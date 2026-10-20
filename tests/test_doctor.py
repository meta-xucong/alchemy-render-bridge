"""Bounded offline readiness diagnostics; no real accounts, CLI turns or network."""
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from render_bridge.agent.config import AgentConfig
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.doctor import EXIT_CODES, inspect_local, main, render_human
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.executors.codex import CodexConfig, PINNED_CLI_VERSION, probe_codex
from render_bridge.profiles import get_profile
from render_bridge.runtime import RuntimeConfig


class AgentConfigTests(unittest.TestCase):
    def test_existing_default_and_real_configs_remain_valid(self):
        self.assertEqual(AgentConfig.load().enabled_profiles, ('fake-image',))
        self.assertEqual(AgentConfig.from_mapping({}).codex, CodexConfig())
        config = AgentConfig.from_mapping({'enabled_profiles': ['fake-image', 'codex-image', 'midjourney-manual'],
            'codex': {'enabled': True, 'model': 'accepted-model', 'timeout': 0.5,
                      'image_capability_verified': True, 'tool_policy_verified': True}})
        self.assertEqual(len(config.enabled_profiles), 3)
        self.assertTrue(config.codex.enabled)
        self.assertEqual(config.codex.timeout, 0.5)

    def test_bad_profiles_are_rejected_without_type_coercion(self):
        for value in ([], ['fake-image', 'fake-image'], ['unknown'], 'fake-image', None,
                      [None], [True], [{}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentConfig.from_mapping({'enabled_profiles': value})

    def test_bad_fields_and_string_booleans_are_rejected_even_if_codex_unused(self):
        bad_values = [None, [], {'token': 'do-not-print'}, {'codex': None},
                      {'codex': {'unknown': True}}, {'codex': {'executable': ''}},
                      {'codex': {'model': 42}}, {'codex': {'timeout': 0}},
                      {'codex': {'timeout': True}}, {'codex': {'timeout': float('nan')}},
                      {'codex': {'timeout': float('inf')}}]
        for field in ('enabled', 'image_capability_verified', 'tool_policy_verified'):
            for value in ('false', 'true', 0, 1, None):
                bad_values.append({'codex': {field: value}})
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentConfig.from_mapping(value)

    def test_registry_uses_same_loader_before_building_any_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'agent.json'
            for value in ({'enabled_profiles': []}, {'codex': {'enabled': 'false'}},
                          {'enabled_profiles': ['codex-image'], 'codex': {'model': 1}}):
                path.write_text(json.dumps(value))
                with patch('render_bridge.agent.registry.FakeExecutor') as fake, \
                     patch('render_bridge.agent.registry.CodexExecutor') as codex:
                    with self.assertRaises(ValueError):
                        ExecutorRegistry.from_config(root / 'executors', path)
                    fake.assert_not_called()
                    codex.assert_not_called()
            self.assertFalse((root / 'executors').exists())


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge_path = self.root / 'bridge.json'
        self.agent_path = self.root / 'agent.json'
        self.bridge = {'data_dir': str(self.root / 'must-not-exist'), 'source_id': 'source',
                       'workspace_id': 'workspace', 'node_id': 'node', 'executor_instance': 'instance'}
        self.agent = {}
        self.save()

    def save(self):
        self.bridge_path.write_text(json.dumps(self.bridge), encoding='utf-8')
        self.agent_path.write_text(json.dumps(self.agent), encoding='utf-8')

    def inspect(self, **kwargs):
        report = inspect_local(self.bridge_path, self.agent_path, **kwargs)
        self.assert_contract(report)
        return report

    def assert_contract(self, report):
        self.assertEqual(set(report), {'schema_version', 'kind', 'producer', 'outcome', 'profiles',
                                       'checks', 'real_generation_verified', 'login_verified'})
        self.assertEqual(report['schema_version'], '1')
        self.assertEqual(report['kind'], 'render_bridge_doctor')
        self.assertEqual(report['producer'], {'name': 'alchemy-render-bridge', 'version': '0.1.0a1'})
        self.assertIn(report['outcome'], EXIT_CODES)
        self.assertIs(report['real_generation_verified'], False)
        self.assertIs(report['login_verified'], False)
        self.assertEqual(len({c['id'] for c in report['checks']}), len(report['checks']))
        for check in report['checks']:
            self.assertEqual(set(check), {'id', 'status', 'evidence', 'code', 'next_step'})
            self.assertRegex(check['id'], '^[a-z][a-z0-9_]*$')
            self.assertRegex(check['code'], '^[a-z][a-z0-9_]*$')
            self.assertIn(check['status'], ('pass', 'attention', 'fail', 'not_checked'))
            self.assertIn(check['evidence'], ('configuration', 'local_probe', 'operator_declaration', 'not_checked'))
            self.assertTrue(check['next_step'])
            self.assertLessEqual(len(check['next_step']), 600)
        serialized = json.dumps(report)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn('do-not-print', serialized)

    @staticmethod
    def checks(report):
        return {item['id']: item for item in report['checks']}

    def test_default_is_only_two_file_reads_with_no_side_effects(self):
        original = Path.read_text
        reads = []
        def config_read(path, *args, **kwargs):
            self.assertIn(path, (self.bridge_path, self.agent_path))
            reads.append(path)
            return original(path, *args, **kwargs)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with ExitStack() as stack:
            for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket', 'sqlite3.connect',
                           'httpx.Client', 'httpx.AsyncClient', 'pathlib.Path.mkdir',
                           'pathlib.Path.write_text', 'pathlib.Path.write_bytes',
                           'render_bridge.doctor.probe_codex', 'tempfile.TemporaryDirectory'):
                stack.enter_context(patch(target, side_effect=AssertionError('unexpected side effect')))
            stack.enter_context(patch.object(RuntimeConfig, 'credentials', side_effect=AssertionError('credential access')))
            stack.enter_context(patch.object(RuntimeConfig, 'build_app', side_effect=AssertionError('runtime creation')))
            stack.enter_context(patch.object(Path, 'read_text', config_read))
            report = self.inspect()
        self.assertEqual(reads, [self.bridge_path, self.agent_path])
        self.assertEqual(report['outcome'], 'ready')
        self.assertEqual(report['profiles'], ['fake-image'])
        self.assertEqual(self.checks(report)['login']['status'], 'not_checked')
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_profile_mismatch_needs_attention_without_mutation(self):
        self.agent = {'enabled_profiles': ['midjourney-manual']}
        self.save()
        before = self.agent_path.read_bytes()
        report = self.inspect()
        self.assertEqual(report['outcome'], 'attention')
        self.assertEqual(self.checks(report)['profile_alignment']['code'], 'profiles_mismatch')
        self.assertEqual(before, self.agent_path.read_bytes())

    def test_string_false_is_invalid_not_truthy_acceptance(self):
        self.agent = {'enabled_profiles': ['codex-image'], 'codex': {'enabled': 'false'}}
        self.save()
        with patch('render_bridge.doctor.probe_codex') as probe:
            report = self.inspect(probe=True)
        probe.assert_not_called()
        self.assertEqual(report['outcome'], 'invalid')
        self.assertEqual(self.checks(report)['agent_config']['code'], 'agent_config_invalid')

    def test_malformed_and_unreadable_configs_return_stable_invalid(self):
        for value in ('not json do-not-print', 'null', '[]', '{"data_dir": "do-not-print"}'):
            self.bridge_path.write_text(value)
            report = self.inspect()
            self.assertEqual(report['outcome'], 'invalid')
        self.bridge_path.unlink()
        self.agent_path.write_text('{"codex": {"unknown": "do-not-print"}}')
        report = self.inspect()
        self.assertEqual(report['profiles'], [])
        self.assertEqual(report['outcome'], 'invalid')

    def codex_config(self, accepted=True):
        self.bridge['enabled_profiles'] = ['codex-image']
        self.agent = {'enabled_profiles': ['codex-image'], 'codex': {
            'enabled': accepted, 'model': 'do-not-print', 'image_capability_verified': accepted,
            'tool_policy_verified': accepted, 'executable': '/do-not-print/codex'}}
        self.save()

    def test_declarations_and_executable_are_not_real_verification(self):
        self.codex_config()
        with patch('render_bridge.doctor.shutil.which', return_value='/local/codex'), \
             patch('render_bridge.doctor.probe_codex') as probe:
            report = self.inspect()
        probe.assert_not_called()
        self.assertEqual(report['outcome'], 'attention')
        checks = self.checks(report)
        self.assertEqual(checks['codex_image_acceptance']['status'], 'pass')
        self.assertEqual(checks['codex_image_acceptance']['evidence'], 'operator_declaration')
        self.assertEqual(checks['codex_version']['status'], 'not_checked')
        self.assertEqual(checks['real_generation']['status'], 'attention')

    def test_missing_cli_and_disabled_declarations_are_actionable(self):
        self.codex_config(accepted=False)
        with patch('render_bridge.doctor.shutil.which', return_value=None):
            report = self.inspect()
        checks = self.checks(report)
        self.assertEqual(checks['codex_execution_enabled']['status'], 'attention')
        self.assertEqual(checks['codex_executable']['code'], 'codex_executable_missing')
        self.assertIn('Real generation verified: no', render_human(report))

    def test_explicit_probe_uses_configured_executable_but_keeps_real_unverified(self):
        self.codex_config()
        with patch('render_bridge.doctor.shutil.which', return_value='/local/codex'), \
             patch('render_bridge.doctor.probe_codex', return_value={
                 'cli_version': PINNED_CLI_VERSION, 'schema_version_matches': True, 'stdio_advertised': True}) as probe:
            report = self.inspect(probe=True)
        probe.assert_called_once_with('/do-not-print/codex')
        self.assertEqual(report['outcome'], 'attention')
        self.assertIn('Observed installed version: codex-cli 0.163.0.', self.checks(report)['codex_version']['next_step'])
        for key in ('codex_version', 'codex_protocol'):
            self.assertEqual(self.checks(report)[key]['status'], 'pass')
            self.assertEqual(self.checks(report)[key]['evidence'], 'local_probe')

    def test_probe_failure_and_stdio_absence_do_not_pass(self):
        for result in ({'code': 'codex_unavailable'}, {'cli_version': 'do-not-print'},
                       {'cli_version': 'codex-cli 0.163.0\ndo-not-print'},
                       {'cli_version': PINNED_CLI_VERSION, 'schema_version_matches': True, 'stdio_advertised': False}):
            with patch('render_bridge.doctor.shutil.which', return_value='/local/codex'), \
                 patch('render_bridge.doctor.probe_codex', return_value=result):
                report = self.inspect(probe=True)
            self.assertEqual(report['outcome'], 'attention')
            self.assertEqual(self.checks(report)['codex_protocol']['status'], 'attention')

    def test_cli_exit_codes_and_json_shape(self):
        for expected, profiles in ((0, ['fake-image']), (1, ['midjourney-manual']), (2, [])):
            self.bridge['enabled_profiles'] = profiles
            self.agent = {'enabled_profiles': profiles}
            self.save()
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['--bridge-config', str(self.bridge_path), '--agent-config', str(self.agent_path), '--json'])
            report = json.loads(output.getvalue())
            self.assert_contract(report)
            self.assertEqual(code, expected)
            self.assertEqual(code, EXIT_CODES[report['outcome']])


