"""Small configured-Agent contract suite. No model, account, or Hatchet service."""
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from render_bridge.agent import AgentBusy, HttpNodeClient, InstanceLock, NodeAgent
from render_bridge.agent.client import validated_url
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

    def test_upload_failure_replays_only_upload_after_claim_expiry(self):
        task_id = self.create()
        put = self.client.put_upload
        self.client.put_upload = lambda *args: (_ for _ in ()).throw(ConnectionError())
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'upload_pending')
        self.client.put_upload = put
        self.now += 3601
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'artifact_committed')
        self.assertEqual(self.fake.calls, 1)

    def test_commit_response_lost_uses_server_receipt(self):
        task_id = self.create()
        commit = self.client.commit
        def lose(*args):
            commit(*args)
            raise ConnectionError('Lost response')
        self.client.commit = lose
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'upload_pending')
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'already_committed')
        self.assertEqual(self.fake.calls, 1)

    def test_start_response_unknown_never_executes_or_takes_another_slot(self):
        task_id = self.create()
        start = self.client.start
        def lose(*args):
            start(*args)
            raise ConnectionError('Lost response')
        self.client.start = lose
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        self.assertEqual(self.agent.execute_task(task_id, self.fake)['outcome'], 'execution_unknown')
        self.assertEqual(self.agent.execute_task(self.create('second'), self.fake)['outcome'], 'physical_slot_busy')
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
