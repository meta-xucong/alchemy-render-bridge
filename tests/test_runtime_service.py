"""Configured dynamic task handoff and node HTTP contract, without a real engine."""
import hashlib
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import time
import unittest

import httpx

from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.fake import fixture_png
from render_bridge.node_api import NodeScope
from render_bridge.runtime import RuntimeConfig, create_runtime_app


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = BridgeStore(self.root/'ledger.db', self.root/'objects')
        self.scope = SourceScope('source', 'workspace')
        self.app = create_runtime_app(store=self.store, source_tokens={'source-test': self.scope},
            node_tokens={'node-test': NodeScope('source','workspace','node','executor')},
            dispatch_tokens={'dispatch-test': self.scope})
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://fixture')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.close()
        self.temp.cleanup()

    async def req(self, method, path, token='source-test', **kwargs):
        headers = {'Authorization': 'Bearer '+token, **kwargs.pop('headers', {})}
        response = await self.client.request(method, path, headers=headers, **kwargs)
        self.assertLess(response.status_code, 300, response.text)
        return response

    def payload(self, key='dynamic-one'):
        return {'schema_version':'1','origin_key':key,'binding_id':'opaque-binding',
            'operation':'image.generate','execution_profile_id':'fake-image','execution_profile_revision':'1',
            'prompt':'A caller-supplied frozen prompt','parameters':{'width':32,'height':32},
            'references':[],'output_slot':{'slot_id':'primary','count':1},'routing':'bridge_only',
            'deadline':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}

    async def test_dynamic_source_dispatch_node_receipt(self):
        payload=self.payload()
        task=(await self.req('POST','/bridge/v1/tasks',json=payload)).json()
        replay=(await self.req('POST','/bridge/v1/tasks',json=payload)).json()
        self.assertEqual(task['remote_task_id'], replay['remote_task_id'])
        pending=(await self.req('GET','/bridge/dispatch/v1/pending',token='dispatch-test')).json()['items']
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['task_id'],task['remote_task_id'])
        key=pending[0]['dispatch_key']
        for _ in range(2):
            ack=(await self.req('POST',f'/bridge/dispatch/v1/{key}/ack',token='dispatch-test',json={'engine_run_id':'fake-engine-run'})).json()
            self.assertTrue(ack['acknowledged'])
        self.assertEqual((await self.req('GET','/bridge/dispatch/v1/pending',token='dispatch-test')).json()['items'],[])
        tid=task['remote_task_id']; prefix=f'/bridge/node/v1/tasks/{tid}'
        view=(await self.req('GET',prefix,token='node-test')).json()
        self.assertEqual(view['request']['prompt'], payload['prompt'])
        claim=(await self.req('POST',prefix+'/claim',token='node-test',json={'mode':'execute'})).json()
        grant=(await self.req('POST',prefix+'/start',token='node-test',json={'claim_id':claim['claim_id']})).json()
        self.assertTrue(grant['may_execute'])
        self.assertFalse((await self.req('POST',prefix+'/start',token='node-test',json={'claim_id':claim['claim_id']})).json()['may_execute'])
        content=fixture_png(32,32); digest=hashlib.sha256(content).hexdigest()
        upload=(await self.req('POST',prefix+'/uploads',token='node-test',json={'claim_id':claim['claim_id'], 'payload':{
            'attempt_id':claim['attempt_id'],'slot_id':'primary','original_sha256':digest,'size':len(content),'mime_type':'image/png'}})).json()
        up='/bridge/node/v1/uploads/'+upload['upload_id']
        await self.req('PUT',up+'/content',token='node-test',content=content,headers={'X-Bridge-Claim':claim['claim_id'],'Content-Type':'image/png'})
        receipt=(await self.req('POST',up+'/commit',token='node-test',json={'claim_id':claim['claim_id']})).json()
        downloaded=await self.req('GET','/bridge/v1/artifacts/'+receipt['artifact_id']+'/content')
        self.assertEqual(downloaded.content,content)
        await self.req('POST','/bridge/v1/receipts/'+receipt['receipt_id']+'/ack',json={
            'client_receipt_id':'dynamic-result','decision':'accepted','reason_code':'fixture_verified','original_hash':digest})
        reopened=BridgeStore(self.root/'ledger.db',self.root/'objects')
        self.assertEqual(reopened.get_task(self.scope,tid)['delivery_ack'],'accepted')

    async def test_canceled_handoff_not_dispatched(self):
        task=(await self.req('POST','/bridge/v1/tasks',json=self.payload())).json()
        await self.req('POST','/bridge/v1/tasks/'+task['remote_task_id']+'/cancel')
        self.assertEqual((await self.req('GET','/bridge/dispatch/v1/pending',token='dispatch-test')).json()['items'],[])

    async def test_node_pull_admission_offer_and_existing_claim_boundary(self):
        self.store.dispatch_mode = 'node_pull'
        payload = self.payload('pull-one')
        response = await self.client.post('/bridge/v1/tasks', headers={'Authorization':'Bearer source-test'}, json=payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'node_unavailable')
        heartbeat = await self.req('POST','/bridge/node/v1/heartbeat',token='node-test',
            json={'state':'available','profiles':['fake-image']})
        self.assertTrue(heartbeat.json()['accepted'])
        capabilities = (await self.req('GET','/bridge/v1/capabilities')).json()
        self.assertTrue(capabilities['admission']['accepting_new_tasks'])
        task = (await self.req('POST','/bridge/v1/tasks',json=payload)).json()
        self.assertEqual((await self.req('GET','/bridge/node/v1/tasks/next',token='node-test')).json()['task_id'],
                         task['remote_task_id'])
        self.assertEqual((await self.req('GET','/bridge/node/v1/tasks/next',token='node-test')).json()['task_id'],
                         task['remote_task_id'])
        self.assertEqual((await self.req('GET','/bridge/dispatch/v1/pending',token='dispatch-test')).json()['items'], [])
        path='/bridge/node/v1/tasks/'+task['remote_task_id']+'/claim'
        claim=(await self.req('POST',path,token='node-test',json={'mode':'execute'})).json()
        replay=(await self.req('POST',path,token='node-test',json={'mode':'execute'})).json()
        self.assertEqual(claim['claim_id'], replay['claim_id'])

    async def test_node_pull_waits_for_reported_profile_and_available_worker(self):
        self.store.dispatch_mode = 'node_pull'
        self.assertFalse((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        await self.req('POST','/bridge/node/v1/heartbeat',token='node-test',
            json={'state':'attention','profiles':['fake-image']})
        self.assertFalse((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        await self.req('POST','/bridge/node/v1/heartbeat',token='node-test',
            json={'state':'available','profiles':['codex-image']})
        self.assertFalse((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        mismatch = await self.client.post('/bridge/v1/tasks', headers={'Authorization':'Bearer source-test'},
            json=self.payload('profile-mismatch'))
        self.assertEqual(mismatch.status_code, 503)
        self.assertEqual(mismatch.json()['error']['code'], 'node_unavailable')
        await self.req('POST','/bridge/node/v1/heartbeat',token='node-test',
            json={'state':'busy','profiles':['fake-image']})
        self.assertTrue((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        self.assertIsNone((await self.req('GET','/bridge/node/v1/tasks/next',token='node-test')).json()['task_id'])

    async def test_node_pull_admission_closes_on_stale_heartbeat(self):
        self.store.dispatch_mode = 'node_pull'
        now = time.time()
        self.store.clock = lambda: now
        await self.req('POST','/bridge/node/v1/heartbeat',token='node-test',
            json={'state':'available','profiles':['fake-image']})
        self.assertTrue((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        self.store.clock = lambda: now + 90
        self.assertFalse((await self.req('GET','/bridge/v1/capabilities')).json()['admission']['accepting_new_tasks'])
        response = await self.client.post('/bridge/v1/tasks', headers={'Authorization':'Bearer source-test'},
            json=self.payload('stale-heartbeat'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'node_unavailable')

    async def test_credentials_separate_and_identity_not_caller_selected(self):
        task=(await self.req('POST','/bridge/v1/tasks',json=self.payload())).json()
        path='/bridge/node/v1/tasks/'+task['remote_task_id']+'/claim'
        response=await self.client.post(path,json={'mode':'execute'},headers={'Authorization':'Bearer source-test'})
        self.assertEqual(response.status_code,401)
        response=await self.client.post(path,json={'mode':'execute','executor_instance':'other'},headers={'Authorization':'Bearer node-test'})
        self.assertEqual(response.status_code,422)
        response=await self.client.get('/bridge/dispatch/v1/pending',headers={'Authorization':'Bearer node-test'})
        self.assertEqual(response.status_code,401)

    async def test_bound_input_download(self):
        data=fixture_png(16,16); digest=hashlib.sha256(data).hexdigest()
        inp=(await self.req('POST','/bridge/v1/inputs',json={'input_key':'ref','original_sha256':digest,'size':len(data),'mime_type':'image/png'})).json()
        await self.req('PUT','/bridge/v1/inputs/'+inp['input_id']+'/content',content=data,headers={'Content-Type':'image/png'})
        payload=self.payload();payload['references']=[{'input_ref':inp['input_id'],'role':'reference','hash':digest}]
        task=(await self.req('POST','/bridge/v1/tasks',json=payload)).json()
        prefix='/bridge/node/v1/tasks/'+task['remote_task_id']
        claim=(await self.req('POST',prefix+'/claim',token='node-test',json={'mode':'execute'})).json()
        response=await self.req('GET',prefix+'/inputs/'+inp['input_id']+'/content',token='node-test',headers={'X-Bridge-Claim':claim['claim_id']})
        self.assertEqual(response.content,data)


class RuntimeConfigTests(unittest.TestCase):
    def test_config_does_not_create_credentials(self):
        config=RuntimeConfig(Path('unused'),'s','w','n','e')
        with self.assertRaises(ValueError): config.credentials({})
        with self.assertRaises(ValueError): config.credentials({'BRIDGE_SOURCE_TOKEN':'same','BRIDGE_NODE_TOKEN':'same','BRIDGE_DISPATCH_TOKEN':'same'})
        self.assertEqual(config.credentials({'BRIDGE_SOURCE_TOKEN':'s','BRIDGE_NODE_TOKEN':'n','BRIDGE_DISPATCH_TOKEN':'d'}),['s','n','d'])

    def test_node_pull_needs_no_dispatcher_credential(self):
        config=RuntimeConfig(Path('unused'),'s','w','n','e',dispatch_mode='node_pull')
        self.assertEqual(config.credentials({'BRIDGE_SOURCE_TOKEN':'s','BRIDGE_NODE_TOKEN':'n'}),['s','n'])
        with self.assertRaises(ValueError):
            RuntimeConfig(Path('unused'),'s','w','n','e',dispatch_mode='other').validate()

    def test_non_loopback_requires_tls(self):
        with self.assertRaises(ValueError): RuntimeConfig(Path('unused'),'s','w','n','e',host='0.0.0.0').validate()
        RuntimeConfig(Path('unused'),'s','w','n','e').validate()

    def test_queue_limit_is_bounded(self):
        RuntimeConfig(Path('unused'),'s','w','n','e', max_queued_tasks=1).validate()
        with self.assertRaises(ValueError): RuntimeConfig(Path('unused'),'s','w','n','e', max_queued_tasks=0).validate()


if __name__=='__main__': unittest.main()
