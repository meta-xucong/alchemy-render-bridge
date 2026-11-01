import hashlib
import copy
import http.client
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from node_trial_server import NodeTrial, PREFIX, MAX_BODY
from node_trial_client import NodeClient
from render_bridge.fake import fixture_png


class NodeTrialTests(unittest.TestCase):
    def setUp(self):
        self.trial = NodeTrial()
        self.addCleanup(self.trial.close)
        self.config = self.trial.start()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.node = NodeClient(self.config, Path(self.temp.name) / 'starts')

    def raw(self, path, body=b'{}', token=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.config['port'], timeout=5)
        self.addCleanup(conn.close)
        conn.request('POST', path, body=body, headers=headers or {
            'Authorization': 'Bearer ' + (token or self.config['token'])})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_full_http_roundtrip_and_duplicate_unknown_slot(self):
        self.assertEqual(self.node.run('happy'), {'outcome': 'artifact_committed'})
        self.assertEqual(self.node.run('happy'), {'outcome': 'duplicate_suppressed'})
        self.assertEqual(self.node.run('unknown'), {'outcome': 'execution_unknown'})
        self.assertEqual(self.node.run('unknown'), {'outcome': 'duplicate_suppressed'})
        self.assertEqual(self.node.run('blocked'), {'outcome': 'physical_slot_busy'})
        result = self.trial.verify()
        self.assertEqual(result['artifact_sha256'], hashlib.sha256(fixture_png()).hexdigest())
        self.assertEqual(self.node.journal.read_text(), 'happy\n')
        self.assertEqual(result['receipt_ack'], 'accepted')
        self.assertEqual(result['source_transport'], 'loopback_http')
        self.assertEqual(self.trial.artifact_content, fixture_png())
        self.assertEqual(self.node.prompt_digest,
                         'cfdc2217a30328caae330b9120feb5a53791022bff5e343ba5e9e774d5ff2351')

    def test_invalid_grant_types_and_shapes_fail_closed(self):
        base = {'may_execute': True, 'request': {'prompt': '合成图片\nSynthetic fixture only',
                'parameters': {'width': 32, 'height': 32}, 'references': []}}
        invalid = []
        for permission in (1, 'true', None):
            value = copy.deepcopy(base)
            value['may_execute'] = permission
            invalid.append(value)
        for dimension in ('width', 'height'):
            value = copy.deepcopy(base)
            value['request']['parameters'][dimension] = 32.0
            invalid.append(value)
        for permission in (True, False):
            value = copy.deepcopy(base)
            value.update(may_execute=permission, unexpected='field')
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value):
                with patch.object(self.node, 'request', return_value=value):
                    with self.assertRaises(RuntimeError):
                        self.node.run('happy')
        self.assertFalse(self.node.journal.exists())

    def test_changed_frozen_prompt_denied_before_renderer(self):
        with patch.object(self.node, 'request', return_value={
                'may_execute': True, 'request': {'prompt': 'different',
                'parameters': {'width': 32, 'height': 32}, 'references': []}}):
            with self.assertRaises(RuntimeError):
                self.node.run('happy')
        self.assertFalse(self.node.journal.exists())

    def test_response_loss_cannot_grant_second_start(self):
        # Ignore the first response, as if transport lost it after commit.
        self.node.request('happy', 'start')
        self.assertEqual(self.node.run('happy'), {'outcome': 'duplicate_suppressed'})
        self.assertFalse(self.node.journal.exists())
        self.assertEqual(self.trial.grants['happy'], 1)

    def test_auth_scope_and_operation_bounds(self):
        for path, body, token, code in [
            (PREFIX+'happy/start', b'{}', 'wrong', 401),
            (PREFIX+'foreign/start', b'{}', None, 404),
            (PREFIX+'happy/ack', b'{}', None, 404),
            (PREFIX+'happy/start?claim=foreign', b'{}', None, 404),
            (PREFIX+'happy/start', b'{"claim":"foreign"}', None, 400),
            (PREFIX+'happy/start', b'{invalid', None, 400),
            (PREFIX+'happy/artifact', b'x'*(MAX_BODY+1), None, 413),
            ('/bridge/v1/tasks', b'{}', None, 404),
        ]:
            with self.subTest(path=path, code=code):
                self.assertEqual(self.raw(path, body, token)[0], code)
        self.assertEqual(sum(self.trial.grants.values()), 0)

    def test_duplicate_authorization_and_transfer_encoding_rejected(self):
        for duplicate in ('Authorization', 'Content-Length', 'Transfer-Encoding'):
            conn = http.client.HTTPConnection('127.0.0.1', self.config['port'], timeout=5)
            self.addCleanup(conn.close)
            conn.putrequest('POST', PREFIX+'happy/start')
            conn.putheader('Authorization', 'Bearer '+self.config['token'])
            conn.putheader('Content-Length', '2')
            conn.putheader(duplicate, 'Bearer '+self.config['token'] if duplicate == 'Authorization' else '2')
            conn.endheaders(b'{}')
            response = conn.getresponse()
            self.assertIn(response.status, (400, 401))
            response.read()

    def test_unknown_requires_grant_and_commit_bound_to_task(self):
        self.assertEqual(self.raw(PREFIX+'unknown/unknown')[1]['error'], 'execution_not_started')
        self.node.run('happy')
        self.assertEqual(self.raw(PREFIX+'unknown/commit')[1]['error'], 'upload_not_ready')
        self.node.run('unknown')
        self.assertEqual(self.node.request('unknown', 'unknown'), {'marked_unknown': True})

    def test_bad_media_conflict_and_cancel_before_commit(self):
        self.node.request('happy', 'start')
        self.assertNotEqual(self.raw(PREFIX+'happy/artifact', b'not an image')[0], 200)
        # Bad media reserved metadata: changing digest must conflict, never overwrite.
        self.assertEqual(self.raw(PREFIX+'happy/artifact', fixture_png())[1]['error'], 'idempotency_conflict')
        self.trial.store.cancel_task(self.trial.scope, self.trial.bindings['happy'][0])
        self.assertNotEqual(self.raw(PREFIX+'happy/commit')[0], 200)

    def test_cancel_after_upload_prevents_commit(self):
        self.node.request('happy', 'start')
        self.node.request('happy', 'artifact', fixture_png())
        self.trial.store.cancel_task(self.trial.scope, self.trial.bindings['happy'][0])
        self.assertEqual(self.raw(PREFIX+'happy/commit')[1]['error'], 'task_not_accepting')

    def test_client_rejects_endpoint_override_and_no_scope_paths(self):
        with self.assertRaises(ValueError):
            NodeClient({'port': True, 'token': self.config['token']}, '/unused')
        self.assertEqual(set(self.config), {'port', 'token'})
        with self.assertRaises(ValueError):
            self.node.request('../scope', 'start')