class ProbeAndDescriptorTests(unittest.TestCase):
    def test_probe_only_version_help_with_empty_home_and_no_secret_environment(self):
        homes = []
        def run(argv, **kwargs):
            env = kwargs['env']
            home = Path(env['CODEX_HOME'])
            homes.append(home)
            self.assertTrue(home.is_dir())
            self.assertEqual(list(home.iterdir()), [])
            self.assertEqual(env['HOME'], str(home))
            self.assertEqual(env['USERPROFILE'], str(home))
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('BRIDGE_SOURCE_TOKEN', env)
            self.assertNotIn('CODEX_API_KEY', env)
            self.assertEqual(kwargs['timeout'], 10)
            self.assertIn(argv, [['trusted-codex', '--version'], ['trusted-codex', 'app-server', '--help']])
            return types.SimpleNamespace(returncode=0, stdout=PINNED_CLI_VERSION if argv[-1] == '--version' else '--stdio')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'do-not-print', 'CODEX_API_KEY': 'do-not-print',
                                     'BRIDGE_SOURCE_TOKEN': 'do-not-print'}), \
             patch('render_bridge.executors.codex.subprocess.run', side_effect=run) as process:
            result = probe_codex('trusted-codex')
        self.assertEqual(process.call_count, 2)
        self.assertEqual(homes[0], homes[1])
        self.assertFalse(homes[0].exists())
        self.assertFalse(result['image_generation_verified'])
        self.assertTrue(result['schema_version_matches'])

    def test_capabilities_derive_fresh_descriptor_without_changing_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = BridgeStore(root / 'db', root / 'artifacts',
                                enabled_profiles=['fake-image', 'codex-image', 'midjourney-manual'])
            self.addCleanup(store.close)
            report = store.capabilities(SourceScope('source', 'workspace'))
            self.assertEqual(report['schema_version'], '1')
            for item in report['profiles']:
                profile = get_profile(item['execution_profile_id'])
                contract = item['parameter_contract']
                self.assertEqual(contract, profile.parameter_contract())
                self.assertEqual(item['execution_profile_revision'], '1')
                self.assertEqual(contract['schema_version'], '1')
                self.assertEqual(contract['accepted_parameters'], ['width', 'height', 'seed'])
                self.assertEqual(contract['dimension_semantics'], 'exact_output_match')
                self.assertEqual(contract['required_parameters'], [] if profile.id == 'fake-image' else ['width', 'height'])
                self.assertEqual(contract['unsupported_parameters'], ['quality', 'output_format', 'input_fidelity'])
                self.assertFalse(item['production_verified'])
                contract['accepted_parameters'].append('bad')
                self.assertNotIn('bad', profile.parameter_contract()['accepted_parameters'])
            self.assertEqual(get_profile('fake-image').seed_semantics, 'accepted_not_applied')
            self.assertEqual(get_profile('codex-image').executor_control, 'text_instruction_only')
            self.assertEqual(get_profile('midjourney-manual').reference_semantics, 'not_supported')


if __name__ == '__main__':
    unittest.main()
