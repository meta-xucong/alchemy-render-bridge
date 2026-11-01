"""Bound terminal account failure recovery; synthetic events, no real CLI/account."""
import unittest
from unittest.mock import patch

import test_agent_profiles
from fastapi.testclient import TestClient
from render_bridge.runtime import create_runtime_app
from render_bridge.node_api import NodeScope
from render_bridge.agent import NodeAgent
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.agent.health import ProfileHealthReporter
from render_bridge.agent.journal import InstanceLock
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.executors.codex import CodexExecutor, CodexConfig
from render_bridge.executors.fake import FakeExecutor
from render_bridge.executors.base import ExecutionRequest, AttemptFiles
from render_bridge.fake import fixture_png


def events(info='unauthorized'):
    return [{'method': 'turn/completed', 'params': {'threadId': 'thread',
        'turn': {'id': 'turn', 'status': 'failed', 'items': [],
                 'error': {'codexErrorInfo': info, 'message': 'PRIVATE ERROR'}}}}]


class TerminalFailureTests(unittest.TestCase):
    setUp = test_agent_profiles.AgentProfilesTests.setUp
    tearDown = test_agent_profiles.AgentProfilesTests.tearDown
    create = test_agent_profiles.AgentProfilesTests.create

    def configured(self):
        executor = CodexExecutor(self.state/'executors', CodexConfig(enabled=True, model='fixture',
            accepted_cli_version='codex-cli 0.163.0',
            approval_policy='on-request', image_capability_verified=True, tool_policy_verified=True))
        registry = ExecutorRegistry({'codex-image': executor, 'fake-image': FakeExecutor(self.state/'executors')},
            health_reporting_enabled=True)
        agent = NodeAgent(self.client, self.state, registry=registry)
        agent.health_reporter = ProfileHealthReporter(agent.journal, registry, clock=lambda:self.now,
            codex_probe=lambda _:dict(cli_version='codex-cli 0.163.0', schema_version_matches=True, stdio_advertised=True),
            account_probe=lambda *_:True)
        return agent, executor

    def execute_failure(self, agent, executor, task, value=None):
        def execute(request):
            return executor.collect_completed(request, thread_id='thread', turn_id='turn',
                                              events=events() if value is None else value)
        with patch.object(executor, 'preflight', return_value=None), patch.object(executor, 'execute', side_effect=execute) as called:
            result = agent.execute_task(task)
        self.assertEqual(1, called.call_count)
        return result

    def assert_preserved(self, task):
        with self.store._connection() as con:
            attempt = con.execute('SELECT * FROM attempts WHERE task_id=?',(task,)).fetchone()
            self.assertTrue(attempt['start_grant_id'])
            self.assertEqual(('node','instance'), (attempt['original_node'],attempt['original_executor']))
        self.assertIsNone(self.store.get_task(self.scope,task)['receipt_id'])
        with self.assertRaises(DomainError):
            self.store.claim_task(self.scope,task,node_id='node',executor_instance='instance')
        task_value=self.store.get_task(self.scope,task)
        with self.assertRaises(DomainError):
            self.store.withdraw_before_start(self.scope,task,dict(schema_version='1',withdrawal_key='no',
                origin_key=task_value['origin_key'],attempt_id=task_value['attempt_id'],task_digest=task_value['task_digest']))

    def test_terminal_failure_recovery_then_new_local_execution_and_old_no_replay(self):
        agent, executor=self.configured(); task=self.create('codex-image')
        self.assertEqual('terminal_failed',self.execute_failure(agent,executor,task)['outcome'])
        self.assertEqual('failed',self.store.get_task(self.scope,task)['state'])
        self.assertFalse(agent.journal.occupied_by_other('next'))
        self.assert_preserved(task)
        agent.registry.account_health_probe_enabled=True;self.now+=31;agent.heartbeat()
        fact=self.store.routing_availability(self.scope)['profiles'][1]
        self.assertEqual(('available','idle'),(fact['health'],fact['capacity']))
        with patch.object(executor,'execute',side_effect=AssertionError('must not regenerate')):
            self.assertEqual('terminal_failed',agent.execute_task(task)['outcome'])
        request=ExecutionRequest(**agent.journal.get(task)['request'])
        with patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            self.assertEqual('terminal_failure',executor.execute(request).status)
        def succeed(request):
            files=AttemptFiles(executor.root,request,'codex-cli')
            original=files.path/'new.png';original.write_bytes(fixture_png(32,24))
            return executor.collect_completed(request,thread_id='new-thread',turn_id='new-turn',events=[
                {'method':'item/completed','params':{'threadId':'new-thread','turnId':'new-turn',
                 'item':{'id':'image','type':'imageGeneration','status':'completed','savedPath':str(original.resolve())}}},
                {'method':'turn/completed','params':{'threadId':'new-thread','turn':{'id':'new-turn','status':'completed'}}}])
        with patch.object(executor,'preflight',return_value=None),patch.object(executor,'execute',side_effect=succeed) as called:
            self.assertEqual('artifact_committed',agent.execute_task(self.create('codex-image','next'))['outcome'])
            self.assertEqual(1,called.call_count)

    def test_lost_report_response_restart_and_heartbeat_reconcile_without_old_task_redelivery(self):
        agent,executor=self.configured();task=self.create('codex-image');real=self.client.terminal_failure
        def lost(*args):
            real(*args)
            raise TimeoutError('response lost')
        with patch.object(self.client,'terminal_failure',side_effect=lost):
            self.assertEqual('failure_report_pending',self.execute_failure(agent,executor,task)['outcome'])
        self.assertTrue(agent.journal.occupied_by_other('next'))
        self.assertEqual('failed',self.store.get_task(self.scope,task)['state'])
        self.store.close()
        self.store=BridgeStore(self.root/'ledger.db',self.root/'blobs',clock=lambda:self.now,
            enabled_profiles=['fake-image','codex-image','midjourney-manual'])
        app=create_runtime_app(store=self.store,source_tokens={'source':self.scope},
            node_tokens={'node':NodeScope('source','workspace','node','instance')},
            dispatch_tokens={'dispatch':self.scope})
        self.client.http.close()
        self.client.http=TestClient(app,base_url='http://127.0.0.1:8787',headers={'Authorization':'Bearer node'})
        # Both server instance and Agent now reload their independent durable state.
        resumed,_=self.configured()
        with patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            resumed.heartbeat()
        self.assertEqual('terminal_failed',resumed.journal.get(task)['state'])
        self.assertFalse(resumed.journal.occupied_by_other('next'))
        self.assertEqual('artifact_committed',resumed.execute_task(self.create('fake-image','next'))['outcome'])
        self.assert_preserved(task)

    def test_pre_report_failure_stays_occupied_until_new_task_edge_reconciles(self):
        agent,executor=self.configured();task=self.create('codex-image')
        with patch.object(self.client,'terminal_failure',side_effect=TimeoutError('not sent')):
            self.assertEqual('failure_report_pending',self.execute_failure(agent,executor,task)['outcome'])
            agent.heartbeat()
            self.assertTrue(agent.journal.occupied_by_other('next'))
        self.assertEqual('artifact_committed',agent.execute_task(self.create('fake-image','next'))['outcome'])
        self.assertEqual('failed',self.store.get_task(self.scope,task)['state'])

    def test_crash_after_completion_before_journal_projection_is_recovered_from_same_evidence(self):
        agent,executor=self.configured();task=self.create('codex-image')
        # Inject process death after durable Codex completion, before Agent's
        # failure_report_pending projection. BaseException bypasses error handling.
        with patch.object(agent,'_save_terminal_failure',side_effect=SystemExit('crash')):
            with self.assertRaises(SystemExit):self.execute_failure(agent,executor,task)
        self.assertEqual('start_requested',agent.journal.get(task)['state'])
        resumed,_=self.configured()
        with patch('subprocess.Popen',side_effect=AssertionError('must not launch')):resumed.heartbeat()
        self.assertEqual('terminal_failed',resumed.journal.get(task)['state'])
        self.assertEqual('failed',self.store.get_task(self.scope,task)['state'])

    def test_genuine_unknown_and_image_activity_are_never_released_by_health(self):
        cases=[events('internalServerError'),events(),events()]
        del cases[1][0]['params']['turn']['items']
        cases[2].insert(0,{'method':'item/started','params':{'threadId':'thread','turnId':'turn',
            'item':{'type':'imageGeneration','id':'image','status':'inProgress'}}})
        for i,value in enumerate(cases):
            with self.subTest(i=i):
                # Isolated trusted-node fixture per case, never reset occupancy.
                if i:self.tearDown();self.setUp()
                agent,executor=self.configured();task=self.create('codex-image')
                self.assertEqual('execution_unknown',self.execute_failure(agent,executor,task,value)['outcome'])
                agent.registry.account_health_probe_enabled=True;self.now+=31;agent.heartbeat()
                self.assertTrue(agent.journal.occupied_by_other('next'))
                self.assertEqual('physical_slot_busy',agent.execute_task(self.create('fake-image','next'))['outcome'])
                self.assertEqual('execution_unknown',self.store.get_task(self.scope,task)['state'])

    def test_node_report_binding_replay_scope_and_strict_shape(self):
        agent,executor=self.configured();task=self.create('codex-image')
        self.execute_failure(agent,executor,task)
        record=agent.journal.get(task); evidence=record['terminal_failure']
        one=self.client.terminal_failure(task,record['attempt_id'],evidence)
        two=self.client.terminal_failure(task,record['attempt_id'],evidence)
        self.assertEqual(one,two);self.assertNotIn('PRIVATE',str(one))
        body={'attempt_id':record['attempt_id'],'evidence':evidence}
        for changed in ({**body,'attempt_id':'wrong'}, {**body,'evidence':{**evidence,'turn_id':'other'}}):
            response=self.client.http.post('/bridge/node/v1/tasks/'+task+'/terminal-failure',json=changed)
            self.assertEqual(409,response.status_code)
        response=self.client.http.post('/bridge/node/v1/tasks/'+task+'/terminal-failure',json={**body,'unexpected':True})
        self.assertEqual(422,response.status_code)
        with self.assertRaises(DomainError):self.store.confirm_terminal_failure(self.scope,task,'other','instance',body)
        with self.assertRaises(DomainError):self.store.confirm_terminal_failure(SourceScope('other','workspace'),task,'node','instance',body)

    def test_heartbeat_cannot_reconcile_while_execution_instance_lock_is_held(self):
        agent,executor=self.configured();task=self.create('codex-image')
        with patch.object(self.client,'terminal_failure',side_effect=TimeoutError('not sent')):
            self.execute_failure(agent,executor,task)
        with InstanceLock(agent.journal.root/'agent.lock'):
            agent.heartbeat()
        self.assertEqual('failure_report_pending',agent.journal.get(task)['state'])
        agent.heartbeat()
        self.assertEqual('terminal_failed',agent.journal.get(task)['state'])
