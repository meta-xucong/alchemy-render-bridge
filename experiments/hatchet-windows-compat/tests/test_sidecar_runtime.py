"""Import the configured sidecar in the SDK-only environment, never a service."""
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]


class SidecarRuntimeTests(unittest.TestCase):
    def test_sdk_environment_has_no_bridge_server_dependency_conflict(self):
        self.assertEqual(importlib.metadata.version('pydantic'), '2.14.1')
        self.assertEqual(importlib.metadata.version('httpx'), '0.28.1')
        self.assertEqual(importlib.metadata.version('Pillow'), '12.3.0')
        self.assertIsNone(importlib.util.find_spec('fastapi'))
        with self.assertRaises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version('alchemy-render-bridge')
        result = subprocess.run([sys.executable, '-m', 'pip', 'check'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sdk_types_import_without_a_client(self):
        from hatchet_sdk import Context, Hatchet
        self.assertIsNotNone(Context)
        self.assertIsNotNone(Hatchet)

    def test_source_sidecar_help_loads_without_credentials_or_network(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('HATCHET_', 'BRIDGE_'))}
        env['PYTHONPATH'] = str(ROOT / 'src')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        for args in (['--help'], ['worker', '--help'], ['dispatcher', '--help']):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, '-m', 'render_bridge.agent.hatchet_sidecar', *args],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout)
