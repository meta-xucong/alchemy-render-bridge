"""Configured server + separate Agent HTTP happy path; no external engine/provider."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


class ConfiguredProcessTests(unittest.TestCase):
    def test_configured_http_processes(self):
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'run_configured_demo.py'
        spec = importlib.util.spec_from_file_location('configured_demo', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'result'
            result = module.run_demo(output)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['fake_render_starts'], 1)
            self.assertTrue(result['manual_import_after_restart'])
            self.assertTrue(result['manual_wait_does_not_block_automatic'])
            self.assertEqual(result['manual_receipt_ack'], 'accepted')
            self.assertTrue(result['temporary_runtime_removed'])
            self.assertTrue(result['server_process_stopped'])
            self.assertTrue((output / 'synthetic.png').is_file())
