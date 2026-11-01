import unittest
from unittest.mock import patch
import test_domain
from render_bridge.agent.health import ProfileHealthReporter
from render_bridge.agent.journal import AgentJournal
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.executors.codex import CodexExecutor, CodexConfig


class AvailabilityTests(unittest.TestCase):
    setUp = test_domain.DomainTests.setUp
    reopen = test_domain.DomainTests.reopen
    payload = test_domain.DomainTests.payload
    task = test_domain.DomainTests.task
    started = test_domain.DomainTests.started

    def report(self, revision, health='available', reason='ready', evidence='local_probe', profile='fake-image', generation=0):
        self.store.provision_node(self.scope, 'node', 'instance')
        return self.store.report_heartbeat(self.scope, 'node', 'instance', 'available', [profile],
            [dict(profile_id=profile, profile_revision='1', report_revision=revision,
                  health=health, reason_code=reason, evidence_kind=evidence, revalidation_generation=generation)])

    def fact(self):
        return self.store.capabilities(self.scope)['routing_availability_v1']['profiles'][0]

    def test_missing_expiry_reordered_restart(self):
        self.assertEqual('unknown', self.fact()['health'])
        self.report(1); first = self.fact(); self.assertEqual('available', first['health'])
        self.now += 30; self.report(1); self.assertEqual(first, self.fact())
        self.now += 60; self.assertEqual('stale', self.fact()['reason_code'])
        self.store = self.reopen(); self.assertEqual(1, self.store.routing_availability(self.scope)['revision'])
        self.report(0+1); self.assertEqual('stale', self.fact()['reason_code'])
        self.report(2, 'unavailable', 'tool_missing'); self.assertEqual('unavailable', self.fact()['health'])
        self.report(1); self.assertEqual('unavailable', self.fact()['health'])
        self.store.report_heartbeat(self.scope, 'node', 'instance', 'available', ['fake-image'])
        self.assertEqual('unknown', self.fact()['health'])

    def test_reason_specific_latch_and_no_duplicate_hysteresis(self):
        self.report(1, 'unavailable', 'auth_unavailable', 'provider_error')
        self.now += 90; self.report(2); self.assertEqual('auth_unavailable', self.fact()['reason_code'])
        self.report(3, evidence='operator_attested_preflight'); self.assertEqual('unavailable', self.fact()['health'])
        self.report(4, evidence='operator_validation', generation=1); self.assertEqual('available', self.fact()['health'])
        # Alchemy alone applies recovery hysteresis to distinct fresh observations.
        self.store = self.reopen(); self.assertEqual('available', self.fact()['health'])

    def test_transient_failure_busy_and_revocation_are_separate(self):
        self.report(1, 'unavailable', 'probe_failed'); self.assertEqual('unknown', self.fact()['health'])
        self.report(2, 'unavailable', 'probe_failed'); self.assertEqual('unavailable', self.fact()['health'])
        self.report(3); self.now += 60; self.report(4); self.assertEqual('available', self.fact()['health'])
        self.started(); self.assertEqual('busy', self.fact()['capacity']); self.assertEqual('available', self.fact()['health'])
        self.store.accepting_new_tasks = False
        caps = self.store.capabilities(self.scope); self.assertFalse(caps['admission']['accepting_new_tasks'])
        self.assertFalse(caps['profiles'][0]['production_verified'])
        self.store.set_node_active(self.scope, 'node', 'instance', False)
        self.assertEqual('unknown', self.fact()['health'])

    def test_profile_isolation(self):
        self.store.enabled_profiles = ('fake-image', 'codex-image')
        self.report(1)
        self.report(1, 'unavailable', 'tool_missing', profile='codex-image')
        # Explicit omission invalidates previous positive fact, never makes another healthy.
        facts = self.store.routing_availability(self.scope)['profiles']
        self.assertEqual(['unknown', 'unavailable'], [x['health'] for x in facts])

    def test_agent_safe_probe_revision_persistence_and_cache(self):
        journal = AgentJournal(self.root/'agent')
        executor = CodexExecutor(self.root/'exec', CodexConfig())
        registry = ExecutorRegistry({'codex-image': executor}, health_reporting_enabled=True)
        probe = lambda executable: dict(cli_version='codex-cli 0.163.0', schema_version_matches=True, stdio_advertised=True)
        reporter = ProfileHealthReporter(journal, registry, clock=lambda: self.now, codex_probe=probe)
        first = reporter.reports(); self.assertEqual('unknown', first[0]['health'])
        self.assertEqual(first, reporter.reports())
        self.now += 30; self.assertEqual(2, reporter.reports()[0]['report_revision'])
        reporter = ProfileHealthReporter(AgentJournal(self.root/'agent'), registry, codex_probe=lambda _: {'code': 'codex_unavailable'})
        self.assertEqual('tool_missing', reporter.reports()[0]['reason_code'])
        self.assertEqual(3, reporter.reports()[0]['report_revision'])

    def test_omission_and_unrelated_probe_cannot_erase_auth_latch(self):
        self.report(1, 'unavailable', 'quota_exhausted', 'provider_error')
        self.store.report_heartbeat(self.scope, 'node', 'instance', 'available', ['fake-image'])
        self.report(2); self.now += 60; self.report(3)
        self.assertNotEqual('available', self.fact()['health'])
        self.report(4, 'unavailable', 'tool_missing')
        self.report(5); self.now += 60; self.report(6)
        self.assertNotEqual('available', self.fact()['health'])

    def test_fresh_observation_after_gap_is_raw_fact_not_routing_authority(self):
        self.report(1); self.now += 90; self.assertEqual('unknown', self.fact()['health'])
        self.report(2); self.assertEqual('available', self.fact()['health'])

    def test_manual_revalidation_generation_is_not_replayed_after_restart(self):
        journal = AgentJournal(self.root/'agent')
        self.assertFalse(journal.revalidation_active(0, self.now))
        self.assertTrue(journal.revalidation_active(1, self.now))
        self.assertTrue(AgentJournal(self.root/'agent').revalidation_active(1, self.now+60))
        self.assertFalse(journal.revalidation_active(1, self.now+120))
        self.assertFalse(journal.revalidation_active(0, self.now+121))
        self.assertTrue(journal.revalidation_active(2, self.now+121))

    def test_accepted_config_health_has_attested_evidence_and_missing_policy_blocks(self):
        from render_bridge.executors.base import ExecutionRequest
        probe = lambda _: dict(cli_version='codex-cli 0.163.0', schema_version_matches=True, stdio_advertised=True)
        for policy, expected in (('', 'unknown'), ('on-request', 'available')):
            config = CodexConfig(enabled=True, model='model', image_capability_verified=True,
                                 accepted_cli_version='codex-cli 0.163.0',
                                 tool_policy_verified=True, approval_policy=policy)
            executor = CodexExecutor(self.root/'exec', config)
            reporter = ProfileHealthReporter(AgentJournal(self.root/'agent'),
                ExecutorRegistry({'codex-image':executor}, health_reporting_enabled=True), codex_probe=probe)
            report = reporter.reports()[0]
            self.assertEqual(expected, report['health'])
            if policy:
                self.assertEqual('operator_attested_preflight', report['evidence_kind'])
            else:
                with patch('render_bridge.executors.codex.probe_codex') as called:
                    self.assertEqual('codex_local_acceptance_required', executor.preflight(ExecutionRequest('task','attempt','prompt', {'width':32,'height':32})).code)
                    called.assert_not_called()
        accepted = CodexConfig(enabled=True, model='model', accepted_cli_version='codex-cli 0.163.0',
            image_capability_verified=True, tool_policy_verified=True, approval_policy='on-request')
        reporter = ProfileHealthReporter(AgentJournal(self.root/'agent'),
            ExecutorRegistry({'codex-image': CodexExecutor(self.root/'exec', accepted)}, health_reporting_enabled=True),
            codex_probe=lambda _: dict(cli_version='codex-cli 0.157.1', schema_version_matches=True, stdio_advertised=True))
        mismatch_report = reporter.reports()[0]
        self.assertEqual('unavailable', mismatch_report['health'])
        self.assertEqual('version_unsupported', mismatch_report['reason_code'])
        with self.assertRaises(ValueError): CodexConfig(approval_policy='untrusted')
