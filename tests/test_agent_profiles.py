"""Profile routing and human round-trip. No login, external turn, or Discord."""
import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from render_bridge.agent import HttpNodeClient, NodeAgent
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.executors.codex import CodexConfig, CodexExecutor
from render_bridge.executors.fake import FakeExecutor
from render_bridge.executors.manual import MidjourneyManualExecutor
from render_bridge.fake import fixture_png, fixture_jpeg
from render_bridge.executors.base import AttemptFiles
from render_bridge.node_api import NodeScope
from render_bridge.runtime import create_runtime_app


class AgentProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).timestamp()
        self.scope = SourceScope('source', 'workspace')
        self.store = BridgeStore(self.root / 'ledger.db', self.root / 'blobs', clock=lambda: self.now,
            enabled_profiles=['fake-image', 'codex-image', 'midjourney-manual'])
        app = create_runtime_app(store=self.store, source_tokens={'source': self.scope},
            node_tokens={'node': NodeScope('source', 'workspace', 'node', 'instance')},
            dispatch_tokens={'dispatch': self.scope})
        self.client = HttpNodeClient('http://127.0.0.1:8787', 'node')
        self.client.close()
        self.client.http = TestClient(app, base_url='http://127.0.0.1:8787', headers={'Authorization': 'Bearer node'})
        self.state = self.root / 'agent'
        self.registry = ExecutorRegistry({'fake-image': FakeExecutor(self.state / 'executors'),
            'midjourney-manual': MidjourneyManualExecutor(self.state / 'executors')})
        self.agent = NodeAgent(self.client, self.state, registry=self.registry)

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.temp.cleanup()

    def create(self, profile, origin='task', image_options=None):
        return self.store.create_task(self.scope, {'schema_version': '1', 'origin_key': origin,
            'binding_id': 'binding', 'operation': 'image.generate', 'execution_profile_id': profile,
            'execution_profile_revision': '1', 'prompt': '冻结提示词 中文',
            'parameters': {'width': 32, 'height': 24, 'seed': 1,
                **({'image_options': image_options} if image_options is not None else {})}, 'references': [],
            'output_slot': {'slot_id': 'primary', 'count': 1}, 'routing': 'bridge_only',
            'deadline': (datetime.fromtimestamp(self.now, timezone.utc) + timedelta(hours=2)).isoformat()
        })['remote_task_id']

    def test_invalid_frozen_options_are_rejected_before_claim(self):
        task = self.create('fake-image')
        snapshot = self.client.task(task)
        snapshot['request']['parameters']['image_options'] = {
            'schema_version': '1', 'quality': 'high', 'output_format': 'png'}
        with patch.object(self.client, 'task', return_value=snapshot), \
             patch.object(self.client, 'claim') as claim, patch.object(self.client, 'start') as start:
            with self.assertRaises(ValueError):
                self.agent.execute_task(task)
            claim.assert_not_called()
            start.assert_not_called()

    def test_agent_recovers_completed_codex_original_without_second_execution(self):
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                self._completed_codex_recovery(manifest=manifest)

    def test_agent_mismatch_retains_original_and_replays_attention(self):
        self._completed_codex_recovery(manifest=True, mismatch=True)

    def _completed_codex_recovery(self, *, manifest, mismatch=False):
        executor = CodexExecutor(self.state / 'executors', CodexConfig(enabled=True,
            model='mock-model', accepted_cli_version='codex-cli 0.163.0', image_capability_verified=True,
            tool_policy_verified=True, approval_policy='on-request'))
        self.registry.executors['codex-image'] = executor
        task = self.create('codex-image', 'recovery-'+str(manifest)+str(mismatch), {
            'schema_version': '1', 'quality': 'auto', 'output_format': 'jpeg'})
        expected = (fixture_png if mismatch else fixture_jpeg)(32, 24)
        def completed(request):
            files = AttemptFiles(executor.root, request, 'codex-cli')
            files.begin()
            original = files.path / 'original.image'
            original.write_bytes(expected)
            evidence = {'thread_id': 'thread', 'turn_id': 'turn', 'events': [
                {'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn', 'item': {
                    'id': 'image', 'type': 'imageGeneration', 'status': 'completed', 'savedPath': str(original)}}},
                {'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}}]}
            (files.path / 'codex-completion.json').write_text(json.dumps(evidence))
            if manifest:
                result = executor.collect_completed(request, **evidence)
                if mismatch:
                    return result
            raise RuntimeError('Simulated interruption before Agent saves the completed result')
        with patch('render_bridge.executors.codex.probe_codex', return_value={
                'cli_version': 'codex-cli 0.163.0', 'schema_version_matches': True, 'stdio_advertised': True}) as probe, \
             patch.object(executor, 'execute', side_effect=completed) as execute, \
             patch.object(self.client, 'start', wraps=self.client.start) as start, \
             patch('render_bridge.executors.codex.StdioSession', side_effect=AssertionError('unexpected turn')):
            result = self.agent.execute_task(task)
            self.assertEqual(result['outcome'], 'needs_attention' if mismatch else 'execution_unknown')
            self.now += 3601  # The fixed node claim lease expires before upload-only recovery.
            reopened = NodeAgent(self.client, self.state, registry=self.registry)
            recovered = reopened.execute_task(task)
            self.assertEqual(recovered['outcome'], 'needs_attention' if mismatch else 'artifact_committed')
            if mismatch:
                self.assertEqual(result['code'], 'output_format_mismatch')
                self.assertEqual(recovered['code'], 'output_format_mismatch')
                self.assertIsNone(self.client.task(task)['receipt_id'])
            else:
                self.assertEqual(reopened.execute_task(task)['outcome'], 'already_committed')
            execute.assert_called_once()
            probe.assert_called_once()
            start.assert_called_once()
        attempt = self.client.task(task)['attempt_id']
        self.assertEqual((executor.root / task / attempt / 'artifact.original').read_bytes(), expected)

    def test_default_registry_remains_fake_only(self):
        agent = NodeAgent(self.client, self.state)
        self.assertEqual(agent.execute_task(self.create('fake-image'))['outcome'], 'artifact_committed')
        with patch.object(self.client, 'start', wraps=self.client.start) as start:
            result = agent.execute_task(self.create('codex-image', 'disabled'))
            self.assertEqual(result['code'], 'profile_not_enabled')
            start.assert_not_called()

    def test_codex_preflight_failure_never_requests_start(self):
        self.registry.executors['codex-image'] = CodexExecutor(self.state / 'executors')
        task = self.create('codex-image')
        with patch.object(self.client, 'start', wraps=self.client.start) as start, \
                patch('render_bridge.executors.codex.probe_codex') as probe:
            result = self.agent.execute_task(task)
            self.assertEqual(result['code'], 'codex_local_acceptance_required')
            start.assert_not_called()
            probe.assert_not_called()
        self.assertFalse(self.agent.journal.occupied_by_other('other'))

    def test_codex_mock_preflight_execution_receipt(self):
        executor = CodexExecutor(self.state / 'executors', CodexConfig(enabled=True,
            model='mock-model', accepted_cli_version='codex-cli 0.163.0', image_capability_verified=True,
            tool_policy_verified=True, approval_policy='on-request'))
        self.registry.executors['codex-image'] = executor
        task = self.create('codex-image')
        fake = FakeExecutor(self.state / 'mock-output')
        with patch('render_bridge.executors.codex.probe_codex', return_value={
                'cli_version': 'codex-cli 0.163.0', 'schema_version_matches': True, 'stdio_advertised': True}) as probe, \
                patch.object(executor, 'execute', side_effect=fake.execute) as execute:
            self.assertEqual(self.agent.execute_task(task)['outcome'], 'artifact_committed')
            self.assertEqual(self.agent.execute_task(task)['outcome'], 'already_committed')
            probe.assert_called_once()
            execute.assert_called_once()
        receipt = self.store.get_receipt(self.scope, self.store.get_task(self.scope, task)['receipt_id'])
        self.assertEqual(receipt['execution_profile_id'], 'codex-image')

    def test_receipt_with_wrong_task_binding_is_not_marked_complete(self):
        task = self.create('fake-image', 'receipt-binding')
        original_commit = self.client.commit

        def mismatched_commit(*args):
            return {**original_commit(*args), 'task_id': 'another-task'}

        with patch.object(self.client, 'commit', side_effect=mismatched_commit):
            result = self.agent.execute_task(task)
        self.assertEqual(result['outcome'], 'upload_pending')
        self.assertNotEqual(self.agent.journal.get(task)['state'], 'complete')

    def test_manual_export_restart_import_receipt_and_parallel_fake(self):
        task = self.create('midjourney-manual')
        with patch.object(self.client, 'start', wraps=self.client.start) as start:
            exported = self.agent.execute_task(task)
            self.assertEqual(exported['outcome'], 'awaiting_user')
            self.assertTrue(Path(exported['task_file']).is_file())
            self.assertEqual(self.agent.execute_task(task), exported)
            start.assert_not_called()
        self.assertFalse(self.agent.journal.occupied_by_other('another'))
        self.assertEqual(self.agent.execute_task(self.create('fake-image', 'other'))['outcome'], 'artifact_committed')
        reopened = NodeAgent(self.client, self.state, registry=self.registry)
        source = self.root / 'selected.png'
        source.write_bytes(fixture_png(32, 24))
        self.now += 3601  # expired execution claim uses same-attempt upload_only
        attempt = self.client.task(task)['attempt_id']
        result = reopened.manual_import(task, source, confirmed_request_sha256=exported['request_sha256'],
            confirmed_single_original=True)
        self.assertEqual(result['outcome'], 'artifact_committed')
        self.assertEqual(self.client.task(task)['attempt_id'], attempt)
        self.assertEqual(reopened.execute_task(task)['outcome'], 'already_committed')

    def test_cancelled_manual_cannot_import(self):
        task = self.create('midjourney-manual')
        exported = self.agent.execute_task(task)
        self.store.cancel_task(self.scope, task)
        source = self.root / 'selected.png'
        source.write_bytes(fixture_png(32, 24))
        with patch.object(self.client, 'create_upload', wraps=self.client.create_upload) as upload:
            result = self.agent.manual_import(task, source,
                confirmed_request_sha256=exported['request_sha256'], confirmed_single_original=True)
            self.assertEqual(result['outcome'], 'not_executable')
            upload.assert_not_called()

    def test_manual_ready_response_lost_restores_same_export(self):
        task = self.create('midjourney-manual')
        original = self.client.manual_ready
        def lost(*args):
            original(*args)
            raise ConnectionError('Response lost')
        with patch.object(self.client, 'manual_ready', side_effect=lost):
            with self.assertRaises(ConnectionError):
                self.agent.execute_task(task)
        attempt = self.client.task(task)['attempt_id']
        reopened = NodeAgent(self.client, self.state, registry=self.registry)
        self.assertEqual(reopened.execute_task(task)['outcome'], 'awaiting_user')
        self.assertEqual(reopened.journal.get(task)['attempt_id'], attempt)

    def test_manual_cancel_during_commit_is_not_success(self):
        task = self.create('midjourney-manual')
        exported = self.agent.execute_task(task)
        source = self.root / 'selected.png'
        source.write_bytes(fixture_png(32, 24))
        commit = self.client.commit
        def canceled(*args):
            self.store.cancel_task(self.scope, task)
            return commit(*args)
        with patch.object(self.client, 'commit', side_effect=canceled):
            result = self.agent.manual_import(task, source,
                confirmed_request_sha256=exported['request_sha256'], confirmed_single_original=True)
        self.assertEqual(result['outcome'], 'not_executable')
        self.assertEqual(result['code'], 'task_not_accepting')
        self.assertIsNone(self.client.task(task)['receipt_id'])

    def test_hatchet_callback_returns_manual_wait_without_blocking(self):
        from render_bridge.agent.hatchet_sidecar import make_worker
        task = self.create('midjourney-manual')
        class Hatchet:
            def task(self, **options):
                return lambda callback: callback
            def worker(self, name, **options):
                return options
        with patch.dict('sys.modules', {'hatchet_sdk': types.SimpleNamespace(Context=object)}):
            worker = make_worker(Hatchet(), self.agent)
        result = worker['workflows'][0](types.SimpleNamespace(task_id=task), object())
        self.assertEqual(result['outcome'], 'awaiting_user')
        self.assertEqual(worker['slots'], 1)
        self.assertEqual(self.agent.execute_task(self.create('fake-image', 'next'))['outcome'], 'artifact_committed')

    def test_config_defaults_and_explicit_selection(self):
        self.assertEqual(set(ExecutorRegistry.from_config(self.root).executors), {'fake-image'})
        config = self.root / 'agent.json'
        config.write_text(json.dumps({'enabled_profiles': ['fake-image', 'midjourney-manual']}), encoding='utf-8')
        registry = ExecutorRegistry.from_config(self.root, config)
        self.assertEqual(set(registry.executors), {'fake-image', 'midjourney-manual'})
