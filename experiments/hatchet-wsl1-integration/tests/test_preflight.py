import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('preflight', Path(__file__).parents[1] / 'preflight.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PreflightSafetyTests(unittest.TestCase):
    def test_utf16_wsl_inventory(self):
        self.assertEqual(p.decode('Ubuntu\r\n'.encode('utf-16-le')), 'Ubuntu')

    def test_utf8_output(self):
        self.assertEqual(p.decode(b'Enabled\r\n'), 'Enabled')

    def test_digest_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.tar.gz'
            path.write_bytes(b'not Ubuntu')
            with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                p.verify_digest(path)

    def test_cleanup_rejects_existing_distro_name(self):
        with self.assertRaisesRegex(RuntimeError, 'unowned'):
            p.validate_state({'distro': 'Ubuntu', 'root': '/tmp/Ubuntu'}, Path('/tmp'))

    def test_cleanup_rejects_outside_root(self):
        name = 'bridge-wsl1-' + 'a' * 32
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            p.validate_state({'distro': name, 'root': '/elsewhere/' + name}, Path('/tmp'))

    def test_owned_cleanup_path(self):
        name = 'bridge-wsl1-' + 'a' * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / name
            self.assertEqual(p.validate_state({'distro': name, 'root': str(root)}, Path(directory)), root.resolve())

    def test_missing_state_cleanup_is_noop(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(p, 'run') as run:
            self.assertEqual(p.cleanup(Path(directory) / 'missing', Path(directory)), [])
            run.assert_not_called()

    def test_no_settings_mutators(self):
        source = Path(p.__file__).read_text()
        for forbidden in ('--set-default-version', '--set-version', '--shutdown',
                          'Enable-WindowsOptionalFeature', 'Set-NetFirewall', 'New-NetFirewall',
                          'netsh', '--install', 'Start-Service'):
            self.assertNotIn(forbidden, source)


class OrchestrationTests(unittest.TestCase):
    def exercise(self, *, disabled=False, import_failure=False, cleanup_errors=None):
        import contextlib
        import json
        import os
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            base = Path(directory)
            state, report = base / 'state.json', base / 'result.json'
            def fake_run(args, **kwargs):
                if args[0] == 'powershell.exe':
                    return 'Disabled' if disabled else 'Enabled'
                if '--import' in args and import_failure:
                    raise RuntimeError('simulated import failure')
                if '--verbose' in args and state.exists():
                    return json.loads(state.read_text())['distro'] + ' Stopped 1'
                return ''
            def fake_process(args, stdout, **kwargs):
                stdout.write(b'{"port": 43210, "bind": "127.0.0.1", "linux_listener_verified": true}\n')
                stdout.flush()
                process = MagicMock()
                process.poll.return_value = 0
                process.wait.return_value = 0
                return process
            stack.enter_context(patch.object(p.sys, 'platform', 'win32'))
            stack.enter_context(patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}))
            stack.enter_context(patch.object(p, 'run', side_effect=fake_run))
            stack.enter_context(patch.object(p, 'names', return_value=set()))
            stack.enter_context(patch.object(p, 'download'))
            stack.enter_context(patch.object(p.subprocess, 'Popen', side_effect=fake_process))
            connection = MagicMock()
            connection.__enter__.return_value.recv.return_value = b'bridge-wsl1-ok\n'
            stack.enter_context(patch.object(p.socket, 'create_connection', return_value=connection))
            clean = stack.enter_context(patch.object(p, 'cleanup', return_value=cleanup_errors or []))
            stack.enter_context(patch('builtins.print'))
            code = p.preflight(state, base, report)
            result = json.loads(report.read_text())
            clean.assert_called_once_with(state, base)
            return code, result

    def test_disabled_feature_is_blocked(self):
        code, result = self.exercise(disabled=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['stage'], 'existing_wsl1_feature')
        self.assertEqual(result['status'], 'blocked')

    def test_failed_import_is_blocked_and_cleanup_attempted(self):
        code, result = self.exercise(import_failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['stage'], 'wsl1_import')
        self.assertIn('import failure', result['error'])

    def test_success_does_not_claim_engine(self):
        code, result = self.exercise()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['engine_tested'])
        self.assertFalse(result['postgres_tested'])

    def test_cleanup_failure_overrides_success(self):
        code, result = self.exercise(cleanup_errors=['unregister failed'])
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'blocked')

    def test_malformed_state_never_deletes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(p, 'run') as run:
            state = Path(directory) / 'state.json'
            state.write_text('not json')
            self.assertIn('Invalid cleanup state', p.cleanup(state, Path(directory))[0])
            self.assertTrue(state.exists())
            run.assert_not_called()

    def test_cleanup_only_owned_distro(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            name = 'bridge-wsl1-' + 'a' * 32
            root = base / name
            root.mkdir()
            state = base / 'state.json'
            state.write_text(json.dumps({'distro': name, 'root': str(root)}))
            with patch.object(p, 'names', side_effect=[{name, 'Ubuntu'}, {'Ubuntu'}]), patch.object(p, 'run') as run:
                self.assertEqual(p.cleanup(state, base), [])
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    ['wsl.exe', '--terminate', name], ['wsl.exe', '--unregister', name]])
            self.assertFalse(root.exists())
            self.assertFalse(state.exists())

    def test_empty_inventory_is_valid(self):
        with patch.object(p, 'run', return_value='[]'):
            self.assertEqual(p.names(), set())


if __name__ == '__main__':
    unittest.main()
