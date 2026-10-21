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
from render_bridge.profiles import get_profile
from render_bridge.image_options import validate_image_request, requested_output_mime
from .registry import ExecutorRegistry

_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


class NodeAgent:
    def __init__(self, client, state_dir: Path, *, profile='fake-image', registry=None):
        self.client = client
        self.journal = AgentJournal(state_dir)
        self.profile = profile
        self.registry = registry or ExecutorRegistry.from_config(self.journal.root / 'executors')

    def execute_task(self, task_id, executor=None):
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
        if task['state'] in ('canceled', 'failed', 'expired'):
            return {'outcome': 'not_executable', 'task_id': task_id}
        profile_id = task['request']['execution_profile_id']
        validate_image_request(task['request']['parameters'], task['request']['references'], profile_id)
        if record and record['state'] == 'artifact_ready':
            return self._upload(record)
        profile = get_profile(profile_id)
        # Legacy explicit injection is limited to the configured legacy profile.
        executor = executor if executor is not None and profile_id == self.profile else self.registry.get(profile_id)
        if executor is None:
            return {'outcome': 'needs_attention', 'code': 'profile_not_enabled', 'task_id': task_id}
        if record and record['state'] in ('start_requested', 'execution_unknown'):
            # Recovery uses completed evidence only, never execute/preflight.
            if hasattr(executor, 'recover_completed') and record.get('request'):
                try:
                    recovered = executor.recover_completed(ExecutionRequest(**record['request']))
                    if recovered is not None:
                        if recovered.status == 'ready' and recovered.artifact is not None:
                            self._save_artifact(record, recovered.artifact, profile)
                            return self._upload(record)
                        self._unknown(record)
                        return {'outcome': recovered.status, 'code': recovered.code, 'task_id': task_id}
                except Exception:
                    pass  # Incomplete evidence must not authorize another turn.
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        if record and record['state'] == 'manual_prepared' and task['state'] == 'awaiting_user':
            record['state'] = 'awaiting_user'
            self.journal.save(record)
        if record and record['state'] == 'awaiting_user':
            return {'outcome': 'awaiting_user', 'task_id': task_id, **record['manual_details']}
        if profile.execution_mode == 'automatic' and self.journal.occupied_by_other(task_id):
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
        record['request'] = request.payload()
        record['profile_id'] = profile_id
        if shutil.disk_usage(work).free < max(4 * 1_048_576, 2 * profile.max_output_bytes):
            return {'outcome': 'needs_attention', 'code': 'insufficient_disk', 'task_id': task_id}
        if hasattr(executor, 'preflight'):
            blocked = executor.preflight(request)
            if blocked is not None:
                return {'outcome': blocked.status, 'code': blocked.code, 'task_id': task_id}
        if profile.execution_mode == 'manual':
            result = executor.execute(request)
            if result.code != 'awaiting_user_submission':
                return {'outcome': result.status, 'code': result.code, 'task_id': task_id}
            record.update(state='manual_prepared', manual_details=result.details)
            self.journal.save(record)
            grant = self.client.manual_ready(task_id, claim['claim_id'])
            if grant.get('attempt_id') != attempt_id or grant.get('state') != 'awaiting_user':
                raise ValueError('Manual grant does not match attempt')
            record.update(state='awaiting_user', manual_details=result.details)
            self.journal.save(record)
            return {'outcome': 'awaiting_user', 'task_id': task_id, **result.details}
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
            self._save_artifact(record, result.artifact, profile)
        except Exception:
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': task_id}
        return self._upload(record)

    def _save_artifact(self, record, artifact, profile):
        content = Path(artifact.path).read_bytes()
        if not 0 < len(content) <= profile.max_output_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ValueError('Executor artifact hash or size does not match')
        saved = self.journal.root / 'attempts' / record['task_id'] / record['attempt_id'] / 'result.original'
        checked = inspect_image(content, provenance=artifact.provenance, path=saved)
        if (checked.mime_type != artifact.mime_type or checked.width * checked.height > profile.max_pixels
                or max(checked.width, checked.height) > profile.max_dimension):
            raise ValueError('Executor artifact does not match profile limits')
        expected_mime = requested_output_mime(record['request']['parameters'])
        if expected_mime and checked.mime_type != expected_mime:
            raise ValueError('Executor original does not match requested output format')
        self._save_bytes(saved, content)
        record.update(state='artifact_ready', artifact=str(saved.relative_to(self.journal.root)),
                      sha256=checked.sha256, size=checked.size, mime_type=checked.mime_type)
        self.journal.save(record)

    def manual_import(self, task_id, source, *, confirmed_request_sha256, confirmed_single_original):
        if not isinstance(task_id, str) or not _KEY.fullmatch(task_id):
            raise ValueError('Invalid task identifier')
        with InstanceLock(self.journal.root / 'agent.lock'):
            task = self.client.task(task_id)
            record = self.journal.get(task_id)
            if not record or record['attempt_id'] != task['attempt_id']:
                raise ValueError('Matching exported local attempt required')
            if task.get('receipt_id'):
                return {'outcome': 'already_committed', 'task_id': task_id, 'receipt_id': task['receipt_id']}
            if task['state'] in ('canceled', 'failed', 'expired'):
                return {'outcome': 'not_executable', 'task_id': task_id}
            if record.get('profile_id') != 'midjourney-manual':
                raise ValueError('Task is not a manual profile')
            if confirmed_request_sha256 != record['request_sha256'] or confirmed_single_original is not True:
                raise ValueError('Explicit matching task and single-original confirmation required')
            if record['state'] == 'artifact_ready':
                return self._upload(record)
            if record['state'] != 'awaiting_user':
                raise ValueError('Task is not awaiting manual import')
            executor = self.registry.get(record['profile_id'])
            if executor is None:
                return {'outcome': 'needs_attention', 'code': 'profile_not_enabled', 'task_id': task_id}
            request = ExecutionRequest(**record['request'])
            result = executor.import_original(request, Path(source),
                confirmed_request_sha256=confirmed_request_sha256,
                confirmed_single_original=confirmed_single_original)
            if result.status != 'ready' or result.artifact is None:
                return {'outcome': result.status, 'code': result.code, 'task_id': task_id}
            self._save_artifact(record, result.artifact, get_profile(record['profile_id']))
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
        expected_mime = requested_output_mime(record['request']['parameters'])
        checked = inspect_image(content, provenance='agent_saved_original', path=path)
        if expected_mime and checked.mime_type != expected_mime:
            return {'outcome': 'needs_attention', 'code': 'output_format_mismatch', 'task_id': record['task_id']}
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
        except NodeHTTPError as exc:
            if exc.code == 'output_format_mismatch':
                return {'outcome': 'needs_attention', 'code': exc.code, 'task_id': record['task_id']}
            if exc.code in ('task_not_executable', 'task_not_accepting', 'task_expired', 'task_canceled', 'task_cancelled'):
                return {'outcome': 'not_executable', 'code': exc.code, 'task_id': record['task_id']}
            return {'outcome': 'upload_pending', 'code': exc.code, 'task_id': record['task_id']}
        except Exception:
            # Known bytes are retained; subsequent delivery retries only upload.
            return {'outcome': 'upload_pending', 'task_id': record['task_id']}
