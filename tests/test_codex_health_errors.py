import unittest
from unittest.mock import patch
import test_agent_profiles
from render_bridge.executors.codex import CodexConfig, CodexExecutor, health_reason_from_codex_error
from render_bridge.executors.base import ExecutionRequest
from render_bridge.agent import NodeAgent
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.agent.health import ProfileHealthReporter


class CodexHealthErrorTests(unittest.TestCase):
    setUp = test_agent_profiles.AgentProfilesTests.setUp
    tearDown = test_agent_profiles.AgentProfilesTests.tearDown
    create = test_agent_profiles.AgentProfilesTests.create

    def test_strict_typed_error_allowlist(self):
        for info, expected in [('unauthorized','auth_unavailable'), ('usageLimitExceeded','quota_exhausted'),
                ('rateLimitExceeded','probe_failed'), ({'httpConnectionFailed':{'httpStatusCode':401}},'auth_unavailable'),
                ({'httpConnectionFailed':{'httpStatusCode':429}},'probe_failed'),
                ('badRequest',None), ('contextWindowExceeded',None), ('sandboxError',None),
                ('unauthorized private raw string',None), ({'httpConnectionFailed':{'httpStatusCode':'401'}},None),
                ({'httpConnectionFailed':{'httpStatusCode':400}},None)]:
            self.assertEqual(expected, health_reason_from_codex_error(info))

    def test_terminal_failed_turn_records_health_and_releases_slot_without_replay(self):
        for index, (info, expected) in enumerate([('unauthorized','auth_unavailable'),('usageLimitExceeded','quota_exhausted')]):
            task_id = self.create('codex-image', 'failure'+str(index))
            config = CodexConfig(enabled=True, model='accepted', accepted_cli_version='codex-cli 0.163.0', image_capability_verified=True,
                                 tool_policy_verified=True, approval_policy='on-request')
            executor = CodexExecutor(self.state/'executors', config)
            registry = ExecutorRegistry({'codex-image':executor}, health_reporting_enabled=True)
            agent = NodeAgent(self.client, self.state/'health-agent', registry=registry)
            good = lambda _:dict(cli_version='codex-cli 0.163.0', schema_version_matches=True, stdio_advertised=True)
            agent.health_reporter = ProfileHealthReporter(agent.journal, registry, clock=lambda:self.now,
                                                         wall_clock=lambda:self.now, codex_probe=good)
            def execute(request):
                return executor.collect_completed(request, thread_id='thread',turn_id='turn',events=[{
                    'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'failed','items':[],
                        'error':{'message':'PRIVATE PROVIDER TEXT','codexErrorInfo':info}}}}])
            with patch.object(executor, 'preflight', return_value=None), patch.object(executor, 'execute', side_effect=execute):
                result = agent.execute_task(task_id)
            self.assertEqual('terminal_failed', result['outcome'])
            self.assertEqual('failed', self.store.get_task(self.scope, task_id)['state'])
            fact = self.store.routing_availability(self.scope)['profiles'][1]
            self.assertEqual(expected, fact['reason_code']); self.assertEqual('unavailable', fact['health'])
            self.assertNotIn('PRIVATE', str(fact))
            fresh = ProfileHealthReporter(agent.journal, registry, codex_probe=good)
            self.assertEqual(expected, fresh.reports()[0]['reason_code'])
            self.assertFalse(agent.journal.occupied_by_other('new-task'))

    def test_unbound_failure_and_input_error_never_poison_profile(self):
        executor = CodexExecutor(self.state/'executors')
        request = ExecutionRequest('task','attempt','prompt',{'width':32,'height':32})
        for thread, info in [('other','unauthorized'), ('thread','badRequest')]:
            result = executor.collect_completed(request,thread_id='thread',turn_id='turn',events=[{
                'method':'turn/completed','params':{'threadId':thread,'turn':{'id':'turn','status':'failed','error':{'codexErrorInfo':info}}}}])
            self.assertNotIn('profile_health_reason', result.details)
