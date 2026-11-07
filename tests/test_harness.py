"""Real BridgeStore/HTTP Harness extension tests; all native output synthetic."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.harness import (CONTRACT, HarnessBinding, HarnessConfig, digest)
from render_bridge.node_api import NodeScope
from render_bridge.runtime import RuntimeConfig


BINDING = HarnessBinding(binding_id='fake-basic', revision=1,
                         model_alias='synthetic-basic', native_model='fake-basic')
SOURCE = SourceScope('source', 'workspace')
NODE = NodeScope('source', 'workspace', 'node', 'executor')


def config(**changes):
    return replace(HarnessConfig('source', 'workspace', 'ledger', 'node', 'executor',
                                 (('tenant', 'principal'),), (BINDING,), accepting_new=True), **changes)


def envelope(operation='operation', tenant='tenant', principal='principal'):
    identity = {'source': 'source', 'tenant': tenant, 'principal': principal, 'operation': operation}
    client = {'model': 'synthetic-basic', 'messages': [{'role': 'user', 'content': 'Synthetic test only'}],
              'stream': False, 'n': 1}
    return {'schema_version': CONTRACT, 'identity': identity, 'origin_key': 'h_' + digest(identity),
            'request_digest': digest(client), 'ledger_instance': 'ledger',
            'request': {'binding': BINDING.wire(), 'messages': copy.deepcopy(client['messages']), 'client_request': client}}


def result(outcome='succeeded', liveness='stopped_confirmed'):
    value = {'outcome': outcome, 'liveness': liveness,
             'output_blocks': [{'type': 'text', 'text': 'Synthetic result'}] if outcome == 'succeeded' else [],
             'native_completion': {'confirmed': True},
             'model_evidence': {'selected': 'fake-basic', 'reported': 'fake-basic'}, 'usage': {'state': 'unknown'}}
    if outcome == 'failed':
        value['code'] = 'NATIVE_FAILED'
    return value


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1800000000.0
        self.store = self.new_store()
        self.store.provision_node(SOURCE, 'node', 'executor')
        self.heartbeat()

    def new_store(self, **changes):
        return BridgeStore(self.root/'bridge.db', self.root/'objects', clock=lambda: self.now,
                           dispatch_mode='node_pull', harness=config(**changes))

    def heartbeat(self, state='available'):
        return self.store.report_harness_heartbeat(SOURCE, 'node', 'executor',
                   {'state': state, 'binding_digests': [digest(BINDING.wire())]})

    def create(self, operation='operation'):
        return self.store.create_task(SOURCE, envelope(operation))

    def begin(self, task=None):
        task = task or self.create()
        task_id = task['remote_task_id']
        claim = self.store.claim_task(SOURCE, task_id, node_id='node', executor_instance='executor')
        grant = self.store.start_execution(SOURCE, task_id, claim['claim_id'])
        return task, claim, grant

    def finish(self, task, claim, grant, value=None):
        return self.store.report_harness_result(SOURCE, task['remote_task_id'], 'node', 'executor',
               {'claim_id': claim['claim_id'], 'start_grant_id': grant['start_grant_id'], 'result': value or result()})

    def assert_error(self, code, fn):
        with self.assertRaises(DomainError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_default_disabled_and_requires_pull(self):
        disabled = BridgeStore(self.root/'disabled.db', self.root/'disabled-objects')
        self.assertNotIn('harness_contract', disabled.capabilities(SOURCE))
        self.assert_error('harness_disabled', lambda: disabled.create_task(SOURCE, envelope()))
        with self.assertRaises(ValueError):
            BridgeStore(self.root/'other.db', self.root/'other-objects', harness=config())

    def test_same_physical_ledger_tables_and_no_dispatch(self):
        task = self.create()
        self.assertEqual(self.store.capabilities(SOURCE)['harness_contract'], CONTRACT)
        with closing(sqlite3.connect(self.store.db_path)) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT count(*) FROM attempts').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT count(*) FROM dispatch_outbox').fetchone()[0], 0)
        restarted = self.new_store()
        self.assertEqual(restarted.get_task(SOURCE, task['remote_task_id']), task)
        with closing(sqlite3.connect(self.store.db_path)) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM dispatch_outbox').fetchone()[0], 0)

    def test_hundred_concurrent_same_operation_one_task(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            tasks = list(pool.map(lambda _: self.create(), range(100)))
        self.assertEqual(len({t['remote_task_id'] for t in tasks}), 1)

    def test_digest_conflict_does_not_create_new(self):
        self.create()
        other = envelope()
        other['request']['messages'][0]['content'] = 'Changed'
        other['request']['client_request']['messages'][0]['content'] = 'Changed'
        other['request_digest'] = digest(other['request']['client_request'])
        self.assert_error('idempotency_conflict', lambda: self.store.create_task(SOURCE, other))

    def test_replay_before_binding_drain_online_admission(self):
        task, claim, grant = self.begin()
        completed = self.finish(task, claim, grant)
        self.now += 1000
        restarted = self.new_store(bindings=(), accepting_new=False)
        self.assertEqual(restarted.create_task(SOURCE, envelope())['result'], completed['result'])
        self.assertEqual(restarted.get_task_by_origin(SOURCE, envelope()['origin_key'])['remote_task_id'], task['remote_task_id'])
        self.assert_error('bridge_draining', lambda: restarted.create_task(SOURCE, envelope('new')))

    def test_node_offline_or_missing_admission_rejected(self):
        self.now += 91
        error = self.assert_error('node_unavailable', lambda: self.create())
        self.assertIs(error.accepted, False)

    def test_unapproved_owner_and_foreign_source_rejected(self):
        for payload in [envelope(tenant='other'), envelope(principal='other')]:
            self.assert_error('harness_forbidden', lambda: self.store.create_task(SOURCE, payload))
        self.assert_error('harness_forbidden', lambda: self.store.create_task(SourceScope('foreign','workspace'), envelope()))

    def test_mutated_input_rejected_before_admission(self):
        changes = [lambda p: p.update(origin_key='h_'+'0'*64),
                   lambda p: p.update(request_digest='0'*64),
                   lambda p: p.update(extra='not allowed'),
                   lambda p: p['request'].update(extra=True),
                   lambda p: p['request']['binding'].update(revision=True),
                   lambda p: p['request']['client_request'].update(stream=0),
                   lambda p: p['request']['messages'][0].update(content='different'),
                   lambda p: p['request']['client_request'].update(n=True)]
        for change in changes:
            with self.subTest(change=change):
                value = envelope()
                change(value)
                self.assert_error('invalid_harness_request', lambda: self.store.create_task(SOURCE, value))
        with closing(sqlite3.connect(self.store.db_path)) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)

    def test_offer_claim_start_and_duplicate_grant(self):
        task = self.create()
        self.assertEqual(self.store.next_node_task(SOURCE,'node','executor')['task_id'],task['remote_task_id'])
        task, claim, grant = self.begin(task)
        again = self.store.start_execution(SOURCE, task['remote_task_id'], claim['claim_id'])
        self.assertEqual(again['start_grant_id'],grant['start_grant_id'])
        self.assertFalse(again['may_execute'])
        self.assertIsNone(self.store.next_node_task(SOURCE,'node','executor')['task_id'])
        self.assert_error('execution_already_started', lambda: self.store.claim_task(SOURCE, task['remote_task_id'],node_id='node',executor_instance='executor'))

    def test_prestart_expired_claim_can_be_replaced_old_fenced(self):
        task = self.create()
        first = self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor',lease_seconds=1)
        self.now += 2
        second = self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor')
        self.assertGreater(second['epoch'],first['epoch'])
        self.assert_error('stale_claim', lambda: self.store.start_execution(SOURCE,task['remote_task_id'],first['claim_id']))
        self.assertTrue(self.store.start_execution(SOURCE,task['remote_task_id'],second['claim_id'])['may_execute'])

    def test_poststart_lease_expiry_never_reassigns_but_same_result_recovers(self):
        task, claim, grant = self.begin()
        self.now += 10000
        self.assertEqual(self.store.get_task(SOURCE,task['remote_task_id'])['liveness'],'unknown')
        self.assert_error('execution_already_started', lambda: self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor',mode='upload_only'))
        completed = self.finish(task,claim,grant)
        self.assertEqual(completed['outcome'],'succeeded')

    def test_unknown_observation_does_not_finish_or_release(self):
        task, claim, grant = self.begin()
        observed = self.store.report_harness_observation(SOURCE,task['remote_task_id'],'node','executor',
                {'claim_id':claim['claim_id'],'start_grant_id':grant['start_grant_id'],
                 'outcome':'pending','liveness':'unknown','code':'OBSERVATION_ERROR'})
        self.assertEqual((observed['outcome'],observed['liveness']),('pending','unknown'))
        self.assertIsNone(observed['result'])
        self.assert_error('queue_capacity',lambda:self.create('next'))

    def test_observation_cannot_claim_stopped_and_free_slot(self):
        task, claim, grant = self.begin()
        for live in ['stopped_confirmed','not_started']:
            self.assert_error('invalid_harness_observation',lambda:self.store.report_harness_observation(
                SOURCE,task['remote_task_id'],'node','executor', {'claim_id':claim['claim_id'],
                'start_grant_id':grant['start_grant_id'],'outcome':'pending','liveness':live,'code':'OBSERVATION_ERROR'}))
        self.assert_error('queue_capacity',lambda:self.create('next'))

    def test_succeeded_or_failed_unknown_still_reserves_slot(self):
        task, claim, grant = self.begin()
        self.finish(task,claim,grant,result(liveness='unknown'))
        self.assert_error('queue_capacity',lambda:self.create('next'))
        self.assertEqual(self.store._active_task_count(self._connection_for_test()),1)

    def _connection_for_test(self):
        con=sqlite3.connect(self.store.db_path)
        con.row_factory=sqlite3.Row
        self.addCleanup(con.close)
        return con

    def test_failed_unknown_does_not_release_on_restart(self):
        task,claim,grant=self.begin()
        self.finish(task,claim,grant,result('failed','unknown'))
        restarted=self.new_store()
        self.assert_error('queue_capacity',lambda:restarted.create_task(SOURCE,envelope('next')))

    def test_stopped_terminal_result_frees_slot_and_is_immutable(self):
        task,claim,grant=self.begin()
        complete=self.finish(task,claim,grant)
        self.assertEqual(self.finish(task,claim,grant),complete)
        self.assert_error('harness_terminal_conflict',lambda:self.finish(task,claim,grant,result('failed')))
        self.assertNotEqual(self.create('next')['remote_task_id'],task['remote_task_id'])

    def test_forged_receipt_wrong_grant_node_claim_or_task_rejected(self):
        task,claim,grant=self.begin()
        payload={'claim_id':claim['claim_id'],'start_grant_id':grant['start_grant_id'],'result':result()}
        for key in ['claim_id','start_grant_id']:
            wrong=dict(payload,**{key:'wrong'})
            self.assert_error('harness_receipt_identity_mismatch',lambda:self.store.report_harness_result(SOURCE,task['remote_task_id'],'node','executor',wrong))
        self.store.provision_node(SOURCE,'other','other')
        self.assert_error('harness_node_mismatch',lambda:self.store.report_harness_result(SOURCE,task['remote_task_id'],'other','other',payload))
        self.assert_error('not_found',lambda:self.store.report_harness_result(SOURCE,'wrong','node','executor',payload))

    def test_result_strict_native_proof_model_output_and_usage(self):
        task,claim,grant=self.begin()
        values=[]
        for change in [lambda r:r.update(output_blocks=[]), lambda r:r['native_completion'].update(confirmed=1),
                       lambda r:r.update(usage={'state':'unknown','total_tokens':0}),
                       lambda r:r.update(output_blocks=[{'type':'tool_call','text':'not final'}])]:
            value=result();change(value);values.append(value)
        for value in values:
            self.assert_error('invalid_harness_result',lambda:self.finish(task,claim,grant,value))
        value=result();value['model_evidence']['reported']='fake-wrong'
        self.assert_error('harness_model_mismatch',lambda:self.finish(task,claim,grant,value))
        self.assertEqual(self.store.get_task(SOURCE,task['remote_task_id'])['outcome'],'pending')

    def test_cancel_before_start_atomic_block_and_running_cancel_keeps_slot(self):
        task=self.create()
        claim=self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor')
        cancelled=self.store.cancel_task(SOURCE,task['remote_task_id'])
        self.assertEqual(cancelled['outcome'],'cancelled')
        self.assert_error('task_not_executable',lambda:self.store.start_execution(SOURCE,task['remote_task_id'],claim['claim_id']))
        running,claim,grant=self.begin(self.create('next'))
        cancelled=self.store.cancel_task(SOURCE,running['remote_task_id'])
        self.assertEqual((cancelled['outcome'],cancelled['liveness']),('pending','unknown'))
        self.assert_error('queue_capacity',lambda:self.create('third'))
        completed=self.finish(running,claim,grant)
        self.assertEqual(completed['outcome'],'succeeded')
        self.assertEqual(self.store.cancel_task(SOURCE,running['remote_task_id'])['outcome'],'succeeded')

    def test_result_ttl_preserves_tombstone_and_pending_result(self):
        task,claim,grant=self.begin()
        self.finish(task,claim,grant)
        self.now+=10
        self.assertEqual(self.store.expire_harness_results(SOURCE,retention_seconds=1)['results_expired'],1)
        replay=self.create()
        self.assertTrue(replay['result_expired'])
        self.assertIsNone(replay['result'])
        self.assertEqual(replay['remote_task_id'],task['remote_task_id'])
        self.assertTrue(self.finish(task,claim,grant)['result_expired'])

    def test_unknown_terminal_result_not_expired(self):
        task,claim,grant=self.begin()
        self.finish(task,claim,grant,result(liveness='unknown'))
        self.now+=100
        self.assertEqual(self.store.expire_harness_results(SOURCE,retention_seconds=1)['results_expired'],0)

    def test_prestart_deadline_expiry_never_reexecutes_same_key(self):
        task=self.create()
        self.now+=3601
        replay=self.create()
        self.assertEqual(replay['remote_task_id'],task['remote_task_id'])
        self.assertEqual(replay['outcome'],'expired')
        self.assert_error('task_expired',lambda:self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor'))

    def test_observed_prestart_expiry_survives_clock_rollback(self):
        task = self.create()
        original = self.now
        self.now += 3601
        self.assertEqual(self.store.get_task(SOURCE,task['remote_task_id'])['outcome'],'expired')
        self.now = original
        self.assertEqual(self.create()['outcome'],'expired')
        self.assert_error('task_expired',lambda:self.store.claim_task(SOURCE,task['remote_task_id'],node_id='node',executor_instance='executor'))

    def test_image_upload_cannot_disguise_harness_output(self):
        task,claim,grant=self.begin()
        self.assert_error('harness_result_endpoint_required',lambda:self.store.create_upload(SOURCE,task['remote_task_id'],claim['claim_id'],
            {'attempt_id':task['attempt_id'],'slot_id':'primary','size':10,'mime_type':'image/png'}))

    def test_dedicated_harness_rejects_image_in_both_heartbeat_orders(self):
        for before in (True, False):
            if before:
                self.heartbeat()
            self.assert_error('harness_instance_image_disabled', lambda:
                self.store.report_heartbeat(SOURCE, 'node', 'executor', 'available', ['fake-image']))
            if not before:
                self.heartbeat()
            self.assertEqual(self.store.node_status(SOURCE)['reported_profiles'], ['harness-invoke'])
        self.assert_error('harness_instance_image_disabled', lambda:self.store.create_task(SOURCE, {'schema_version':'1'}))
        task = self.create()
        self.assertEqual(self.store.next_node_task(SOURCE, 'node', 'executor')['task_id'], task['remote_task_id'])

    def test_harness_ledger_cannot_be_reopened_in_image_mode(self):
        with self.assertRaisesRegex(ValueError, 'dedicated Harness'):
            BridgeStore(self.root/'bridge.db', self.root/'objects', dispatch_mode='node_pull')

    def test_image_ledger_cannot_be_reopened_in_harness_mode(self):
        db = self.root/'image.db'
        image = BridgeStore(db, self.root/'image-objects', dispatch_mode='node_pull')
        image.provision_node(SOURCE, 'node', 'executor')
        image.report_heartbeat(SOURCE, 'node', 'executor', 'available', ['fake-image'])
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('DROP TABLE instance_mode')  # pre-mode-pin ledger migration
        with self.assertRaisesRegex(ValueError, 'image heartbeats'):
            BridgeStore(db, self.root/'image-objects', dispatch_mode='node_pull', harness=config())
        self.assertEqual(image.node_status(SOURCE)['reported_profiles'], ['fake-image'])

    def test_completed_image_task_ledger_cannot_enable_harness(self):
        db = self.root/'image-task.db'
        image = BridgeStore(db, self.root/'image-task-objects')
        payload = {'schema_version':'1','origin_key':'image','binding_id':'binding',
                   'operation':'image.generate','execution_profile_id':'fake-image',
                   'execution_profile_revision':'1','prompt':'fixture','parameters':{'width':32,'height':32},
                   'references':[],'output_slot':{'slot_id':'primary','count':1},'routing':'bridge_only',
                   'deadline':datetime.now(timezone.utc).__add__(timedelta(hours=1)).isoformat()}
        task = image.create_task(SOURCE, payload)
        image.cancel_task(SOURCE, task['remote_task_id'])
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('DROP TABLE instance_mode')  # pre-mode-pin ledger migration
        with self.assertRaisesRegex(ValueError, 'image tasks'):
            BridgeStore(db, self.root/'image-task-objects', dispatch_mode='node_pull', harness=config())

    def test_empty_image_ledger_pins_mode_before_any_write(self):
        db = self.root/'empty-image.db'
        old = BridgeStore(db, self.root/'empty-objects', dispatch_mode='node_pull')
        with self.assertRaisesRegex(ValueError, 'mode mismatch'):
            BridgeStore(db, self.root/'empty-objects', dispatch_mode='node_pull', harness=config())
        old.provision_node(SOURCE, 'node', 'executor')
        self.assertTrue(old.report_heartbeat(SOURCE, 'node', 'executor', 'available', ['fake-image'])['accepted'])
        with old._connection() as con:
            self.assertEqual(con.execute('SELECT mode FROM instance_mode').fetchone()[0], 'image')
            self.assertIsNone(con.execute('SELECT 1 FROM harness_instance').fetchone())

    def test_competing_constructors_pin_exactly_one_mode(self):
        from threading import Barrier
        for iteration in range(8):
            db = self.root/f'race-{iteration}.db'
            objects = self.root/f'race-{iteration}'
            barrier = Barrier(2)
            def open_store(mode):
                barrier.wait()
                import time
                # SQLite's existing WAL/schema initialization may reject a
                # concurrent opener before mode validation. Retry that fail-closed
                # constructor, never a task or native execution.
                for retry in range(20):
                    try:
                        BridgeStore(db, objects, dispatch_mode='node_pull',
                                    harness=config() if mode == 'harness' else None)
                        return mode
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc) or retry == 19:
                            raise
                        time.sleep(0.01)
                    except ValueError as exc:
                        self.assertIn('mode mismatch', str(exc))
                        return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(open_store, ('image', 'harness')))
            winners = [value for value in results if value is not None]
            self.assertEqual(len(winners), 1)
            with closing(sqlite3.connect(db)) as con, con:
                self.assertEqual(con.execute('SELECT mode FROM instance_mode').fetchone()[0], winners[0])

    def test_expired_or_removed_binding_does_not_offer_work(self):
        self.create()
        self.now += 90
        self.assertIsNone(self.store.next_node_task(SOURCE, 'node', 'executor')['task_id'])
        self.heartbeat()
        self.store = self.new_store(bindings=())
        self.assertIsNone(self.store.next_node_task(SOURCE, 'node', 'executor')['task_id'])
        self.assert_error('harness_binding_unavailable', self.heartbeat)

    def test_ledger_identity_cannot_be_silently_replaced(self):
        self.create()
        for changes in [{'ledger_instance':'changed'},{'source_id':'changed'},{'node_id':'changed'}]:
            with self.assertRaises(ValueError):
                self.new_store(**changes)

    def test_storage_bound_rejects_before_acceptance(self):
        store=self.new_store(max_result_storage_bytes=100)
        self.assert_error('harness_storage_capacity',lambda:store.create_task(SOURCE,envelope()))


class HarnessHTTPTests(unittest.TestCase):
    """Same real store under existing FastAPI authentication and routes."""
    new_store = HarnessTests.new_store
    heartbeat = HarnessTests.heartbeat

    def setUp(self):
        HarnessTests.setUp(self)
        self.app=create_app(store=self.store,source_tokens={'source-test':SOURCE},node_tokens={'node-test':NODE})
        self.http=TestClient(self.app)
        self.addCleanup(self.http.close)

    def request(self, method,path,payload=None,node=False):
        return self.http.request(method,path,json=payload,headers={'Authorization':'Bearer '+('node-test' if node else 'source-test')})

    def test_http_typed_chain_and_ack_shape(self):
        hb=self.request('POST','/bridge/node/v1/harness-heartbeat',{'state':'available','binding_digests':[digest(BINDING.wire())]},True)
        self.assertEqual(hb.status_code,200,hb.text)
        created=self.request('POST','/bridge/v1/tasks',envelope())
        self.assertEqual(created.status_code,200,created.text)
        task=created.json(); prefix='/bridge/node/v1/tasks/'+task['remote_task_id']
        offer=self.request('GET','/bridge/node/v1/tasks/next',node=True)
        self.assertEqual(offer.json()['task_id'],task['remote_task_id'])
        claim=self.request('POST',prefix+'/claim',{'mode':'execute'},True).json()
        grant=self.request('POST',prefix+'/start',{'claim_id':claim['claim_id']},True).json()
        completed=self.request('POST',prefix+'/harness-result',{'claim_id':claim['claim_id'],'start_grant_id':grant['start_grant_id'],'result':result()},True)
        self.assertEqual(completed.status_code,200,completed.text)
        value=completed.json()
        self.assertEqual((value['claim_id'],value['start_grant_id'],value['result']),(claim['claim_id'],grant['start_grant_id'],result()))
        self.assertEqual(self.request('GET','/bridge/v1/tasks/'+task['remote_task_id']).json(),value)
        self.assertEqual(int(completed.headers['content-length']),len(completed.content))

    def test_http_missing_key_and_source_cannot_use_node_routes(self):
        self.assertEqual(self.http.get('/bridge/v1/capabilities').status_code,401)
        self.assertEqual(self.request('GET','/bridge/node/v1/tasks/next').status_code,401)

    def test_http_preadmission_error_marks_not_accepted(self):
        self.now+=100
        response=self.request('POST','/bridge/v1/tasks',envelope())
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers['x-native-accepted'],'false')

    def test_runtime_opt_in_scope_and_disabled_by_default(self):
        runtime=RuntimeConfig(self.root/'runtime','source','workspace','node','executor',dispatch_mode='node_pull')
        self.assertIsNone(runtime.harness_config())
        runtime=replace(runtime,harness={'ledger_instance':'ledger','allowed_owners':[['tenant','principal']],
              'bindings':[BINDING.wire()],'accepting_new':True})
        app=runtime.build_app({'BRIDGE_SOURCE_TOKEN':'source-config','BRIDGE_NODE_TOKEN':'node-config'})
        with TestClient(app) as client:
            response=client.get('/bridge/v1/capabilities',headers={'Authorization':'Bearer source-config'})
            self.assertEqual(response.json()['harness_contract'],CONTRACT)
        with self.assertRaises(ValueError):
            replace(runtime,harness={'source_id':'forged'}).validate()


class HarnessAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = BridgeStore(root/'db', root/'objects', dispatch_mode='node_pull', harness=config())
        self.store.provision_node(SOURCE, 'node', 'executor')
        self.store.report_harness_heartbeat(SOURCE, 'node', 'executor',
            {'state':'available','binding_digests':[digest(BINDING.wire())]})
        self.task = self.store.create_task(SOURCE, envelope())
        self.claim = self.store.claim_task(SOURCE, self.task['remote_task_id'], node_id='node', executor_instance='executor')
        app = create_app(store=self.store, source_tokens={'source-test':SOURCE}, node_tokens={'node-test':NODE})
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def contention(self, cancel):
        import asyncio
        import anyio
        import threading
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        waiting = asyncio.Event()
        original_get = self.store.get_task
        original_run = anyio.to_thread.run_sync
        calls = 0
        def slow_read(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release read')
            return original_get(*args)
        async def observed_run(func, *args, **kwargs):
            nonlocal calls
            if getattr(func, '__name__', '') == 'invoke':
                calls += 1
                if calls == 2:
                    waiting.set()
            return await original_run(func, *args, **kwargs)
        task_id = self.task['remote_task_id']
        with patch.object(self.store, 'get_task', side_effect=slow_read), patch.object(anyio.to_thread, 'run_sync', side_effect=observed_run):
            read = asyncio.create_task(self.client.get('/bridge/v1/tasks/'+task_id,
                headers={'Authorization':'Bearer source-test'}))
            start = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                start = asyncio.create_task(self.client.post('/bridge/node/v1/tasks/'+task_id+'/start',
                    json={'claim_id':self.claim['claim_id']}, headers={'Authorization':'Bearer node-test'}))
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertFalse(start.done())
                third = await self.client.get('/bridge/v1/tasks/'+task_id, headers={'Authorization':'Bearer source-test'})
                self.assertEqual(third.status_code, 503)
                self.assertEqual(third.json()['error']['code'], 'store_busy')
                if cancel:
                    start.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await start
                release.set()
                self.assertEqual((await read).status_code, 200)
                if not cancel:
                    response = await start
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()['may_execute'])
            finally:
                release.set()
                await asyncio.gather(*[t for t in (read, start) if t is not None], return_exceptions=True)
        current = original_get(SOURCE, task_id)
        self.assertEqual(current['start_grant_id'] is None, cancel)
        # Admission is reusable after both completed and canceled waiter paths.
        check = await self.client.get('/bridge/v1/tasks/'+task_id, headers={'Authorization':'Bearer source-test'})
        self.assertEqual(check.status_code, 200)

    async def test_read_and_start_share_bounded_admission_third_is_rejected(self):
        await self.contention(False)

    async def test_canceled_waiting_start_never_creates_late_grant(self):
        await self.contention(True)

    async def test_cancel_running_read_keeps_worker_serialized_until_completion(self):
        import asyncio
        import threading
        from unittest.mock import patch
        entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        original = self.store.capabilities
        calls = 0
        def slow(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('test did not release read')
            else:
                second_entered.set()
            return original(*args)
        with patch.object(self.store, 'capabilities', side_effect=slow):
            first = asyncio.create_task(self.client.get('/bridge/v1/capabilities',
                headers={'Authorization':'Bearer source-test'}))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(self.client.get('/bridge/v1/capabilities',
                    headers={'Authorization':'Bearer source-test'}))
                self.assertFalse(await asyncio.to_thread(second_entered.wait, 0.2))
                self.assertFalse(second.done())
                third = await self.client.get('/bridge/v1/capabilities', headers={'Authorization':'Bearer source-test'})
                self.assertEqual(third.status_code, 503)
                release.set()
                self.assertEqual((await second).status_code, 200)
                self.assertTrue(second_entered.is_set())
            finally:
                release.set()
                await asyncio.gather(*[t for t in (first, second) if t is not None], return_exceptions=True)