class NodeCleanupTests(unittest.TestCase):
    def test_quick_demo_retains_verified_png_and_report(self):
        script = Path(__file__).resolve().parents[1] / 'demo_source_http.py'
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'demo'
            completed = subprocess.run([sys.executable, str(script), '--output-dir', str(output)],
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((output / 'result.json').read_text())
            content = (output / 'synthetic.png').read_bytes()
            self.assertEqual(report['artifact_sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(report['source_transport'], 'loopback_http')
            self.assertEqual(report['fake_render_starts'], 1)
            self.assertTrue(report['temporary_runtime_removed'])
            self.assertEqual(set(output.iterdir()), {output / 'synthetic.png', output / 'result.json'})

    def test_separate_server_process_protocol_and_cleanup(self):
        script = Path(__file__).resolve().parents[1] / 'node_trial_server.py'
        process = subprocess.Popen([sys.executable, '-u', str(script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.addCleanup(cleanup)
        process.stdin.write('start\n')
        process.stdin.flush()
        ready = json.loads(process.stdout.readline())
        self.assertEqual(ready.pop('event'), 'node_ready')
        self.assertIsInstance(ready.pop('source_port'), int)
        with tempfile.TemporaryDirectory() as root:
            node = NodeClient(ready, Path(root) / 'starts')
            for key in ('happy', 'happy', 'unknown', 'unknown', 'blocked'):
                node.run(key)
        process.stdin.write('verify\n')
        process.stdin.flush()
        verified = json.loads(process.stdout.readline())
        self.assertEqual(verified['status'], 'passed')
        self.assertNotIn('token', verified)
        process.stdin.write('stop\n')
        process.stdin.flush()
        stopped = json.loads(process.stdout.readline())
        self.assertEqual(stopped, {'event': 'node_stopped', 'status': 'passed',
                                   'temporary_directory_removed': True})
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(process.stderr.read(), '')

    def test_thread_start_failure_does_not_wait_for_serve_forever(self):
        import node_trial_server
        trial = NodeTrial()
        with patch.object(node_trial_server.threading.Thread, 'start', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                trial.start()
        trial.close()
        self.assertFalse(trial.root.exists())
        self.assertEqual(trial.server.socket.fileno(), -1)

    def test_initialization_failure_removes_owned_directory(self):
        import node_trial_server
        original = tempfile.TemporaryDirectory
        created = []
        def temporary(*args, **kwargs):
            value = original(*args, **kwargs)
            created.append(Path(value.name))
            return value
        with patch.object(node_trial_server.tempfile, 'TemporaryDirectory', side_effect=temporary):
            with patch.object(node_trial_server.BridgeStore, 'create_task', side_effect=RuntimeError('injected')):
                with self.assertRaises(RuntimeError):
                    NodeTrial()
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())

    def test_stop_failure_still_removes_files(self):
        trial = NodeTrial()
        trial.start()
        original = trial.server.shutdown
        def failing_shutdown():
            original()
            raise RuntimeError('injected after orderly stop')
        with patch.object(trial.server, 'shutdown', side_effect=failing_shutdown):
            with self.assertRaises(RuntimeError):
                trial.close()
        self.assertFalse(trial.root.exists())
        self.assertFalse(trial.thread.is_alive())

    def test_store_close_failure_still_removes_files(self):
        trial = NodeTrial()
        with patch.object(trial.store, 'close', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                trial.close()
        self.assertFalse(trial.root.exists())


if __name__ == '__main__':
    unittest.main()
