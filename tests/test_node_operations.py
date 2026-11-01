"""Local node operations against synthetic scopes, images and credentials only."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_agent_profiles as fixtures
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.node_api import NodeScope
from render_bridge.runtime import create_runtime_app, RuntimeConfig
from render_bridge.operations import main
from render_bridge.operational_store import AUDIT_CAPACITY


class NodeOperationsTests(unittest.TestCase):
    setUp = fixtures.AgentProfilesTests.setUp
    tearDown = fixtures.AgentProfilesTests.tearDown
    create = fixtures.AgentProfilesTests.create

    def toggle(self, active):
        return self.store.set_node_active(self.scope, 'node', 'instance', active)

    def test_revoke_is_live_and_survives_restart(self):
        task = self.create('fake-image')
        claim = self.client.claim(task)
        self.toggle(False)
        for path, payload in [('/heartbeat', {'state': 'available', 'profiles': ['fake-image']}),
                (f'/tasks/{task}/claim', {'mode': 'execute'}),
                (f'/tasks/{task}/start', {'claim_id': claim['claim_id']}),
                (f'/tasks/{task}/uploads', {'claim_id': claim['claim_id'], 'payload': {}})]:
            response = self.client.http.post('/bridge/node/v1' + path, json=payload)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()['error']['code'], 'node_revoked')
        with self.assertRaises(DomainError):
            self.store.start_execution(self.scope, task, claim['claim_id'])
        reopened = BridgeStore(self.root/'ledger.db', self.root/'blobs', clock=lambda: self.now)
        create_runtime_app(store=reopened, source_tokens={'source': self.scope},
            node_tokens={'node': NodeScope('source', 'workspace', 'node', 'instance')},
            dispatch_tokens={'dispatch': self.scope})
        self.assertEqual(reopened.configured_node_status(self.scope, 'node', 'instance')['authorization'], 'revoked')
        caps = reopened.capabilities(self.scope)
        self.assertEqual(caps['node_status']['authorization'], 'revoked')
        self.assertEqual(caps['node_status']['state'], 'attention')
        self.assertEqual(reopened.get_task(self.scope, task)['state'], 'queued')

    def test_revoke_does_not_unlock_started_attempt(self):
        task = self.create('fake-image')
        claim = self.client.claim(task)
        grant = self.client.start(task, claim['claim_id'])
        self.toggle(False)
        self.toggle(True)
        repeated = self.client.start(task, claim['claim_id'])
        self.assertEqual(repeated['start_grant_id'], grant['start_grant_id'])
        self.assertFalse(repeated['may_execute'])
        self.client.unknown(task, claim['claim_id'])
        self.toggle(False)
        self.toggle(True)
        self.assertEqual(self.store.get_task(self.scope, task)['state'], 'execution_unknown')
        self.assertFalse(self.client.start(task, claim['claim_id'])['may_execute'])

    def test_existing_receipt_source_read_and_ack_survive_revoke(self):
        task = self.create('fake-image')
        self.agent.execute_task(task)
        receipt_id = self.store.get_task(self.scope, task)['receipt_id']
        receipt = self.store.get_receipt(self.scope, receipt_id)
        self.toggle(False)
        headers = {'Authorization': 'Bearer source'}
        self.assertEqual(self.client.http.get('/bridge/v1/receipts/'+receipt_id, headers=headers).status_code, 200)
        self.assertEqual(self.client.http.get('/bridge/v1/artifacts/'+receipt['artifact_id']+'/content', headers=headers).status_code, 200)
        payload = {'client_receipt_id': 'secret-client-reason-not-in-audit', 'decision': 'accepted',
                   'reason_code': 'private-user-reason', 'original_hash': receipt['original_sha256']}
        for _ in range(2):
            self.assertEqual(self.client.http.post('/bridge/v1/receipts/'+receipt_id+'/ack', json=payload, headers=headers).status_code, 200)
        events = self.store.audit_events(self.scope)['events']
        kinds = [e['kind'] for e in events]
        self.assertEqual(kinds.count('delivery_accepted'), 1)
        self.assertEqual(kinds.count('receipt_committed'), 1)
        self.assertEqual(kinds.count('start_granted'), 1)
        text = json.dumps(events, ensure_ascii=False)
        for secret in ('private-user-reason', 'secret-client-reason-not-in-audit', '冻结提示词', 'Bearer', 'binding'):
            self.assertNotIn(secret, text)

    def test_revoked_existing_upload_transaction_is_blocked(self):
        task = self.create('fake-image')
        claim = self.client.claim(task)
        self.client.start(task, claim['claim_id'])
        from render_bridge.fake import fixture_png
        import hashlib
        content = fixture_png(32, 24)
        upload = self.client.create_upload(task, claim['claim_id'], {
            'attempt_id': claim['attempt_id'], 'slot_id': 'primary',
            'original_sha256': hashlib.sha256(content).hexdigest(), 'size': len(content), 'mime_type': 'image/png'})
        self.toggle(False)
        with self.assertRaises(DomainError) as error:
            self.store.put_upload_content(self.scope, upload['upload_id'], claim['claim_id'], content)
        self.assertEqual(error.exception.code, 'node_revoked')
        with self.assertRaises(DomainError):
            self.store.commit_upload(self.scope, upload['upload_id'], claim['claim_id'])

    def test_audit_is_bounded_scoped_idempotent_and_safe(self):
        task = self.create('fake-image')
        view = self.store.get_task(self.scope, task)
        self.store.create_task(self.scope, view['request'])
        self.store.report_attention(self.scope, task, view['attempt_id'], 'node', 'instance', 'profile_not_enabled')
        self.store.report_attention(self.scope, task, view['attempt_id'], 'node', 'instance', 'profile_not_enabled')
        self.store.cancel_task(self.scope, task)
        self.store.cancel_task(self.scope, task)
        kinds = [e['kind'] for e in self.store.audit_events(self.scope)['events']]
        for kind in ('task_created', 'task_attention', 'canceled'):
            self.assertEqual(kinds.count(kind), 1)
        for index in range(AUDIT_CAPACITY+10):
            self.toggle(index % 2 == 0)
        with self.store._connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], AUDIT_CAPACITY)
        self.assertEqual(len(self.store.audit_events(self.scope, 100)['events']), 100)
        self.assertEqual(self.store.audit_events(SourceScope('another', 'scope'))['events'], [])
        for limit in (0, 101, True):
            with self.assertRaises(DomainError):
                self.store.audit_events(self.scope, limit)
        with self.assertRaises(DomainError):
            self.store.set_node_active(self.scope, 'unregistered', 'instance', True)

    def test_operator_cli_uses_no_credentials_and_targets_configured_identity(self):
        # CLI fixture uses the runtime filenames, not the server fixture above.
        data = self.root/'operator'
        store = BridgeStore(data/'bridge.sqlite', data/'artifacts')
        store.provision_node(self.scope, 'node', 'instance')
        config = self.root/'operator.json'
        config.write_text(json.dumps({'data_dir': str(data), 'source_id': 'source', 'workspace_id': 'workspace',
                                     'node_id': 'node', 'executor_instance': 'instance'}))
        with patch.object(RuntimeConfig, 'credentials', side_effect=AssertionError('No credential reads')):
            for command, expected in [('node-status', 'active'), ('revoke-node', 'revoked'), ('reactivate-node', 'active')]:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(['--config', str(config), command]), 0)
                self.assertEqual(json.loads(out.getvalue())['authorization'], expected)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['--config', str(config), 'audit', '--limit', '3']), 0)
            self.assertEqual(len(json.loads(out.getvalue())['events']), 3)
