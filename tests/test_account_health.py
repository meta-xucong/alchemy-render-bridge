import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from render_bridge.agent.account_health import probe_account_health
from render_bridge.agent.config import AgentConfig
from render_bridge.agent.health import ProfileHealthReporter
from render_bridge.agent.journal import AgentJournal
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.executors.codex import CodexConfig, CodexExecutor
from render_bridge.executors.base import ExecutionResult
import test_availability


class AccountProbeTests(unittest.TestCase):
    def test_status_only_rpc_shape_and_strict_boolean(self):
        for allowed, expected in [(True,True),(False,False),(None,None),(1,None),('true',None)]:
            calls = []
            class Session:
                def __init__(self, argv, cwd, timeout):
                    self.assertion = (argv, timeout)
                    assert argv == ['local-cli','app-server','--stdio'] and timeout == 3
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def request(self, method, params):
                    calls.append((method,params))
                    return {'ordinaryUsageAllowed':allowed,'accountId':'PRIVATE','rateLimits':{'secret':'PRIVATE'}}
                def send(self, value): calls.append(value)
            result = probe_account_health('local-cli', Path('/tmp'), session_factory=Session)
            self.assertIs(expected, result)
            self.assertEqual(['initialize','account/rateLimits/read'], [c[0] for c in calls if isinstance(c,tuple)])
            self.assertEqual({'supportsLunaReserve':False,'excludeResetCreditDetails':True}, calls[-1][1])
            self.assertNotIn('PRIVATE', str(result))
        def denied(*args,**kwargs): raise PermissionError('PRIVATE approval required')
        self.assertIsNone(probe_account_health('cli',Path('/tmp'),session_factory=denied))

    def test_probe_opt_in_and_actual_cause_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); journal = AgentJournal(root)
            config = CodexConfig(enabled=True,model='accepted',image_capability_verified=True,
                                 tool_policy_verified=True,approval_policy='on-request')
            executor = CodexExecutor(root/'exec',config)
            registry = ExecutorRegistry({'codex-image':executor},health_reporting_enabled=True)
            good = lambda _: {'schema_version_matches':True,'stdio_advertised':True}
            def reporter(account_probe):
                return ProfileHealthReporter(journal,registry,codex_probe=good,account_probe=account_probe)
            with patch('render_bridge.agent.account_health.StdioSession', side_effect=AssertionError('not enabled')):
                self.assertEqual('available', reporter(lambda *_:self.fail('opt out must not probe')).reports()[0]['health'])
            r = reporter(lambda *_:None)
            r.record_result('codex-image',ExecutionResult('execution_unknown','x',details={'profile_health_reason':'auth_unavailable'}),'attempt')
            registry.account_health_probe_enabled = True
            self.assertEqual('auth_unavailable', reporter(lambda *_:None).reports()[0]['reason_code'])
            self.assertEqual('probe_failed', reporter(lambda *_:False).reports()[0]['reason_code'])
            result = reporter(lambda *_:True).reports()[0]
            self.assertEqual(('available','account_status'),(result['health'],result['evidence_kind']))
            self.assertIsNone(journal.health_fault('codex-image'))
            unknown = reporter(lambda *_:None).reports()[0]
            self.assertEqual(('unknown','unverified','none'), (unknown['health'],unknown['reason_code'],unknown['evidence_kind']))
            self.assertFalse(AgentConfig().account_health_probe_enabled)


class RevalidationFenceTests(unittest.TestCase):
    setUp = test_availability.AvailabilityTests.setUp
    reopen = test_availability.AvailabilityTests.reopen
    report = test_availability.AvailabilityTests.report
    fact = test_availability.AvailabilityTests.fact

    def test_same_operator_generation_cannot_clear_later_fault(self):
        self.report(1,'unavailable','auth_unavailable','provider_error')
        self.report(2,evidence='operator_validation',generation=1)
        self.assertEqual('available',self.fact()['health'])
        self.report(3,'unavailable','quota_exhausted','provider_error',generation=1)
        self.store = self.reopen()
        self.report(4,evidence='operator_validation',generation=1)
        self.assertEqual('unavailable',self.fact()['health'])
        self.report(5,evidence='operator_validation',generation=2)
        self.assertEqual('available',self.fact()['health'])

    def test_account_status_clears_only_with_fresh_positive_fact(self):
        self.report(1,'unavailable','quota_exhausted','account_status')
        self.report(1,evidence='account_status')
        self.assertEqual('unavailable',self.fact()['health'])
        self.report(2,evidence='account_status')
        self.assertEqual('available',self.fact()['health'])
