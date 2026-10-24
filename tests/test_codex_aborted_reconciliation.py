import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.executors import CodexExecutor, ExecutionRequest
from render_bridge.agent.client import NodeHTTPError
from render_bridge.agent.journal import AgentJournal
from render_bridge.agent.registry import ExecutorRegistry, get_profile
from render_bridge.agent.runner import NodeAgent
from render_bridge.fake import fixture_png


class CodexAbortedImageReconciliationTests(unittest.TestCase):
    THREAD = 'thread-fixture'
    TURN = 'turn-fixture'
    ITEM = 'image-fixture'

    @staticmethod
    def bridge_binding():
        return {'task_digest': 'a' * 64,
                'start_grant_id': 'grant-fixture',
                'output_slot': {'count': 1, 'slot_id': 'primary'},
                'profile_id': 'codex-image', 'profile_revision': '1'}

    def setup_case(self, root, *, width=32, height=32, references=()):
        home = root / 'codex-home'
        output = home / 'generated_images' / self.THREAD / f'{self.ITEM}.png'
        output.parent.mkdir(parents=True)
        output.write_bytes(fixture_png())
        request = ExecutionRequest('task-fixture', 'attempt-fixture', 'frozen prompt',
            {'width': width, 'height': height, 'image_options': {
                'schema_version': '1', 'quality': 'auto', 'output_format': 'png'}},
            tuple(references))
        executor = CodexExecutor(root / 'private' / 'executors')
        attempt = executor.root / request.task_id / request.attempt_id
        attempt.mkdir(parents=True)
        (attempt / 'started').write_bytes(b'Possible external execution; never auto-restart.\n')
        (attempt / 'codex-thread.json').write_text(json.dumps({'thread_id': self.THREAD}), encoding='utf-8')
        (attempt / 'codex-turn.json').write_text(json.dumps({
            'thread_id': self.THREAD, 'turn_id': self.TURN}), encoding='utf-8')
        rows = [
            {'ordinal': 0, 'type': 'session_meta', 'payload': {'id': self.THREAD}},
            {'ordinal': 1, 'type': 'event_msg', 'payload': {
                'type': 'task_started', 'turn_id': self.TURN}},
            {'ordinal': 2, 'type': 'turn_context', 'payload': {'turn_id': self.TURN}},
            {'ordinal': 3, 'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'thread_id': self.THREAD, 'turn_id': self.TURN,
                'item': {'type': 'Extension', 'kind': 'image_gen.generation',
                    'status': 'completed', 'id': self.ITEM, 'savedPath': str(output)}}},
            {'ordinal': 4, 'type': 'event_msg', 'payload': {
                'type': 'turn_aborted', 'turn_id': self.TURN, 'reason': 'interrupted'}},
        ]
        rollout = root / 'rollout.jsonl'
        rollout.write_bytes(b''.join((json.dumps(row, separators=(',', ':')) + '\n').encode()
                                     for row in rows))
        return home, request, executor, rollout, output

    def test_one_bound_aborted_turn_reconciles_original_and_replays_without_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, output = self.setup_case(root)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(result.status, 'ready')
                self.assertTrue(result.details['review_only'])
                self.assertEqual(result.artifact.path.read_bytes(), output.read_bytes())
                evidence_path = executor.root / request.task_id / request.attempt_id / 'codex-aborted-image.json'
                evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
                self.assertEqual(evidence['contract'], 'codex-aborted-image-reconcile/v1')
                self.assertEqual(evidence['original_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
                (evidence_path.parent / 'artifact.json').unlink()
                with patch('render_bridge.executors.codex.StdioSession', side_effect=AssertionError('no CLI')):
                    recovered = executor.recover_completed(request)
                self.assertEqual(recovered.status, 'ready')
                self.assertEqual(recovered.artifact.sha256, result.artifact.sha256)

    def test_actual_dimensions_are_evidence_and_terminal_order_is_mandatory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root, width=64, height=64)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                accepted = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(accepted.status, 'ready')
                evidence = accepted.details['reconciliation']
                self.assertEqual(evidence['dimensions'], {'width': 32, 'height': 32})

                rows = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
                rows[-1], rows[-2] = rows[-2], rows[-1]
                rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
                rejected = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(rejected.status, 'execution_unknown')

    def test_existing_completion_or_terminal_failure_blocks_aborted_reconciliation(self):
        for evidence_name in ('codex-completion.json', 'codex-terminal-failure.json'):
            with self.subTest(evidence=evidence_name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home, request, executor, rollout, _ = self.setup_case(root)
                attempt = executor.root / request.task_id / request.attempt_id
                (attempt / evidence_name).write_text('{}', encoding='utf-8')
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    result = executor.reconcile_interrupted_image(
                        request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(result.status, 'execution_unknown')
                self.assertEqual(result.code,
                    'codex_interrupted_image_conflicting_terminal_evidence')
                self.assertFalse((attempt / 'codex-aborted-image.json').exists())
                self.assertFalse((attempt / 'artifact.original').exists())

    def test_missing_reconciliation_manifest_never_degrades_to_cached_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root)
            attempt = executor.root / request.task_id / request.attempt_id
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                accepted = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(accepted.status, 'ready')
                (attempt / 'codex-aborted-image.json').unlink()
                recovered = executor.recover_completed(request)
            self.assertEqual(recovered.status, 'execution_unknown')
            self.assertEqual(recovered.code, 'codex_interrupted_image_evidence_missing')

    def test_saved_image_extension_must_match_codex_generated_image_contract(self):
        for suffix in ('', '.bmp'):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home, request, executor, rollout, output = self.setup_case(root)
                renamed = output.with_name(self.ITEM + suffix)
                output.rename(renamed)
                rows = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
                rows[3]['payload']['item']['savedPath'] = str(renamed)
                rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    result = executor.reconcile_interrupted_image(
                        request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(result.status, 'execution_unknown')
                self.assertFalse((executor.root / request.task_id / request.attempt_id /
                                  'codex-aborted-image.json').exists())
                self.assertFalse((executor.root / request.task_id / request.attempt_id /
                                  'artifact.original').exists())

    def test_different_thread_turn_or_multiple_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root)
            rows = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
            rows[3]['payload']['item']['id'] = 'different-image'
            rows[3]['payload']['item']['savedPath'] = rows[3]['payload']['item']['savedPath'].replace(
                self.ITEM, 'different-image')
            rows[4]['ordinal'] = 5
            rows.insert(4, {'ordinal': 4, 'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'thread_id': self.THREAD, 'turn_id': self.TURN,
                'item': {'type': 'Extension', 'kind': 'image_gen.generation',
                    'status': 'completed', 'id': self.ITEM,
                    'savedPath': str(root / 'not-used.png')}}})
            rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = executor.reconcile_interrupted_image(request, rollout)
            self.assertEqual(result.status, 'execution_unknown')
            self.assertFalse((executor.root / request.task_id / request.attempt_id /
                              'artifact.original').exists())

    def test_malformed_or_non_event_image_activity_is_not_ignored(self):
        for malformed in (True, False):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home, request, executor, rollout, _ = self.setup_case(root)
                rows = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
                image = {'type': 'Extension', 'kind': 'image_gen.generation',
                    'status': 'completed', 'id': self.ITEM,
                    'savedPath': rows[3]['payload']['item']['savedPath']}
                if malformed:
                    payload = {'type': 'item_completed', 'thread_id': self.THREAD,
                        'turn_id': self.TURN, 'kind': 'image_gen.generation'}
                    record_type = 'event_msg'
                else:
                    payload = {'thread_id': self.THREAD, 'turn_id': self.TURN, 'item': image}
                    record_type = 'response_item'
                rows.insert(3, {'ordinal': 3, 'type': record_type, 'payload': payload})
                for ordinal, row in enumerate(rows):
                    row['ordinal'] = ordinal
                rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    result = executor.reconcile_interrupted_image(
                        request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(result.status, 'execution_unknown')
                self.assertFalse((executor.root / request.task_id / request.attempt_id /
                                  'artifact.original').exists())

    def test_lifecycle_events_with_explicit_wrong_thread_are_rejected(self):
        for event_index in (1, 2, 4):
            with self.subTest(event_index=event_index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home, request, executor, rollout, _ = self.setup_case(root)
                rows = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
                rows[event_index]['payload']['thread_id'] = 'different-thread'
                rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    result = executor.reconcile_interrupted_image(
                        request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(result.status, 'execution_unknown')
                self.assertFalse((executor.root / request.task_id / request.attempt_id /
                                  'artifact.original').exists())

    def test_agent_reconciliation_only_uploads_same_attempt_and_persists_review_proof(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.upload_bytes = None
                self.committed = None
                self.remote_receipt = None
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id,
                    'receipt_id': self.remote_receipt, 'task_digest': 'b' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def create_upload(self, task_id, claim_id, payload):
                self.upload_claim_id = claim_id
                self.payload = payload
                return {'upload_id': 'upload-fixture'}
            def claim(self, task_id, mode):
                self.claim_mode = mode
                return {'claim_id': 'upload-only-claim', 'attempt_id': self.request.attempt_id,
                    'mode': mode}
            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.upload_bytes = content
            def commit(self, upload_id, claim_id):
                self.committed = {'receipt_id': 'receipt-fixture', 'task_id': self.request.task_id,
                    'attempt_id': self.request.attempt_id,
                    'original_sha256': self.payload['original_sha256']}
                return self.committed

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, output = self.setup_case(root)
            client = Client(request)
            registry = ExecutorRegistry({'codex-image': executor})
            agent = NodeAgent(client, root / 'private' / 'agent', registry=registry)
            (agent.journal.root / 'attempts' / request.task_id / request.attempt_id).mkdir(parents=True)
            agent.journal.save({'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                'start_grant_id': 'grant-fixture',
                'profile_id': 'codex-image', 'request': request.payload(),
                'request_sha256': request.digest})
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}), \
                 patch.object(executor, 'execute', side_effect=AssertionError('must not execute')):
                result = agent.reconcile_codex_interrupted(request.task_id, rollout)
            self.assertEqual(result['outcome'], 'artifact_committed')
            self.assertEqual(client.upload_bytes, output.read_bytes())
            self.assertEqual(client.claim_mode, 'upload_only')
            self.assertEqual(client.upload_claim_id, 'upload-only-claim')
            record = agent.journal.get(request.task_id)
            self.assertEqual(record['state'], 'complete')
            self.assertEqual(record['review_only_reconciliation']['contract'],
                             'codex-aborted-image-reconcile/v1')
            self.assertEqual(record['receipt_id'], 'receipt-fixture')

    def test_reference_paths_stay_local_and_shared_reference_binding_matches(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.upload_bytes = None
            def task(self, task_id):
                ref = self.request.references[0]
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id, 'receipt_id': None,
                    'task_digest': 'b' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': [{k: v for k, v in ref.items() if k != 'path'}],
                        'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def claim(self, task_id, mode):
                return {'claim_id': 'upload-only-claim', 'attempt_id': self.request.attempt_id,
                    'mode': mode}
            def create_upload(self, task_id, claim_id, payload):
                self.payload = payload
                return {'upload_id': 'upload-fixture'}
            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.upload_bytes = content
            def commit(self, upload_id, claim_id):
                return {'receipt_id': 'receipt-fixture', 'task_id': self.request.task_id,
                    'attempt_id': self.request.attempt_id,
                    'original_sha256': self.payload['original_sha256']}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref_file = root / 'reference.original'
            ref_file.write_bytes(b'local reference')
            reference = {'input_ref': 'input-fixture', 'role': 'reference',
                'hash': hashlib.sha256(ref_file.read_bytes()).hexdigest(), 'path': str(ref_file)}
            home, request, executor, rollout, output = self.setup_case(root, references=(reference,))
            client = Client(request)
            agent = NodeAgent(client, root / 'private' / 'agent',
                registry=ExecutorRegistry({'codex-image': executor}))
            (agent.journal.root / 'attempts' / request.task_id / request.attempt_id).mkdir(parents=True)
            agent.journal.save({'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                'start_grant_id': 'grant-fixture', 'profile_id': 'codex-image',
                'request': request.payload(), 'request_sha256': request.digest})
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = agent.reconcile_codex_interrupted(request.task_id, rollout)
            self.assertEqual(result['outcome'], 'artifact_committed')
            self.assertEqual(client.upload_bytes, output.read_bytes())
            self.assertNotIn('path', client.task(request.task_id)['request']['references'][0])

    def test_artifact_ready_crash_revalidates_rollout_before_upload(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.upload_bytes = None
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id,
                    'receipt_id': None, 'task_digest': 'a' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def unknown(self, task_id, claim_id):
                self.unknown_called = True
            def create_upload(self, task_id, claim_id, payload):
                self.upload_claim_id = claim_id
                self.payload = payload
                return {'upload_id': 'upload-fixture'}
            def claim(self, task_id, mode):
                self.claim_mode = mode
                return {'claim_id': 'upload-only-claim', 'attempt_id': self.request.attempt_id,
                    'mode': mode}
            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.upload_bytes = content
            def commit(self, upload_id, claim_id):
                return {'receipt_id': 'receipt-fixture', 'task_id': self.request.task_id,
                    'attempt_id': self.request.attempt_id,
                    'original_sha256': self.payload['original_sha256']}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, output = self.setup_case(root)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                recovered = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
            self.assertEqual(recovered.status, 'ready')
            client = Client(request)
            agent = NodeAgent(client, root / 'private' / 'agent',
                registry=ExecutorRegistry({'codex-image': executor}))
            (agent.journal.root / 'attempts' / request.task_id / request.attempt_id).mkdir(parents=True)
            record = {'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                'start_grant_id': 'grant-fixture',
                'profile_id': 'codex-image', 'request': request.payload(),
                'request_sha256': request.digest}
            agent.journal.save(record)
            # Simulate a crash after artifact_ready is durable but before the
            # review-only proof is projected into the Agent journal.
            agent._save_artifact(record, recovered.artifact, get_profile('codex-image'))
            record = agent.journal.get(request.task_id)
            self.assertNotIn('review_only_reconciliation', record)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}), \
                 patch.object(executor, 'execute', side_effect=AssertionError('must not execute')):
                result = agent._execute(request.task_id, None)
            self.assertEqual(result['outcome'], 'artifact_committed')
            self.assertEqual(client.upload_bytes, output.read_bytes())
            self.assertEqual(client.claim_mode, 'upload_only')
            self.assertEqual(client.upload_claim_id, 'upload-only-claim')
            self.assertEqual(agent.journal.get(request.task_id)['review_only_reconciliation'],
                             recovered.details['reconciliation'])

    def test_artifact_ready_with_changed_rollout_stays_unknown_and_does_not_upload(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.upload_bytes = None
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id,
                    'receipt_id': None, 'task_digest': 'a' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def unknown(self, task_id, claim_id):
                self.unknown_called = True
            def claim(self, task_id, mode):
                return {'claim_id': 'upload-only-claim', 'attempt_id': self.request.attempt_id,
                    'mode': mode}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                recovered = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
            client = Client(request)
            agent = NodeAgent(client, root / 'private' / 'agent',
                registry=ExecutorRegistry({'codex-image': executor}))
            (agent.journal.root / 'attempts' / request.task_id / request.attempt_id).mkdir(parents=True)
            record = {'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                'start_grant_id': 'grant-fixture',
                'profile_id': 'codex-image', 'request': request.payload(),
                'request_sha256': request.digest}
            agent.journal.save(record)
            agent._save_artifact(record, recovered.artifact, get_profile('codex-image'))
            rollout.write_bytes(rollout.read_bytes() + b' ')
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = agent._execute(request.task_id, None)
            self.assertEqual(result['outcome'], 'execution_unknown')
            self.assertIsNone(client.upload_bytes)
            self.assertEqual(agent.journal.get(request.task_id)['state'], 'execution_unknown')

    def test_unknown_recovery_rejects_proof_for_a_different_start_grant(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.unknown_called = False
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id,
                    'receipt_id': None, 'task_digest': 'a' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def unknown(self, task_id, claim_id):
                self.unknown_called = True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                accepted = executor.reconcile_interrupted_image(
                    request, rollout, bridge_binding=self.bridge_binding())
            self.assertEqual(accepted.status, 'ready')
            client = Client(request)
            agent = NodeAgent(client, root / 'private' / 'agent',
                registry=ExecutorRegistry({'codex-image': executor}))
            attempt_dir = agent.journal.root / 'attempts' / request.task_id / request.attempt_id
            attempt_dir.mkdir(parents=True)
            agent.journal.save({'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                'start_grant_id': 'different-grant', 'profile_id': 'codex-image',
                'request': request.payload(), 'request_sha256': request.digest})
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = agent._execute(request.task_id, None)
            self.assertEqual(result['outcome'], 'execution_unknown')
            self.assertTrue(client.unknown_called)
            self.assertFalse((attempt_dir / 'result.original').exists())
            self.assertNotIn('artifact', agent.journal.get(request.task_id))

    def test_unknown_recovery_rejects_stale_bridge_task_digest_or_profile_revision(self):
        class Client:
            def __init__(self, request, *, task_digest, profile_revision):
                self.request = request
                self.task_digest = task_digest
                self.profile_revision = profile_revision
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id, 'receipt_id': None,
                    'task_digest': self.task_digest,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': self.profile_revision,
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def unknown(self, task_id, claim_id):
                pass

        for task_digest, revision in (('b' * 64, '1'), ('a' * 64, '2')):
            with self.subTest(task_digest=task_digest[:1], revision=revision), \
                 tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home, request, executor, rollout, _ = self.setup_case(root)
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    accepted = executor.reconcile_interrupted_image(
                        request, rollout, bridge_binding=self.bridge_binding())
                self.assertEqual(accepted.status, 'ready')
                client = Client(request, task_digest=task_digest, profile_revision=revision)
                agent = NodeAgent(client, root / 'private' / 'agent',
                    registry=ExecutorRegistry({'codex-image': executor}))
                attempt_dir = agent.journal.root / 'attempts' / request.task_id / request.attempt_id
                attempt_dir.mkdir(parents=True)
                agent.journal.save({'task_id': request.task_id, 'attempt_id': request.attempt_id,
                    'state': 'execution_unknown', 'claim_id': 'claim-fixture',
                    'start_grant_id': 'grant-fixture', 'profile_id': 'codex-image',
                    'request': request.payload(), 'request_sha256': request.digest})
                with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                    result = agent._execute(request.task_id, None)
                self.assertEqual(result['outcome'], 'execution_unknown')
                self.assertFalse((attempt_dir / 'result.original').exists())

    def test_live_execute_claim_blocks_upload_until_upload_only_claim_is_available(self):
        class Client:
            def __init__(self, request):
                self.request = request
                self.upload_started = False
            def task(self, task_id):
                return {'remote_task_id': task_id, 'state': 'execution_unknown',
                    'attempt_id': self.request.attempt_id,
                    'receipt_id': None, 'task_digest': 'b' * 64,
                    'request': {'prompt': self.request.prompt, 'parameters': self.request.parameters,
                        'references': list(self.request.references), 'execution_profile_id': 'codex-image',
                        'execution_profile_revision': '1',
                        'output_slot': {'count': 1, 'slot_id': 'primary'}}}
            def claim(self, task_id, mode):
                self.requested_mode = mode
                raise NodeHTTPError(409, 'claim_busy')
            def unknown(self, task_id, claim_id):
                pass
            def create_upload(self, *args):
                self.upload_started = True
                raise AssertionError('must not upload with the live execute claim')

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, request, executor, rollout, _ = self.setup_case(root)
            client = Client(request)
            agent = NodeAgent(client, root / 'private' / 'agent',
                registry=ExecutorRegistry({'codex-image': executor}))
            (agent.journal.root / 'attempts' / request.task_id / request.attempt_id).mkdir(parents=True)
            agent.journal.save({'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'state': 'execution_unknown', 'claim_id': 'old-execute-claim',
                'start_grant_id': 'grant-fixture',
                'claim_mode': 'execute', 'profile_id': 'codex-image', 'request': request.payload(),
                'request_sha256': request.digest})
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = agent.reconcile_codex_interrupted(request.task_id, rollout)
            self.assertEqual(result['outcome'], 'upload_pending')
            self.assertEqual(client.requested_mode, 'upload_only')
            self.assertFalse(client.upload_started)
            record = agent.journal.get(request.task_id)
            self.assertEqual(record['claim_id'], 'old-execute-claim')
            self.assertEqual(record['state'], 'artifact_ready')

    def test_lost_commit_response_projects_existing_receipt_into_journal(self):
        class Client:
            def task(self, task_id):
                return {'state': 'artifacts_ready', 'attempt_id': 'attempt-fixture',
                        'receipt_id': 'receipt-server'}
        with tempfile.TemporaryDirectory() as temp:
            agent = NodeAgent(Client(), Path(temp) / 'private' / 'agent',
                registry=ExecutorRegistry({}))
            agent.journal.save({'task_id': 'task-fixture', 'attempt_id': 'attempt-fixture',
                'state': 'artifact_ready', 'receipt_id': None,
                'review_only_reconciliation': {'contract': 'codex-aborted-image-reconcile/v1',
                    'task_id': 'task-fixture', 'attempt_id': 'attempt-fixture',
                    'original_sha256': 'c' * 64, 'review_only': True}})
            result = agent.reconcile_codex_interrupted('task-fixture', Path('unused.jsonl'))
            self.assertEqual(result['outcome'], 'already_committed')
            self.assertEqual(agent.journal.get('task-fixture')['state'], 'complete')
            self.assertEqual(agent.journal.get('task-fixture')['receipt_id'], 'receipt-server')


if __name__ == '__main__':
    unittest.main()
