"""No credentials, external tools or services: all subprocesses are fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.executors import (CodexExecutor, ExecutionRequest, FakeExecutor,
                                    MidjourneyManualExecutor, StdioSession, probe_codex)
from render_bridge.executors.base import AttemptFiles
from render_bridge.fake import fixture_png


class OfflineExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = ExecutionRequest('task-1', 'attempt-1', '白色瓶子\nSynthetic test', {'width': 32, 'height': 24})

    def test_fake_replay_returns_same_original_without_rendering_again(self):
        executor = FakeExecutor(self.root)
        first = executor.execute(self.request)
        with patch('render_bridge.executors.fake.fixture_png', side_effect=AssertionError('rerender')):
            second = executor.execute(self.request)
        self.assertEqual(first.artifact, second.artifact)
        self.assertEqual(first.status, 'ready')
        self.assertEqual(first.artifact.provenance, 'synthetic_fixture')

    def test_fake_staged_references_are_checked_in_frozen_order(self):
        path = self.root / 'reference.png'
        path.write_bytes(fixture_png())
        refs = ({'input_ref': 'ref-1', 'role': 'reference', 'hash': hashlib.sha256(path.read_bytes()).hexdigest(), 'path': str(path)},)
        request = ExecutionRequest('task-1', 'attempt-1', 'test', {}, refs)
        result = FakeExecutor(self.root).execute(request)
        self.assertEqual(result.artifact.provenance, 'synthetic_not_conditioned_on_references')
        self.assertEqual(json.loads((result.artifact.path.parent / 'references.json').read_text())[0]['input_ref'], 'ref-1')

    def test_started_without_result_never_restarts(self):
        files = AttemptFiles(self.root, self.request, 'fake-image')
        files.begin()
        result = FakeExecutor(self.root).execute(self.request)
        self.assertEqual(result.status, 'execution_unknown')

    def test_attempt_cannot_be_rebound(self):
        FakeExecutor(self.root).execute(self.request)
        with self.assertRaises(ValueError):
            FakeExecutor(self.root).execute(ExecutionRequest('task-1', 'attempt-1', 'different'))

    def test_manual_export_preserves_prompt_and_import_is_explicit(self):
        executor = MidjourneyManualExecutor(self.root)
        exported = executor.execute(self.request)
        self.assertEqual(Path(exported.details['prompt_file']).read_bytes(), self.request.prompt.encode())
        self.assertEqual(executor.execute(self.request), exported)
        original = self.root / 'selected.png'
        original.write_bytes(fixture_png())
        with self.assertRaises(ValueError):
            executor.import_original(self.request, original, confirmed_request_sha256='wrong', confirmed_single_original=True)
        result = executor.import_original(self.request, original, confirmed_request_sha256=self.request.digest, confirmed_single_original=True)
        self.assertEqual(result.artifact.provenance, 'user_declared_midjourney_original')
        self.assertEqual(result, executor.execute(self.request))
        self.assertEqual(result, executor.import_original(self.request, original, confirmed_request_sha256=self.request.digest, confirmed_single_original=True))
        original.write_bytes(fixture_png(12, 12))
        with self.assertRaises(ValueError):
            executor.import_original(self.request, original, confirmed_request_sha256=self.request.digest, confirmed_single_original=True)

    def events(self, saved_path=None, thread='thread-1', turn='turn-1'):
        events = [{'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': 'completed'}}}]
        if saved_path:
            events.insert(0, {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn, 'item': {
                'type': 'imageGeneration', 'status': 'completed', 'id': 'image-1', 'savedPath': str(saved_path), 'result': ''}}})
        return events

    def test_codex_never_starts_without_local_acceptance(self):
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected process')):
            result = CodexExecutor(self.root).execute(self.request)
        self.assertEqual(result.status, 'needs_attention')

    def test_codex_exact_event_collects_original_and_replay(self):
        executor = CodexExecutor(self.root)
        executor.execute(self.request)
        original = self.root / 'task-1' / 'attempt-1' / 'codex-output.png'
        original.write_bytes(fixture_png())
        result = executor.collect_completed(self.request, thread_id='thread-1', turn_id='turn-1', events=self.events(original))
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.artifact.path.read_bytes(), original.read_bytes())
        self.assertEqual(executor.execute(self.request).artifact, result.artifact)

    def test_codex_text_or_wrong_turn_cannot_be_image_success(self):
        executor = CodexExecutor(self.root)
        events = self.events() + [{'method': 'item/completed', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
                   'item': {'type': 'agentMessage', 'text': 'Image generated!'}}}]
        self.assertEqual(executor.collect_completed(self.request, thread_id='thread-1', turn_id='turn-1', events=events).status, 'needs_attention')
        self.assertEqual(executor.collect_completed(self.request, thread_id='thread-1', turn_id='turn-1', events=self.events('/wrong.png', turn='other')).status, 'execution_unknown')

    def test_codex_event_cannot_select_another_attempt_file(self):
        original = self.root / 'unrelated.png'
        original.write_bytes(fixture_png())
        with self.assertRaises(ValueError):
            CodexExecutor(self.root).collect_completed(self.request, thread_id='thread-1', turn_id='turn-1', events=self.events(original))

    def test_stdio_fixed_mock_and_server_request_not_autoapproved(self):
        script = self.root / 'mock.py'
        script.write_text("import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'id':r['id'],'result':{'ok':True}}),flush=True)\n")
        with StdioSession([sys.executable, '-u', str(script)], self.root) as session:
            self.assertEqual(session.request('initialize', {'clientInfo': {'name': 'bridge-fixture', 'version': '1'}}), {'ok': True})
        script.write_text("import sys,json\nr=json.loads(sys.stdin.readline())\nprint(json.dumps({'id':99,'method':'item/commandExecution/requestApproval','params':{}}),flush=True)\n")
        with StdioSession([sys.executable, '-u', str(script)], self.root) as session:
            with self.assertRaises(PermissionError):
                session.request('initialize', {})

    def test_probe_missing_cli_does_not_claim_auth_or_image_support(self):
        self.assertEqual(probe_codex(str(self.root / 'nonexistent'))['image_generation_verified'], False)


if __name__ == '__main__':
    unittest.main()
