"""Durable synthetic events; no native program, account, external service or deployment."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from render_bridge.api import create_app
from render_bridge.domain import DomainError, SourceScope
from render_bridge.harness_events import MAX_PAGE_BYTES, MAX_TEXT_BYTES, MAX_NATIVE_EVENTS
from render_bridge.harness import canonical
import test_harness as fixture
from test_harness import SOURCE, NODE, envelope

class HarnessEventTests(unittest.TestCase):
    setUp=fixture.HarnessTests.setUp
    new_store=fixture.HarnessTests.new_store
    heartbeat=fixture.HarnessTests.heartbeat
    create=fixture.HarnessTests.create
    begin=fixture.HarnessTests.begin
    finish=fixture.HarnessTests.finish
    assert_error=fixture.HarnessTests.assert_error

    def event(self,claim,grant,seq=1,event=None):
        return {'claim_id':claim['claim_id'],'start_grant_id':grant['start_grant_id'],'event_seq':seq,
                'event':event or {'type':'text_snapshot','text':'first'}}
    def post(self,task,claim,grant,seq=1,event=None):
        return self.store.report_harness_event(SOURCE,task['remote_task_id'],'node','executor',self.event(claim,grant,seq,event))
    def page(self,task,after=0,limit=8):
        return self.store.harness_events(SOURCE,task['remote_task_id'],after=after,limit=limit)
    def test_live_page_before_completion_and_restart(self):
        task,claim,grant=self.begin()
        ack=self.post(task,claim,grant)
        self.assertEqual(ack,{'remote_task_id':task['remote_task_id'],'claim_id':claim['claim_id'],'start_grant_id':grant['start_grant_id'],'event_seq':1,'accepted':True})
        page=self.page(task)
        self.assertEqual([e['type'] for e in page['events']],['task_status','task_status','text_snapshot'])
        self.assertEqual(page['events'][-1]['data'],{'text':'first'});self.assertIsNone(page['terminal_seq'])
        restarted=self.new_store()
        self.assertEqual(page,restarted.harness_events(SOURCE,task['remote_task_id']))
        self.assertIsNone(restarted.get_task(SOURCE,task['remote_task_id'])['result'])
    def test_snapshots_replace_and_idle_never_terminal(self):
        t,c,g=self.begin();self.post(t,c,g,event={'type':'text_snapshot','text':'abcdef'})
        self.post(t,c,g,2,{'type':'text_snapshot','text':'ab'});self.post(t,c,g,3,{'type':'text_snapshot','text':''})
        self.post(t,c,g,4,{'type':'native_status','state':'idle','stop_reason':'end_turn'})
        p=self.page(t);self.assertIsNone(p['terminal_seq']);self.assertEqual([e['data'].get('text') for e in p['events'][2:5]],['abcdef','ab',''])
    def test_concurrent_duplicate_exactly_one(self):
        t,c,g=self.begin()
        with ThreadPoolExecutor(max_workers=8) as pool:acks=list(pool.map(lambda _:self.post(t,c,g),range(32)))
        self.assertTrue(all(a==acks[0] for a in acks));self.assertEqual(len(self.page(t)['events']),3)
    def test_sequence_conflict_gap_and_receipt_scope(self):
        t,c,g=self.begin();self.post(t,c,g)
        self.assert_error('harness_event_conflict',lambda:self.post(t,c,g,event={'type':'text_snapshot','text':'changed'}))
        self.assert_error('harness_event_sequence_gap',lambda:self.post(t,c,g,3))
        wrong=dict(g,start_grant_id='different');self.assert_error('harness_receipt_identity_mismatch',lambda:self.post(t,c,wrong,2))
        with self.assertRaises(DomainError):self.store.harness_events(SourceScope('other','workspace'),t['remote_task_id'])
    def test_terminal_is_durable_once_and_late_duplicate_only(self):
        t,c,g=self.begin();self.post(t,c,g);self.finish(t,c,g)
        p=self.page(t);self.assertEqual(p['terminal_seq'],4);self.assertTrue(p['has_more'])
        terminal=self.page(t,p['next_cursor']);self.assertEqual([e['type'] for e in terminal['events']],['task_terminal'])
        self.finish(t,c,g);self.post(t,c,g)
        self.assertEqual(self.page(t),p)
        self.assert_error('harness_terminal_conflict',lambda:self.post(t,c,g,2))
        empty=self.page(t,4);self.assertEqual(empty['events'],[]);self.assertEqual(empty['terminal_seq'],4);self.assertFalse(empty['has_more'])
        self.assert_error('event_cursor_ahead',lambda:self.page(t,5))
    def test_cancel_before_start_and_after_start_semantics(self):
        t=self.create();self.store.cancel_task(SOURCE,t['remote_task_id'])
        self.assertIsNotNone(self.page(t)['terminal_seq'])
        self.heartbeat();t,c,g=self.begin(self.create('after'));self.store.cancel_task(SOURCE,t['remote_task_id'])
        p=self.page(t);self.assertIsNone(p['terminal_seq']);self.assertEqual(p['events'][-1]['data']['code'],'CANCEL_REQUESTED')
    def test_observation_unknown_is_live_status_not_terminal(self):
        t,c,g=self.begin()
        payload={'claim_id':c['claim_id'],'start_grant_id':g['start_grant_id'],'outcome':'pending','liveness':'unknown','code':'EXECUTION_UNCERTAIN'}
        self.store.report_harness_observation(SOURCE,t['remote_task_id'],'node','executor',payload)
        p=self.page(t);self.assertEqual(p['events'][-1]['data'],{'outcome':'pending','liveness':'unknown','code':'EXECUTION_UNCERTAIN'});self.assertIsNone(p['terminal_seq'])
        self.store.report_harness_observation(SOURCE,t['remote_task_id'],'node','executor',payload);self.assertEqual(self.page(t),p)
    def test_native_budget_failure_does_not_destroy_terminal_capacity(self):
        t,c,g=self.begin()
        with patch('render_bridge.harness_events.MAX_TASK_EVENT_BYTES',1):
            self.assert_error('harness_event_capacity',lambda:self.post(t,c,g))
        self.assertEqual(len(self.page(t)['events']),2)
        self.finish(t,c,g);self.assertEqual(self.page(t)['terminal_seq'],3)
    def test_page_wire_bound_and_cursor_contiguity(self):
        t,c,g=self.begin();text='\x01'*MAX_TEXT_BYTES
        self.post(t,c,g,event={'type':'text_snapshot','text':text});self.post(t,c,g,2,{'type':'text_snapshot','text':text})
        first=self.page(t);self.assertLessEqual(len(canonical(first).encode()),MAX_PAGE_BYTES);self.assertTrue(first['has_more'])
        second=self.page(t,first['next_cursor']);self.assertEqual(second['events'][0]['seq'],first['next_cursor']+1)
        self.assertFalse(second['has_more'])
    def test_schema_rejects_fake_terminal_credentials_and_oversize(self):
        t,c,g=self.begin()
        for event in ({'type':'completion','text':'fake'},{'type':'text_snapshot','text':'x','token':'hidden'},{'type':'text_snapshot','text':'x'*(MAX_TEXT_BYTES+1)},{'type':'native_status','state':'completed'},{'type':'native_status','state':'running','stop_reason':'unknown-secret'}):
            with self.subTest(kind=event['type']):self.assert_error('invalid_harness_event',lambda:self.post(t,c,g,event=event))
        for seq in (True,0,MAX_NATIVE_EVENTS+1):self.assert_error('invalid_harness_event',lambda:self.post(t,c,g,seq))
    def test_expiry_tombstones_stream_without_reexecution(self):
        t,c,g=self.begin();self.post(t,c,g);self.finish(t,c,g);self.now+=86401
        self.assertEqual(self.store.expire_harness_results(SOURCE)['results_expired'],1)
        self.assert_error('event_history_expired',lambda:self.page(t))
        self.assert_error('event_history_expired',lambda:self.post(t,c,g))
        self.assertTrue(self.store.get_task(SOURCE,t['remote_task_id'])['result_expired'])
    def test_old_ledger_has_no_fabricated_history(self):
        t,c,g=self.begin()
        with closing(sqlite3.connect(self.store.db_path)) as con,con:
            con.execute('DELETE FROM harness_events');con.execute('DELETE FROM harness_event_state')
        restarted=self.new_store()
        self.assert_error('event_history_unavailable',lambda:restarted.harness_events(SOURCE,t['remote_task_id']))
        self.assertEqual(restarted.get_task(SOURCE,t['remote_task_id'])['start_grant_id'],g['start_grant_id'])
    def test_http_auth_and_strict_cursor_and_live_event(self):
        t,c,g=self.begin();app=create_app(store=self.store,source_tokens={'source-secret':SOURCE},node_tokens={'node-secret':NODE})
        with TestClient(app) as client:
            url='/bridge/v1/tasks/'+t['remote_task_id']+'/harness-events'
            self.assertEqual(client.get(url).status_code,401)
            for query in ('?after=-1','?after=1&after=2','?after=nan','?after=0&extra=x','?limit=9'):
                self.assertEqual(client.get(url+query,headers={'Authorization':'Bearer source-secret'}).status_code,422)
            response=client.post('/bridge/node/v1/tasks/'+t['remote_task_id']+'/harness-event',json=self.event(c,g),headers={'Authorization':'Bearer node-secret'})
            self.assertEqual(response.status_code,200,response.text)
            p=client.get(url+'?after=0&limit=8',headers={'Authorization':'Bearer source-secret'})
            self.assertEqual(p.status_code,200,p.text);self.assertEqual(p.json()['events'][-1]['type'],'text_snapshot')

    def test_terminal_always_own_page_after_maximum_snapshot(self):
        t,c,g=self.begin();self.post(t,c,g,event={'type':'text_snapshot','text':'\x01'*MAX_TEXT_BYTES});self.finish(t,c,g)
        first=self.page(t);self.assertNotIn('task_terminal',[e['type'] for e in first['events']]);self.assertTrue(first['has_more'])
        terminal=self.page(t,first['next_cursor']);self.assertEqual(len(terminal['events']),1);self.assertEqual(terminal['events'][0]['type'],'task_terminal');self.assertFalse(terminal['has_more'])

    def test_read_checkpoints_expiry_without_reexecution_or_clock_revival(self):
        t,c,g=self.begin();before=self.page(t);self.now+=4000
        unknown=self.page(t);self.assertGreater(unknown['next_cursor'],before['next_cursor'])
        self.assertEqual(unknown['events'][-1]['data']['liveness'],'unknown');self.assertIsNone(unknown['terminal_seq'])
        self.now-=4000;self.assertEqual(self.page(t),unknown)
        self.assertEqual(self.store.get_task(SOURCE,t['remote_task_id'])['start_grant_id'],g['start_grant_id'])
    def test_read_checkpoints_prestart_expiry_terminal_once(self):
        t=self.create();self.now+=4000;p=self.page(t);self.assertIsNotNone(p['terminal_seq'])
        terminal=self.page(t,p['next_cursor']);self.assertEqual(terminal['events'][0]['type'],'task_terminal')
        self.now-=4000;self.assertEqual(self.page(t),p)

    def test_global_native_budget_explicit_failure_preserves_ledger(self):
        t,c,g=self.begin()
        with patch('render_bridge.harness_events.MAX_EVENT_STORAGE_BYTES',1):
            self.assert_error('harness_event_capacity',lambda:self.post(t,c,g))
        self.assertEqual(self.page(t)['next_cursor'],2)
        self.finish(t,c,g);self.assertEqual(self.page(t)['terminal_seq'],3)
        self.heartbeat()
        with patch('render_bridge.harness_events.MAX_EVENT_STORAGE_BYTES',1):
            self.assert_error('harness_event_capacity',lambda:self.create('capacity-blocked'))
    def test_system_status_budget_reserves_terminal(self):
        t,c,g=self.begin()
        payload={'claim_id':c['claim_id'],'start_grant_id':g['start_grant_id'],'outcome':'pending','liveness':'unknown','code':'EXECUTION_UNCERTAIN'}
        with patch('render_bridge.harness_events.MAX_SYSTEM_EVENTS',2):
            self.assert_error('harness_event_capacity',lambda:self.store.report_harness_observation(SOURCE,t['remote_task_id'],'node','executor',payload))
            self.finish(t,c,g)
        self.assertEqual(self.page(t)['terminal_seq'],3)
