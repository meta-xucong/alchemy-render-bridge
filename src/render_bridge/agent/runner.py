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


def _bridge_reference_payloads(references):
    if not isinstance(references, (list, tuple)):
        return None
    result = []
    for reference in references:
        if not isinstance(reference, dict):
            return None
        result.append({key: value for key, value in reference.items() if key != 'path'})
    return result


class NodeAgent:
    def __init__(self, client, state_dir: Path, *, profile='fake-image', registry=None):
        self.client = client
        self.journal = AgentJournal(state_dir)
        self._active = False
        self._worker_running = False
        self._current_attempt = None
        self.profile = profile
        self.registry = registry or ExecutorRegistry.from_config(self.journal.root / 'executors')
        from .health import ProfileHealthReporter
        self.health_reporter = ProfileHealthReporter(self.journal, self.registry) if self.registry.health_reporting_enabled else None

    def execute_task(self, task_id, executor=None):
        if not isinstance(task_id, str) or not _KEY.fullmatch(task_id):
            raise ValueError('Invalid task identifier')
        with InstanceLock(self.journal.root / 'agent.lock'):
            self._retry_terminal_reports()
            self._active = True
            self._current_attempt = None
            try:
                self.heartbeat()
                result = self._execute(task_id, executor)
                if result.get('outcome') in ('needs_attention', 'execution_unknown') and self._current_attempt and hasattr(self.client, 'attention'):
                    from render_bridge.readiness import ATTENTION_MESSAGES
                    code = result.get('code', 'execution_requires_reconciliation')
                    if code not in ATTENTION_MESSAGES:
                        code = 'execution_requires_reconciliation'
                    try:
                        self.client.attention(task_id, self._current_attempt, code)
                        result['attention_reported'] = True
                    except Exception:
                        # Report failure is visible locally; never restart a renderer.
                        result['attention_reported'] = False
                return result
            finally:
                self._active = False
                self.heartbeat()

    def heartbeat(self):
        """Best-effort liveness plus explicitly enabled, read-only local health probes."""
        if not self._active:
            try:
                with InstanceLock(self.journal.root / 'agent.lock'):
                    self._retry_terminal_reports()
            except (OSError, RuntimeError):
                pass  # Another task owns the instance lock; it reconciles first.
        if not hasattr(self.client, 'heartbeat'):
            return False
        try:
            profiles = sorted(self.registry.executors)
            state = ('busy' if self._active or self.journal.occupied_by_other('')
                     else 'available' if self._worker_running and any(p != 'codex-image' for p in profiles) else 'attention')
            if self.health_reporter is None:
                self.client.heartbeat(state, profiles)
            else:
                self.client.heartbeat(state, profiles, profile_health=self.health_reporter.reports())
            return True
        except Exception:
            return False


    def _execute(self, task_id, executor):
        task = self.client.task(task_id)
        attempt_id = task['attempt_id']
        self._current_attempt = attempt_id
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
        if record and record['state'] == 'failure_report_pending':
            return self._report_terminal_failure(record)
        if record and record['state'] == 'terminal_failed':
            return {'outcome': 'terminal_failed', 'task_id': task_id}
        if task['state'] in ('canceled', 'failed', 'expired'):
            return {'outcome': 'not_executable', 'task_id': task_id}
        profile_id = task['request']['execution_profile_id']
        validate_image_request(task['request']['parameters'], task['request']['references'], profile_id)
        if record and record['state'] == 'artifact_ready':
            if profile_id == 'codex-image' and not self._revalidate_codex_artifact(record, task):
                return {'outcome': 'execution_unknown', 'task_id': task_id}
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
                        if recovered.status == 'terminal_failure':
                            return self._save_terminal_failure(record, recovered)
                        if recovered.status == 'ready' and recovered.artifact is not None:
                            proof = recovered.details.get('reconciliation')
                            if proof is not None:
                                proof_field = ('review_only_reconciliation' if proof.get('review_only') is True
                                               else 'completed_rollout_reconciliation')
                                if (not self._codex_rollout_proof_matches(
                                            record, proof, recovered.artifact, task)
                                        or (record.get(proof_field) is not None
                                            and record[proof_field] != proof)):
                                    self._unknown(record)
                                    return {'outcome': 'execution_unknown', 'task_id': task_id}
                                record[proof_field] = proof
                                self.journal.save(record)
                            elif (record.get('review_only_reconciliation') is not None
                                  or record.get('completed_rollout_reconciliation') is not None
                                  or record.get('artifact_provenance') in {
                                      'codex_aborted_turn_review_only',
                                      'codex_completed_rollout_reconciled'}):
                                self._unknown(record)
                                return {'outcome': 'execution_unknown', 'task_id': task_id}
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
        if profile_id == 'codex-image':
            if (not isinstance(grant.get('start_grant_id'), str)
                    or not grant['start_grant_id']):
                self._unknown(record)
                return {'outcome': 'execution_unknown', 'task_id': task_id}
            record['start_grant_id'] = grant['start_grant_id']
            self.journal.save(record)
        try:
            result = executor.execute(request)
            if self.health_reporter is not None:
                self.health_reporter.record_result(profile_id, result, attempt_id)
            if result.status == 'terminal_failure':
                return self._save_terminal_failure(record, result)
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
        expected_mime = requested_output_mime(record['request']['parameters'], profile.id)
        if expected_mime and checked.mime_type != expected_mime:
            raise ValueError('Executor original does not match requested output format')
        self._save_bytes(saved, content)
        record.update(state='artifact_ready', artifact=str(saved.relative_to(self.journal.root)),
                      sha256=checked.sha256, size=checked.size, mime_type=checked.mime_type,
                      artifact_provenance=artifact.provenance)
        self.journal.save(record)

    def _codex_rollout_proof_matches(self, record, proof, artifact, task):
        if not isinstance(proof, dict) or not isinstance(task, dict):
            return False
        binding = proof.get('bridge_binding')
        server_request = task.get('request') or {}
        try:
            request = ExecutionRequest(**record['request'])
        except (KeyError, TypeError, ValueError):
            return False
        task_digest = task.get('task_digest')
        profile_revision = server_request.get('execution_profile_revision')
        contract = proof.get('contract')
        terminal_matches = ((contract == 'codex-aborted-image-reconcile/v1'
                             and proof.get('terminal_event') == 'turn_aborted'
                             and proof.get('terminal_reason') == 'interrupted'
                             and proof.get('review_only') is True)
                            or (contract == 'codex-completed-rollout-reconcile/v1'
                                and proof.get('terminal_event') == 'task_complete'
                                and 'terminal_reason' not in proof
                                and proof.get('review_only') is False))
        return bool(
            isinstance(binding, dict)
            and terminal_matches
            and proof.get('task_id') == record.get('task_id') == task.get('remote_task_id')
            and proof.get('attempt_id') == record.get('attempt_id') == task.get('attempt_id')
            and proof.get('request_sha256') == request.digest == record.get('request_sha256')
            and proof.get('original_sha256') == artifact.sha256
            and binding.get('task_digest') == task_digest
            and isinstance(task_digest, str) and len(task_digest) == 64
            and all(char in '0123456789abcdef' for char in task_digest)
            and binding.get('profile_id') == server_request.get('execution_profile_id') == 'codex-image'
            and binding.get('profile_revision') == profile_revision == get_profile('codex-image').revision
            and binding.get('output_slot') == server_request.get('output_slot')
            == {'count': 1, 'slot_id': 'primary'}
            and binding.get('start_grant_id') == record.get('start_grant_id')
            and isinstance(record.get('start_grant_id'), str) and bool(record['start_grant_id'])
            and server_request.get('prompt') == request.prompt
            and server_request.get('parameters') == request.parameters
            and server_request.get('references', []) == _bridge_reference_payloads(request.references)
        )

    def _revalidate_codex_artifact(self, record, task=None):
        """Recheck Codex evidence before replaying an artifact_ready upload."""
        executor = self.registry.get('codex-image')
        if executor is None or not hasattr(executor, 'recover_completed'):
            self._unknown(record)
            return False
        try:
            task = task or self.client.task(record['task_id'])
            recovered = executor.recover_completed(ExecutionRequest(**record['request']))
        except Exception:
            task = None
            recovered = None
        artifact = getattr(recovered, 'artifact', None)
        if (getattr(recovered, 'status', None) != 'ready' or artifact is None
                or artifact.sha256 != record.get('sha256')
                or artifact.size != record.get('size')
                or artifact.mime_type != record.get('mime_type')):
            self._unknown(record)
            return False
        try:
            checked = inspect_image(Path(artifact.path).read_bytes(),
                                    provenance=artifact.provenance,
                                    path=Path(artifact.path))
            profile = get_profile('codex-image')
        except Exception:
            self._unknown(record)
            return False
        if (checked.sha256 != record.get('sha256') or checked.size != record.get('size')
                or checked.mime_type != record.get('mime_type')
                or checked.width * checked.height > profile.max_pixels
                or max(checked.width, checked.height) > profile.max_dimension):
            self._unknown(record)
            return False
        proof = recovered.details.get('reconciliation')
        if proof is not None:
            proof_field = ('review_only_reconciliation' if proof.get('review_only') is True
                           else 'completed_rollout_reconciliation')
            if (not self._codex_rollout_proof_matches(record, proof, artifact, task)
                    or proof.get('original_sha256') != record.get('sha256')
                    or (record.get(proof_field) is not None
                        and record[proof_field] != proof)):
                self._unknown(record)
                return False
            record[proof_field] = proof
        elif (record.get('review_only_reconciliation') is not None
              or record.get('completed_rollout_reconciliation') is not None
              or record.get('artifact_provenance') in {
                  'codex_aborted_turn_review_only',
                  'codex_completed_rollout_reconciled'}):
            self._unknown(record)
            return False
        record['artifact_provenance'] = artifact.provenance
        self.journal.save(record)
        return True

    def manual_notify(self, task_id, notifier, *, confirmed_request_sha256):
        """Explicit ordinary task notice; never changes generation/attempt state."""
        if not isinstance(task_id, str) or not _KEY.fullmatch(task_id):
            raise ValueError('Invalid task identifier')
        with InstanceLock(self.journal.root / 'agent.lock'):
            task = self.client.task(task_id)
            record = self.journal.get(task_id)
            if not record or record['attempt_id'] != task['attempt_id']:
                raise ValueError('Matching exported local attempt required')
            if record.get('profile_id') != 'midjourney-manual':
                raise ValueError('Task is not a manual profile')
            if confirmed_request_sha256 != record['request_sha256']:
                raise ValueError('Explicit matching task confirmation required')
            if task.get('receipt_id') or task['state'] in ('canceled', 'failed', 'expired'):
                return {'outcome': 'not_executable', 'task_id': task_id}
            if record['state'] != 'awaiting_user' or task['state'] != 'awaiting_user':
                raise ValueError('Task is not awaiting manual submission/import')
            if self.registry.get('midjourney-manual') is None:
                return {'outcome': 'needs_attention', 'code': 'profile_not_enabled', 'task_id': task_id}
            request = ExecutionRequest(**record['request'])
            if request.digest != confirmed_request_sha256:
                raise ValueError('Saved request no longer matches its confirmation')
            return {'task_id': task_id, **notifier.send(request, self.journal, record)}

    def reconcile_codex_interrupted(self, task_id, rollout_path):
        return self._reconcile_codex_rollout(
            task_id, rollout_path, executor_method='reconcile_interrupted_image',
            contract='codex-aborted-image-reconcile/v1',
            proof_field='review_only_reconciliation', review_only=True)

    def reconcile_codex_completed(self, task_id, rollout_path):
        return self._reconcile_codex_rollout(
            task_id, rollout_path, executor_method='reconcile_completed_rollout_image',
            contract='codex-completed-rollout-reconcile/v1',
            proof_field='completed_rollout_reconciliation', review_only=False)

    def _reconcile_codex_rollout(self, task_id, rollout_path, *, executor_method,
                                 contract, proof_field, review_only):
        """Bind one explicit terminal Codex rollout image to its existing attempt.

        This operator path can only verify local evidence and upload the same
        attempt's original. It never calls executor.execute or starts a turn.
        """
        if not isinstance(task_id, str) or not _KEY.fullmatch(task_id):
            raise ValueError('Invalid task identifier')
        with InstanceLock(self.journal.root / 'agent.lock'):
            task = self.client.task(task_id)
            record = self.journal.get(task_id)
            if not record or record.get('attempt_id') != task.get('attempt_id'):
                raise ValueError('Matching exported local attempt required')
            if task.get('receipt_id'):
                record.update(state='complete', receipt_id=task['receipt_id'])
                self.journal.save(record)
                return {'outcome': 'already_committed', 'task_id': task_id,
                        'receipt_id': task['receipt_id']}
            if record.get('state') == 'artifact_ready':
                if not self._revalidate_codex_artifact(record, task):
                    return {'outcome': 'execution_unknown', 'task_id': task_id}
                proof = record.get(proof_field)
                if (not proof or proof.get('contract') != contract
                        or proof.get('original_sha256') != record.get('sha256')
                        or proof.get('attempt_id') != record.get('attempt_id')
                        or proof.get('task_id') != task_id or proof.get('review_only') is not review_only):
                    self._unknown(record)
                    return {'outcome': 'execution_unknown', 'task_id': task_id}
                return self._upload(record)
            if (task.get('state') != 'execution_unknown'
                    or record.get('state') not in ('start_requested', 'execution_unknown')
                    or record.get('profile_id') != 'codex-image'
                    or not record.get('claim_id')
                    or not isinstance(record.get('start_grant_id'), str)
                    or not record['start_grant_id']):
                raise ValueError('Only a started unknown Codex attempt can be reconciled')
            attempt_work = self.journal.root / 'attempts' / task_id / record['attempt_id']
            if not attempt_work.is_dir():
                raise ValueError('Original Agent attempt directory is missing')
            request = ExecutionRequest(**record['request'])
            if (request.task_id != task_id or request.attempt_id != record['attempt_id']
                    or request.digest != record.get('request_sha256')):
                raise ValueError('Frozen request does not match the original attempt')
            server_request = task.get('request') or {}
            if (server_request.get('execution_profile_id') != 'codex-image'
                    or server_request.get('prompt') != request.prompt
                    or server_request.get('parameters') != request.parameters
                    or server_request.get('references', []) != _bridge_reference_payloads(request.references)
                    or server_request.get('output_slot') != {'count': 1, 'slot_id': 'primary'}):
                raise ValueError('Bridge frozen request differs from the original Agent request')
            task_digest = task.get('task_digest')
            profile_revision = server_request.get('execution_profile_revision')
            if (not isinstance(task_digest, str)
                    or profile_revision != get_profile('codex-image').revision):
                raise ValueError('Bridge task digest/profile revision binding is missing')
            executor = self.registry.get('codex-image')
            if executor is None or not hasattr(executor, executor_method):
                return {'outcome': 'needs_attention', 'code': 'codex_profile_not_enabled',
                        'task_id': task_id}
            result = getattr(executor, executor_method)(request, Path(rollout_path),
                bridge_binding={'task_digest': task_digest,
                    'start_grant_id': record['start_grant_id'],
                    'output_slot': {'count': 1, 'slot_id': 'primary'},
                    'profile_id': 'codex-image', 'profile_revision': profile_revision})
            if result.status != 'ready' or result.artifact is None:
                return {'outcome': result.status, 'code': result.code, 'task_id': task_id}
            proof = result.details.get('reconciliation')
            if (not self._codex_rollout_proof_matches(record, proof, result.artifact, task)
                    or proof.get('contract') != contract or proof.get('review_only') is not review_only):
                self._unknown(record)
                return {'outcome': 'execution_unknown', 'task_id': task_id}
            record[proof_field] = proof
            self.journal.save(record)
            self._save_artifact(record, result.artifact, get_profile('codex-image'))
            return self._upload(record)

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

    def _save_terminal_failure(self, record, result):
        record.update(state='failure_report_pending', terminal_failure=result.details['terminal_failure'])
        self.journal.save(record)
        return self._report_terminal_failure(record)

    def _report_terminal_failure(self, record):
        try:
            confirmation = self.client.terminal_failure(record['task_id'], record['attempt_id'], record['terminal_failure'])
            if (confirmation.get('status') != 'failed' or confirmation.get('task_id') != record['task_id']
                    or confirmation.get('attempt_id') != record['attempt_id']
                    or confirmation.get('evidence') != record['terminal_failure']
                    or not isinstance(confirmation.get('terminal_failure_ref'), str)
                    or not confirmation['terminal_failure_ref']):
                raise ValueError('Terminal confirmation binding mismatch')
            record.update(state='terminal_failed', terminal_failure_ref=confirmation['terminal_failure_ref'])
            self.journal.save(record)
            return {'outcome': 'terminal_failed', 'task_id': record['task_id']}
        except Exception:
            return {'outcome': 'failure_report_pending', 'task_id': record['task_id']}

    def _retry_terminal_reports(self):
        # Only reconcile existing execution evidence. Never dispatch, preflight,
        # open a turn, or release a genuinely unknown execution from health alone.
        for record in self.journal.terminal_recovery_records():
            try:
                if record['state'] == 'failure_report_pending':
                    self._report_terminal_failure(record)
                    continue
                executor = self.registry.get(record.get('profile_id'))
                if hasattr(executor, 'recover_terminal_failure') and record.get('request'):
                    result = executor.recover_terminal_failure(ExecutionRequest(**record['request']))
                    if result is not None:
                        if self.health_reporter is not None:
                            self.health_reporter.record_result(record['profile_id'], result, record['attempt_id'])
                        self._save_terminal_failure(record, result)
            except Exception:
                pass  # Incomplete local evidence remains occupied and unknown.

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
        expected_mime = requested_output_mime(record['request']['parameters'],
            record['profile_id'])
        checked = inspect_image(content, provenance='agent_saved_original', path=path)
        if expected_mime and checked.mime_type != expected_mime:
            return {'outcome': 'needs_attention', 'code': 'output_format_mismatch', 'task_id': record['task_id']}
        payload = {'attempt_id': record['attempt_id'], 'slot_id': 'primary',
                   'original_sha256': record['sha256'], 'size': record['size'], 'mime_type': record['mime_type']}
        aborted_artifact = record.get('artifact_provenance') == 'codex_aborted_turn_review_only'
        completed_rollout_artifact = (record.get('artifact_provenance')
                                      == 'codex_completed_rollout_reconciled')
        rollout_artifact = aborted_artifact or completed_rollout_artifact
        proof = (record.get('review_only_reconciliation') if aborted_artifact
                 else record.get('completed_rollout_reconciliation') if completed_rollout_artifact
                 else None)
        expected_contract = ('codex-aborted-image-reconcile/v1' if aborted_artifact
                             else 'codex-completed-rollout-reconcile/v1')
        expected_review_only = aborted_artifact
        if rollout_artifact and (not isinstance(proof, dict)
                or proof.get('contract') != expected_contract
                or proof.get('task_id') != record.get('task_id')
                or proof.get('attempt_id') != record.get('attempt_id')
                or proof.get('original_sha256') != record.get('sha256')
                or proof.get('review_only') is not expected_review_only):
            self._unknown(record)
            return {'outcome': 'execution_unknown', 'task_id': record['task_id']}
        require_upload_only = rollout_artifact
        if require_upload_only:
            try:
                claim = self.client.claim(record['task_id'], 'upload_only')
            except Exception as exc:
                return {'outcome': 'upload_pending',
                        'code': getattr(exc, 'code', 'upload_only_claim_unavailable'),
                        'task_id': record['task_id']}
            if (claim.get('mode') != 'upload_only'
                    or claim.get('attempt_id') != record['attempt_id']
                    or not isinstance(claim.get('claim_id'), str) or not claim['claim_id']):
                self._unknown(record)
                return {'outcome': 'execution_unknown', 'task_id': record['task_id']}
            record.update(claim_id=claim['claim_id'], claim_mode='upload_only')
            self.journal.save(record)
        try:
            try:
                upload = self.client.create_upload(record['task_id'], record['claim_id'], payload)
            except NodeHTTPError as exc:
                if exc.code not in ('claim_expired', 'stale_claim'):
                    raise
                claim = self.client.claim(record['task_id'], 'upload_only')
                if (claim['attempt_id'] != record['attempt_id']
                        or (require_upload_only and claim.get('mode') != 'upload_only')
                        or not isinstance(claim.get('claim_id'), str)
                        or not claim['claim_id']):
                    raise ValueError('Recovery changed attempt identity')
                record['claim_id'] = claim['claim_id']
                if require_upload_only:
                    record['claim_mode'] = 'upload_only'
                self.journal.save(record)
                upload = self.client.create_upload(record['task_id'], record['claim_id'], payload)
            record['upload_id'] = upload['upload_id']
            self.journal.save(record)
            self.client.put_upload(upload['upload_id'], record['claim_id'], content, record['mime_type'])
            receipt = self.client.commit(upload['upload_id'], record['claim_id'])
            if (receipt.get('task_id') != record['task_id']
                    or receipt.get('attempt_id') != record['attempt_id']
                    or receipt.get('original_sha256') != record['sha256']):
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
