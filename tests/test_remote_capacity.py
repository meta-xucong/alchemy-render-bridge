"""Temporary synthetic data only; no deployed configuration or external calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.runtime import RuntimeConfig
import test_domain


class RemoteCapacityTests(unittest.TestCase):
    setUp = test_domain.DomainTests.setUp
    reopen = test_domain.DomainTests.reopen
    png = staticmethod(test_domain.DomainTests.png)
    metadata = staticmethod(test_domain.DomainTests.metadata)

    def configured(self, quota, profiles=('fake-image',)):
        return BridgeStore(self.root / 'ledger.db', self.root / 'objects',
                           enabled_profiles=profiles, max_storage_bytes=quota)

    def test_legacy_defaults_and_strict_config_validation(self):
        self.assertEqual(self.store.max_storage_bytes, 16 * 1024**2)
        self.assertEqual(self.configured(None, ('codex-image',)).max_storage_bytes, 256 * 1024**2)
        config = RuntimeConfig(self.root, 's', 'w', 'n', 'e')
        for bad in (True, False, 0, -1, 1.5, '1024', 1024**4 + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): self.configured(bad)
                with self.assertRaises(ValueError): replace(config, max_storage_bytes=bad).validate()
        replace(config, max_storage_bytes=1024**4).validate()

    def test_concurrent_quota_and_retry_reopen(self):
        data = self.png()
        self.store = self.configured(len(data))
        ids = [self.store.create_input(self.scope, {'input_key': str(i), **self.metadata(data)})['input_id']
               for i in range(2)]
        def put(input_id):
            try:
                self.store.put_input_content(self.scope, input_id, data)
                return input_id
            except DomainError as error:
                self.assertEqual((error.code, error.status), ('storage_capacity', 503))
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(put, ids))
        winner = next(item for item in results if item is not None)
        self.assertEqual(sum(item is not None for item in results), 1)
        self.store = self.configured(len(data))
        self.store.put_input_content(self.scope, winner, data)
        status = self.store.capabilities(self.scope)['storage']
        self.assertEqual(status, {'used_bytes': len(data), 'max_bytes': len(data),
                                 'available_bytes': 0, 'near_capacity': True, 'at_capacity': True,
                                 'includes_orphan_files': False})
        self.assertEqual((self.store.root / (winner + '.blob')).read_bytes(), data)

    def test_orphan_counted_and_capacity_change_never_deletes(self):
        self.store = self.configured(100)
        orphan = self.store.root / 'uncommitted.blob'
        orphan.write_bytes(b'x' * 80)
        status = self.store.capabilities(self.scope)['storage']
        self.assertFalse(status['near_capacity'])
        self.assertFalse(status['includes_orphan_files'])
        self.assertFalse(status['at_capacity'])
        self.store = self.configured(50)
        self.assertEqual(self.store.capabilities(self.scope)['storage']['used_bytes'], 0)
        self.assertEqual(orphan.read_bytes(), b'x' * 80)
        data = self.png()
        inp = self.store.create_input(self.scope, {'input_key': 'quota', **self.metadata(data)})
        with self.assertRaises(DomainError) as caught:
            self.store.put_input_content(self.scope, inp['input_id'], data)
        self.assertEqual(caught.exception.code, 'storage_capacity')

    def test_runtime_quota_wiring_and_authenticated_diagnostics(self):
        config = RuntimeConfig(self.root, 's', 'w', 'n', 'e', max_storage_bytes=5000)
        app = config.build_app({'BRIDGE_SOURCE_TOKEN': 'source', 'BRIDGE_NODE_TOKEN': 'node',
                                'BRIDGE_DISPATCH_TOKEN': 'dispatch'})
        with TestClient(app) as client:
            self.assertEqual(client.get('/bridge/v1/capabilities').status_code, 401)
            result = client.get('/bridge/v1/capabilities', headers={'Authorization': 'Bearer source'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['storage']['max_bytes'], 5000)
            self.assertFalse(result.json()['storage']['near_capacity'])

    def test_frequent_capabilities_never_walk_large_artifact_tree(self):
        # A directory walk per health poll is a regression even if fast on CI.
        for index in range(2000):
            (self.store.root / f'orphan-{index}.blob').touch()
        with patch.object(Path, 'iterdir', side_effect=AssertionError('unexpected scan')):
            for _ in range(10):
                self.assertFalse(self.store.capabilities(self.scope)['storage']['includes_orphan_files'])

    def test_https_proxy_template_exposes_only_explicit_bridge_routes(self):
        import re
        path = Path(__file__).resolve().parents[1] / 'config/source-https.nginx.example.conf'
        text = path.read_text(encoding='utf-8')
        locations = [value.strip() for value in re.findall(r'location\s+([^\{]+)\{', text)]
        self.assertEqual(locations, ['/bridge/v1/', '/bridge/node/v1/',
            '/bridge/control/v2/', '/bridge/source/v2/', '/bridge/node/v2/',
            '= /bridge/node/v2/reward-claims/confirm',
            '= /bridge/rewards/v1/claim-approval',
            '= /bridge/rewards/v1/nodes',
            '= /bridge/console', '= /bridge/operator', '= /bridge/status/v1', '/'])
        self.assertIn('location / { return 404; }', text)
        self.assertEqual(text.count('proxy_pass '), 11)
        self.assertIn('proxy_pass http://127.0.0.1:8787;', text)
        self.assertEqual(text.count('proxy_next_upstream off;'), 10)
        source = re.search(r'location /bridge/v1/ \{(.*?)\n    \}', text, re.S).group(1)
        node = re.search(r'location /bridge/node/v1/ \{(.*?)\n    \}', text, re.S).group(1)
        control_v2 = re.search(r'location /bridge/control/v2/ \{(.*?)\n    \}', text, re.S).group(1)
        source_v2 = re.search(r'location /bridge/source/v2/ \{(.*?)\n    \}', text, re.S).group(1)
        node_v2 = re.search(r'location /bridge/node/v2/ \{(.*?)\n    \}', text, re.S).group(1)
        rewards = re.search(r'location = /bridge/rewards/v1/nodes \{(.*?)\n    \}', text, re.S).group(1)
        self.assertIn('client_max_body_size 32m;', source)
        self.assertIn('client_max_body_size 16m;', node)
        self.assertIn('client_max_body_size 2m;', control_v2)
        self.assertIn('client_max_body_size 2m;', source_v2)
        self.assertIn('client_max_body_size 32m;', node_v2)
        self.assertIn('limit_except GET { deny all; }', rewards)
        self.assertIn('proxy_set_header Authorization $http_authorization;', rewards)
        self.assertIn('client_max_body_size 2m;', rewards)
        routed_locations = [location for location in locations if location != '/']
        for blocked in ('/bridge/dispatch/v1/pending', '/admin', '/docs',
                        '/bridge/v1evil/tasks', '/bridge/node/v1evil/tasks',
                        '/bridge/rewards/v1/nodes/extra', '/bridge/rewards/v1/other',
                        '/bridge/console/extra', '/bridge/operator/extra',
                        '/bridge/status/v1/extra'):
            self.assertFalse(any(
                blocked == location[2:] if location.startswith('= ')
                else blocked.startswith(location)
                for location in routed_locations))

    def test_existing_local_example_defaults_unchanged(self):
        config = RuntimeConfig.load(Path(__file__).resolve().parents[1] / 'config/bridge.example.json')
        self.assertEqual(config.host, '127.0.0.1')
        self.assertIsNone(config.tls_certfile)
        self.assertIsNone(config.max_storage_bytes)
        self.assertFalse(config.retention_enabled)
        config.validate()

    def test_remote_example_keeps_internal_server_loopback_and_safe_retention(self):
        config = RuntimeConfig.load(Path(__file__).resolve().parents[1] / 'config/bridge.remote.example.json')
        self.assertEqual(config.host, '127.0.0.1')
        self.assertTrue(config.retention_enabled)
        self.assertGreaterEqual(config.retention_seconds, 86400)
        self.assertEqual(config.max_storage_bytes, 4 * 1024**3)
        with self.assertRaises(ValueError): replace(config, host='0.0.0.0').validate()


if __name__ == '__main__':
    unittest.main()
