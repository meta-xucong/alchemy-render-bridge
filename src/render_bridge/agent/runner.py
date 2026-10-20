"""One delivered task, one local attempt. Hatchet remains the only scheduler."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil

from .client import NodeHTTPError
from .journal import AgentJournal, InstanceLock
from render_bridge.executors.base import ExecutionRequest, inspect_image

_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


class NodeAgent:
    def __init__(self, client, state_dir: Path, *, profile='fake-image'):
        self.client = client
        self.journal = AgentJournal(state_dir)
        self.profile = profile

    def execute_task(self, task_id, executor):
        if not isinstance(task_id, str) or not _KEY.fullmatch(task_id):
            raise ValueError('Invalid task identifier')
        with InstanceLock(self.journal.root / 'agent.lock'):
            return self._execute(task_id, executor)

    def _execute(self, task_id, executor):
        task = self.client.task(task_id)
        attempt_id = task['attempt_id']
        if not isinstance(attempt_id, str) or not _KEY.fullmatch(attempt_id):
            raise ValueError('Invalid attempt identifier')
        record = self.journal.get(task_id)
        if record and record['attempt_id'] != attempt_id:
            raise ValueError('Server changed the task attempt')
        if task.get('receipt_id'):
            record = record or {'task_id': task_id, 'attempt_id': attempt_id}
            record.update(state='complete', receipt_id=task['receipt_id'])
            self.journal.save(record)
            return {'outcome': 'already_committed', 'task_id': task_id, 'receipt_id': task['receipt_id']}
        if record and record['state'] == 'complete':
            return {'outcome': 'already_committed', 'task_id': task_id, 'receipt_id': record['receipt_id']}
        if record and record['state'] == 'artifact_ready':
            return self._upload(record)
        if record and record['state'] in ('start_requested', 'execution_unknown'):
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        if task['request']['execution_profile_id'] != self.profile or self.profile != 'fake-image':
            # Real executors are developed offline first; local acceptance enables
            # their matching server profiles in a later explicit change.
            return {'outcome': 'needs_attention', 'code': 'profile_not_enabled', 'task_id': task_id}
        if self.journal.occupied_by_other(task_id):
            return {'outcome': 'physical_slot_busy', 'task_id': task_id}
        if task['state'] in ('running', 'execution_unknown', 'uploading') and not record:
            # Missing local evidence cannot authorize another physical execution.
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        if task['state'] in ('canceled', 'failed', 'expired'):
            return {'outcome': 'not_executable', 'task_id': task_id}
        claim = self.client.claim(task_id, 'execute')
        if claim['attempt_id'] != attempt_id:
            raise ValueError('Claim does not match task attempt')
        record = {'task_id': task_id, 'attempt_id': attempt_id,
                  'claim_id': claim['claim_id'], 'state': 'prepared'}
        work = self.journal.root / 'attempts' / task_id / attempt_id
        work.mkdir(parents=True, exist_ok=True)
        references = []
        for index, reference in enumerate(task['request']['references']):
            content = self.client.input_content(task_id, reference['input_ref'], claim['claim_id'])
            if len(content) > 1_048_576 or hashlib.sha256(content).hexdigest() != reference['hash']:
                raise ValueError('Input reference hash or size does not match')
            path = work / f'input-{index:02d}.original'
            self._save_bytes(path, content)
            references.append({**reference, 'path': str(path)})
        request = ExecutionRequest(task_id, attempt_id, task['request']['prompt'],
                                   task['request']['parameters'], tuple(references))
        record['request_sha256'] = request.digest
        if shutil.disk_usage(work).free < 4 * 1_048_576:
            return {'outcome': 'needs_attention', 'code': 'insufficient_disk', 'task_id': task_id}
        # Persist before crossing the execution boundary. An interrupted response
        # can never cause this attempt to automatically execute again.
        record['state'] = 'start_requested'
        self.journal.save(record)
        try:
            grant = self.client.start(task_id, claim['claim_id'])
        except NodeHTTPError as exc:
            if exc.code in ('physical_slot_busy', 'task_not_executable', 'task_expired', 'claim_expired'):
                record['state'] = 'prepared'
                self.journal.save(record)
                return {'outcome': exc.code, 'task_id': task_id}
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        except Exception:
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        if grant.get('may_execute') is not True or grant.get('attempt_id') != attempt_id:
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        try:
            result = executor.execute(request)
            if result.status != 'ready' or result.artifact is None:
                self._unknown(record)
                return {'outcome': result.status, 'code': result.code, 'task_id': task_id}
            artifact = result.artifact
            content = Path(artifact.path).read_bytes()
            if not 0 < len(content) <= 1_048_576 or hashlib.sha256(content).hexdigest() != artifact.sha256:
                raise ValueError('Executor artifact hash or size does not match')
            saved = work / 'result.original'
            checked = inspect_image(content, provenance=artifact.provenance, path=saved)
            if checked.mime_type != artifact.mime_type:
                raise ValueError('Executor artifact media type does not match')
            self._save_bytes(saved, content)
            record.update(state='artifact_ready', artifact=str(saved.relative_to(self.journal.root)),
                          sha256=checked.sha256, size=checked.size, mime_type=checked.mime_type)
            self.journal.save(record)
        except Exception:
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        return self._upload(record)

    @staticmethod
    def _save_bytes(path, content):
        # A preexisting attempt file must be exactly the same bytes.
        try:
            with path.open('xb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError('Attempt file content changed')

    def _unknown(self, record):
        record['state'] = 'execution_unknown'
        self.journal.save(record)
        try:
            self.client.unknown(record['task_id'], record['claim_id'])
        except Exception:
            pass  # Local latch remains authoritative for preventing a rerun.

    def _upload(self, record):
        path = (self.journal.root / record['artifact']).resolve()
        if not path.is_relative_to(self.journal.root):
            raise ValueError('Journal artifact is outside Agent state directory')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record['sha256'] or len(content) != record['size']:
            raise ValueError('Saved artifact changed; cannot upload')
        payload = {'attempt_id': record['attempt_id'], 'slot_id': 'primary',
                   'original_sha256': record['sha256'], 'size': record['size'], 'mime_type': record['mime_type']}
        try:
            try:
                upload = self.client.create_upload(record['task_id'], record['claim_id'], payload)
            except NodeHTTPError as exc:
                if exc.code not in ('claim_expired', 'stale_claim'):
                    raise
                claim = self.client.claim(record['task_id'], 'upload_only')
                if claim['attempt_id'] != record['attempt_id']:
                    raise ValueError('Recovery changed attempt identity')
                record['claim_id'] = claim['claim_id']
                self.journal.save(record)
                upload = self.client.create_upload(record['task_id'], record['claim_id'], payload)
            record['upload_id'] = upload['upload_id']
            self.journal.save(record)
            self.client.put_upload(upload['upload_id'], record['claim_id'], content, record['mime_type'])
            receipt = self.client.commit(upload['upload_id'], record['claim_id'])
            if receipt['attempt_id'] != record['attempt_id'] or receipt['original_sha256'] != record['sha256']:
                raise ValueError('Receipt does not match saved artifact')
            record.update(state='complete', receipt_id=receipt['receipt_id'])
            self.journal.save(record)
            return {'outcome': 'artifact_committed', 'task_id': record['task_id'], 'receipt_id': receipt['receipt_id']}
        except Exception:
            # Known bytes are retained; subsequent delivery retries only upload.
            return {'outcome': 'upload_pending', 'task_id': record['task_id']}
