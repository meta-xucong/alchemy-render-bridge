"""Small configured-Agent contract suite. No model, account, or Hatchet service."""
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
from unittest.mock import patch

from fastapi.testclient import TestClient

from render_bridge.agent import AgentBusy, HttpNodeClient, InstanceLock, NodeAgent
from render_bridge.agent.client import validated_url
from render_bridge.agent.hatchet_sidecar import (
    TASK_NAME, make_submitter, make_worker, task_name_for_bridge_url,
)
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.executors.fake import FakeExecutor
from render_bridge.fake import fixture_png
from render_bridge.node_api import NodeScope
from render_bridge.runtime import create_runtime_app


class CountingFake(FakeExecutor):
    def __init__(self, root):
        super().__init__(root)
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return super().execute(request)


class AgentRuntimeTests(unittest.TestCase):
    def test_node_client_uses_explicit_ca_bundle_without_enabling_proxy_env(self):
        with patch.dict('os.environ', {'BRIDGE_CA_BUNDLE': 'local-ca.pem'}):
            with patch('render_bridge.agent.client.httpx.Client') as client_factory:
                HttpNodeClient('https://bridge.example', 'node-token')
        self.assertEqual(client_factory.call_args.kwargs['verify'], 'local-ca.pem')
        self.assertFalse(client_factory.call_args.kwargs['trust_env'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).timestamp()
        self.scope = SourceScope('source', 'workspace')
        self.store = BridgeStore(self.root / 'server' / 'ledger.db', self.root / 'server' / 'blobs', clock=lambda: self.now)
        app = create_runtime_app(store=self.store, source_tokens={'source-token': self.scope},
                                 node_tokens={'node-token': NodeScope('source', 'workspace', 'node', 'instance')},
                                 dispatch_tokens={'dispatch-token': self.scope})
        self.client = HttpNodeClient('http://127.0.0.1:8765', 'node-token')
        self.client.close()
        self.client.http = TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization': 'Bearer node-token'})
        self.agent = NodeAgent(self.client, self.root / 'agent')
        self.fake = CountingFake(self.root / 'agent' / 'executors')

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.temp.cleanup()

    def create(self, origin='test', references=None):
        return self.store.create_task(self.scope, {
            'schema_version': '1', 'origin_key': origin, 'binding_id': 'binding',
            'operation': 'image.generate', 'execution_profile_id': 'fake-image',
            'execution_profile_revision': '1', 'prompt': 'Exact frozen prompt 中文',
            'parameters': {'width': 32, 'height': 24, 'seed': 1}, 'references': references or [],
            'output_slot': {'slot_id': 'primary', 'count': 1}, 'routing': 'bridge_only',
            'deadline': (datetime.fromtimestamp(self.now, timezone.utc) + timedelta(hours=2)).isoformat(),
        })['remote_task_id']

    def test_pull_recovery_reselects_only_saved_artifacts_during_reconnect(self):
        self.agent.journal.save({'task_id':'upload-task','attempt_id':'attempt-a','state':'artifact_ready'})
        self.agent.journal.save({'task_id':'unknown-task','attempt_id':'attempt-b','state':'execution_unknown'})
        self.assertEqual([row['task_id'] for row in self.agent.journal.pending_artifact_records()], ['upload-task'])

    def test_node_pull_offer_executes_once_through_existing_receipt_flow(self):
        self.store.dispatch_mode = 'node_pull'
        self.agent._worker_running = True
        self.assertTrue(self.agent.heartbeat())
        task_id = self.create('pull-task')
        self.assertEqual(self.client.next_task(), task_id)
        self.assertEqual(self.client.next_task(), task_id)
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        self.assertIsNone(self.client.next_task())
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(self.store.get_task(self.scope, task_id)['state'], 'artifacts_ready')

    def test_dynamic_task_complete_and_reopen_replay(self):
        task_id = self.create()
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        reopened = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(reopened.execute_task(task_id, self.fake)['outcome'], 'already_committed')
        self.assertEqual(self.fake.calls, 1)
        task = self.store.get_task(self.scope, task_id)
        receipt = self.store.get_receipt(self.scope, task['receipt_id'])
        content, mime = self.store.get_artifact_content(self.scope, receipt['artifact_id'])
        self.assertEqual(hashlib.sha256(content).hexdigest(), receipt['original_sha256'])
        self.assertEqual(mime, 'image/png')

    def test_long_render_uploads_after_execution_claim_expires(self):
        task_id = self.create()
        execute = self.fake.execute
        def finish_after_lease(request):
            self.now += 3601
            return execute(request)
        self.fake.execute = finish_after_lease
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        self.assertEqual(self.fake.calls, 1)
        record = self.agent.journal.get(task_id)
        with self.store._connection() as con:
            claim = con.execute('SELECT mode, epoch FROM claims WHERE task_id=?', (task_id,)).fetchone()
        self.assertEqual((claim['mode'], claim['epoch']), ('upload_only', 2))
        self.assertEqual(self.client.task(task_id)['attempt_id'], record['attempt_id'])
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'already_committed')
        self.assertEqual(self.fake.calls, 1)

    def test_upload_failure_replays_only_upload_after_claim_expiry(self):
        task_id = self.create()
        put = self.client.put_upload
        self.client.put_upload = lambda *args: (_ for _ in ()).throw(ConnectionError())
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'upload_pending')
        self.client.put_upload = put
        record = self.agent.journal.get(task_id)
        saved = self.root / 'agent' / record['artifact']
        original = saved.read_bytes()
        original_hash = hashlib.sha256(original).hexdigest()
        restarted = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(restarted.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        task = self.store.get_task(self.scope, task_id)
        receipt = self.store.get_receipt(self.scope, task['receipt_id'])
        delivered, _mime = self.store.get_artifact_content(self.scope, receipt['artifact_id'])
        self.assertEqual((task['attempt_id'], receipt['attempt_id']), (record['attempt_id'], record['attempt_id']))
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), original_hash)
        self.assertEqual(delivered, original)
        self.assertEqual(self.fake.calls, 1)

    def test_upload_response_lost_after_server_received_bytes_replays_same_original(self):
        task_id = self.create('upload-response-lost')
        put = self.client.put_upload
        sent = []
        def receive_then_lose(upload_id, claim_id, content, mime_type):
            sent.append(content)
            put(upload_id, claim_id, content, mime_type)
            raise ConnectionError('Upload response lost after Bridge stored bytes')
        self.client.put_upload = receive_then_lose
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'upload_pending')
        record = self.agent.journal.get(task_id)
        original = (self.root / 'agent' / record['artifact']).read_bytes()
        original_hash = hashlib.sha256(original).hexdigest()
        self.client.put_upload = lambda upload_id, claim_id, content, mime_type: (
            sent.append(content), put(upload_id, claim_id, content, mime_type))[1]
        restarted = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(restarted.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        task = self.store.get_task(self.scope, task_id)
        receipt = self.store.get_receipt(self.scope, task['receipt_id'])
        delivered, _mime = self.store.get_artifact_content(self.scope, receipt['artifact_id'])
        self.assertEqual(sent, [original, original])
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), original_hash)
        self.assertEqual((task['attempt_id'], receipt['attempt_id']), (record['attempt_id'], record['attempt_id']))
        self.assertEqual(self.fake.calls, 1)

    def test_commit_response_lost_uses_server_receipt(self):
        task_id = self.create()
        commit = self.client.commit
        def lose(*args):
            commit(*args)
            raise ConnectionError('Lost response')
        self.client.commit = lose
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'upload_pending')
        receipt_id = self.store.get_task(self.scope, task_id)['receipt_id']
        restarted = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(restarted.execute_task(task_id, self.fake)['outcome'], 'already_committed')
        self.assertEqual(restarted.journal.get(task_id)['receipt_id'], receipt_id)
        receipt = self.store.get_receipt(self.scope, receipt_id)
        delivered, _mime = self.store.get_artifact_content(self.scope, receipt['artifact_id'])
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), receipt['original_sha256'])
        self.assertEqual(self.fake.calls, 1)

    def test_start_response_unknown_never_executes_or_takes_another_slot(self):
        task_id = self.create()
        start = self.client.start
        start_calls = []
        def lose(*args):
            start_calls.append(args)
            start(*args)
            raise ConnectionError('Lost response')
        self.client.start = lose
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        attempt_id = self.store.get_task(self.scope, task_id)['attempt_id']
        with self.store._connection() as con:
            self.assertIsNotNone(con.execute('SELECT start_grant_id FROM attempts WHERE task_id=?', (task_id,)).fetchone()['start_grant_id'])
        restarted = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(restarted.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        self.assertEqual(self.store.get_task(self.scope, task_id)['attempt_id'], attempt_id)
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(self.agent.execute_task(self.create('second'), self.fake)['outcome'], 'physical_slot_busy')
        self.assertEqual(self.fake.calls, 0)

    def test_start_request_not_reached_bridge_stays_unknown_after_restart(self):
        task_id = self.create('start-not-received')
        start_calls = []
        def fail_before_server(*args):
            start_calls.append(args)
            raise ConnectionError('Request did not reach Bridge')
        self.client.start = fail_before_server
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        attempt_id = self.store.get_task(self.scope, task_id)['attempt_id']
        with self.store._connection() as con:
            self.assertIsNone(con.execute('SELECT start_grant_id FROM attempts WHERE task_id=?', (task_id,)).fetchone()['start_grant_id'])
        restarted = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(restarted.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        self.assertEqual(self.store.get_task(self.scope, task_id)['attempt_id'], attempt_id)
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(self.fake.calls, 0)

    def test_references_downloaded_in_order_and_verified(self):
        refs = []
        for i in range(2):
            content = fixture_png(10+i, 10+i)
            digest = hashlib.sha256(content).hexdigest()
            value = self.store.create_input(self.scope, {'input_key': f'input-{i}', 'original_sha256': digest,
                                                         'size': len(content), 'mime_type': 'image/png'})
            self.store.put_input_content(self.scope, value['input_id'], content)
            refs.append({'input_ref': value['input_id'], 'role': 'reference', 'hash': digest})
        task_id = self.create(references=refs)
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        attempt = self.store.get_task(self.scope, task_id)['attempt_id']
        work = self.root / 'agent' / 'attempts' / task_id / attempt
        self.assertEqual(hashlib.sha256((work / 'input-00.original').read_bytes()).hexdigest(), refs[0]['hash'])
        self.assertEqual(hashlib.sha256((work / 'input-01.original').read_bytes()).hexdigest(), refs[1]['hash'])

    def test_two_instances_do_not_share_a_state_lock(self):
        path = self.root / 'agent' / 'agent.lock'
        with InstanceLock(path):
            with self.assertRaises(AgentBusy):
                with InstanceLock(path):
                    pass
        with InstanceLock(path):
            pass

    def test_plain_http_only_for_loopback(self):
        self.assertEqual(validated_url('http://127.0.0.1:8765/'), 'http://127.0.0.1:8765')
        self.assertEqual(validated_url('https://bridge.example'), 'https://bridge.example')
        for value in ('http://bridge.example', 'https://secret@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError):
                validated_url(value)

    def test_hatchet_task_names_are_partitioned_by_bridge_endpoint(self):
        first = task_name_for_bridge_url('http://127.0.0.1:8797/')
        same = task_name_for_bridge_url('http://127.0.0.1:8797')
        second = task_name_for_bridge_url('http://127.0.0.1:8798')
        self.assertEqual(first, same)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(TASK_NAME + '-'))

    def test_hatchet_task_name_is_shared_by_dispatcher_and_worker(self):
        task_name = task_name_for_bridge_url('http://127.0.0.1:8797')
        class Stub:
            def __init__(self, owner):
                self.owner = owner
            def run(self, **kwargs):
                return types.SimpleNamespace(workflow_run_id='run-id')

        class Stubs:
            def __init__(self, owner):
                self.owner = owner
            def task(self, *, name, input_validator):
                self.owner.dispatch_name = name
                return Stub(self.owner)

        class Hatchet:
            def __init__(self):
                self.stubs = Stubs(self)
                self.worker_name = None
            def task(self, *, name, **kwargs):
                self.worker_name = name
                return lambda function: function
            def worker(self, name, *, slots, workflows):
                return types.SimpleNamespace(name=name, slots=slots, workflows=workflows)

        hatchet = Hatchet()
        submit = make_submitter(hatchet, task_name)
        self.assertEqual(submit('task-id', 'dispatch-key'), 'run-id')
        fake_sdk = types.ModuleType('hatchet_sdk')
        fake_sdk.Context = type('Context', (), {})
        with patch.dict(sys.modules, {'hatchet_sdk': fake_sdk}):
            worker = make_worker(hatchet, node=object(), task_name=task_name)
        self.assertEqual(hatchet.dispatch_name, hatchet.worker_name)
        self.assertEqual(hatchet.worker_name, task_name)
        self.assertEqual(worker.workflows[0].__name__, 'execute')
