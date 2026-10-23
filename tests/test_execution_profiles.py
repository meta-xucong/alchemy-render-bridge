"""Configured profiles and manual ledger flow, without external execution."""
import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.node_api import NodeScope
from render_bridge.runtime import RuntimeConfig, create_runtime_app
from render_bridge.schemas import TaskCreate


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000
        self.scope = SourceScope('source', 'workspace')
        self.store = BridgeStore(self.root/'db', self.root/'artifacts', clock=lambda: self.now,
            enabled_profiles=('fake-image', 'codex-image', 'midjourney-manual'))

    def payload(self, profile='midjourney-manual', key='one', size=(32,32)):
        return dict(schema_version='1', origin_key=key, binding_id='binding',
            operation='image.generate', execution_profile_id=profile, execution_profile_revision='1',
            prompt='Synthetic test', parameters=dict(width=size[0],height=size[1]), references=[],
            output_slot=dict(slot_id='primary',count=1), routing='bridge_only',
            deadline=datetime.fromtimestamp(self.now+86400, timezone.utc).isoformat())

    def claimed(self, profile='midjourney-manual', key='one', size=(32,32)):
        task = self.store.create_task(self.scope,self.payload(profile,key,size))
        tid = task['remote_task_id']
        claim = self.store.claim_task(self.scope,tid,node_id='node',executor_instance='executor')
        return tid,claim

    def upload(self, tid, claim, size=(32,32), noisy=False):
        buf=io.BytesIO()
        image = Image.frombytes('RGB',size,os.urandom(size[0]*size[1]*3)) if noisy else Image.new('RGB',size)
        image.save(buf,format='PNG'); data=buf.getvalue()
        if noisy: self.assertGreater(len(data), 1_048_576)
        up=self.store.create_upload(self.scope,tid,claim['claim_id'],dict(attempt_id=claim['attempt_id'],
            slot_id='primary',original_sha256=hashlib.sha256(data).hexdigest(),size=len(data),mime_type='image/png'))
        self.store.put_upload_content(self.scope,up['upload_id'],claim['claim_id'],data)
        return up

    def test_default_disabled_and_profile_limits(self):
        default=BridgeStore(self.root/'default',self.root/'default-artifacts')
        with self.assertRaises(DomainError) as caught:
            default.create_task(self.scope,self.payload())
        self.assertEqual(caught.exception.code,'unsupported_capability')
        self.assertEqual([p['execution_profile_id'] for p in default.capabilities(self.scope)['profiles']],['fake-image'])
        TaskCreate.model_validate(self.payload('codex-image',size=(2048,2048)))
        with self.assertRaises(ValueError):
            TaskCreate.model_validate(self.payload('fake-image',size=(2048,2048)))
        with self.assertRaises(DomainError):
            self.store.create_task(self.scope,self.payload('codex-image',size=(10000,10000)))
        with self.assertRaises(ValueError):
            RuntimeConfig(self.root,'source','workspace','node','executor',enabled_profiles=('typo',)).validate()

    def test_manual_wait_idempotent_expiry_and_automatic_slot(self):
        tid,claim=self.claimed()
        ready=self.store.begin_manual(self.scope,tid,claim['claim_id'])
        self.assertEqual(ready,self.store.begin_manual(self.scope,tid,claim['claim_id']))
        self.assertFalse(ready['may_execute'])
        with self.assertRaises(DomainError): self.store.start_execution(self.scope,tid,claim['claim_id'])
        auto,ac=self.claimed('fake-image','automatic')
        self.assertTrue(self.store.start_execution(self.scope,auto,ac['claim_id'])['may_execute'])
        with self.assertRaises(DomainError): self.store.begin_manual(self.scope,auto,ac['claim_id'])
        self.now += 61
        view=self.store.get_task(self.scope,tid)
        self.assertEqual(view['state'],'awaiting_user')
        self.assertEqual(view['action_required'],'import_original')
        recovery=self.store.claim_task(self.scope,tid,node_id='node',executor_instance='executor',mode='upload_only')
        self.assertEqual(recovery['attempt_id'],claim['attempt_id'])
        up=self.upload(tid,recovery)
        receipt=self.store.commit_upload(self.scope,up['upload_id'],recovery['claim_id'])
        self.assertEqual(receipt['execution_profile_id'],'midjourney-manual')

    def test_manual_capacity_and_cancel_blocks_commit(self):
        for i in range(3):
            tid,claim=self.claimed(key=str(i))
            self.store.begin_manual(self.scope,tid,claim['claim_id'])
        fourth,fc=self.claimed(key='fourth')
        with self.assertRaises(DomainError) as caught:
            self.store.begin_manual(self.scope,fourth,fc['claim_id'])
        self.assertEqual(caught.exception.code,'manual_capacity')
        up=self.upload(tid,claim)
        self.assertEqual(self.store.cancel_task(self.scope,tid)['state'],'canceled')
        with self.assertRaises(DomainError): self.store.commit_upload(self.scope,up['upload_id'],claim['claim_id'])
        self.store.begin_manual(self.scope,fourth,fc['claim_id'])

    def test_codex_larger_output_receipt_and_reopen(self):
        tid,claim=self.claimed('codex-image',size=(2048,2048))
        self.store.start_execution(self.scope,tid,claim['claim_id'])
        up=self.upload(tid,claim,(2048,2048),noisy=True)
        receipt=self.store.commit_upload(self.scope,up['upload_id'],claim['claim_id'])
        self.assertEqual(receipt['execution_profile_id'],'codex-image')
        # Existing requests retain their frozen profile even after config disables new admissions.
        reopened=BridgeStore(self.root/'db',self.root/'artifacts')
        self.assertEqual(reopened.get_receipt(self.scope,receipt['receipt_id'])['execution_profile_id'],'codex-image')

    def test_codex_upload_only_claim_is_bound_to_original_start_identity(self):
        tid, execute_claim = self.claimed('codex-image', key='identity-bound-upload')
        grant = self.store.start_execution(self.scope, tid, execute_claim['claim_id'])
        self.assertTrue(grant['start_grant_id'])
        self.now += 61
        upload_claim = self.store.claim_task(self.scope, tid, node_id='node',
            executor_instance='executor', mode='upload_only')
        self.assertEqual(upload_claim['mode'], 'upload_only')
        self.assertEqual(upload_claim['attempt_id'], execute_claim['attempt_id'])
        self.now += 61
        with self.assertRaises(DomainError) as caught:
            self.store.claim_task(self.scope, tid, node_id='node',
                executor_instance='different-executor', mode='upload_only')
        self.assertEqual(caught.exception.code, 'upload_only_identity_mismatch')

    def test_manual_deadline_blocks_late_import_and_releases_pending_capacity(self):
        for i in range(3):
            tid, claim = self.claimed(key='expired-'+str(i))
            self.store.begin_manual(self.scope, tid, claim['claim_id'])
        up = self.upload(tid, claim)
        self.now += 86401
        self.assertEqual(self.store.get_task(self.scope, tid)['state'], 'expired')
        with self.assertRaises(DomainError) as caught:
            self.store.commit_upload(self.scope, up['upload_id'], claim['claim_id'])
        self.assertEqual(caught.exception.code, 'task_expired')
        with self.assertRaises(DomainError):
            self.store.claim_task(self.scope, tid, node_id='node', executor_instance='executor', mode='upload_only')
        new, nc = self.claimed(key='new')
        self.store.begin_manual(self.scope, new, nc['claim_id'])
        payload = self.payload('codex-image', key='missing-dimensions')
        payload['parameters'] = {}
        with self.assertRaises(ValueError): TaskCreate.model_validate(payload)
        with self.assertRaises(DomainError): self.store.create_task(self.scope, payload)

    def test_manual_http_route(self):
        app=create_runtime_app(store=self.store,source_tokens={'source-token':self.scope},
            node_tokens={'node-token':NodeScope('source','workspace','node','executor')},
            dispatch_tokens={'dispatch-token':self.scope})
        with TestClient(app) as client:
            task=client.post('/bridge/v1/tasks',json=self.payload(),headers={'Authorization':'Bearer source-token'}).json()
            path='/bridge/node/v1/tasks/'+task['remote_task_id']
            headers={'Authorization':'Bearer node-token'}
            claim=client.post(path+'/claim',json={},headers=headers).json()
            ready=client.post(path+'/manual-ready',json={'claim_id':claim['claim_id']},headers=headers)
            self.assertEqual(ready.status_code,200,ready.text)
            self.assertEqual(ready.json(),dict(attempt_id=claim['attempt_id'],state='awaiting_user',may_execute=False))
