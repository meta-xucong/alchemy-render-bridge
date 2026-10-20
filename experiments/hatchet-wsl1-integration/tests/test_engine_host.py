import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location('engine_host', Path(__file__).parents[1] / 'engine_host.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def row(address, port):
    return {'LocalAddress': address, 'LocalPort': port, 'State': 'Listen'}


class ListenerDeltaTests(unittest.TestCase):
    def test_exact_two_owned_loopback_ports_pass(self):
        baseline = [row('0.0.0.0', 135)]
        after = baseline + [row('127.0.0.1', 43210), row('127.0.0.1', 43211)]
        self.assertEqual(len(p.validate_delta(baseline, after, [43210, 43211])), 2)

    def test_extra_missing_wildcard_duplicate_and_ipv6_block(self):
        valid = [row('127.0.0.1', 43210)]
        for after in ([], [row('0.0.0.0', 43210)], [row('::1', 43210)], valid * 2,
                      valid + [row('127.0.0.1', 9090)], valid + [row('0.0.0.0', 9090)]):
            with self.subTest(after=after), self.assertRaises(RuntimeError):
                p.validate_delta([], after, [43210])

    def test_unexpected_listener_after_shutdown_blocks(self):
        with self.assertRaises(RuntimeError):
            p.validate_delta([], [row('127.0.0.1', 9090)], [])

    def test_malformed_inventory_blocks(self):
        for value in (None, {}, [{}], [row('127.0.0.1', True)]):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                p.endpoints(value)

    def test_preexisting_endpoint_cannot_count_as_new_owned_listener(self):
        with self.assertRaises(RuntimeError):
            p.validate_delta([row('127.0.0.1', 43210)], [row('127.0.0.1', 43210)], [43210])


class SecretBoundaryTests(unittest.TestCase):
    def test_private_pipe_transports_only_in_memory(self):
        secret = b'{"event":"engine_ready","token":"do-not-log"}\n'
        value = p.PrivatePipe(io.BytesIO(secret)).event('engine_ready', 1)
        self.assertEqual(value['token'], 'do-not-log')

    def test_private_protocol_errors_never_echo_payload(self):
        for blob in (b'not json do-not-log\n', b'{"event":"wrong","token":"do-not-log"}\n', b'x' * 65537):
            with self.subTest(size=len(blob)):
                with self.assertRaises(RuntimeError) as error:
                    p.PrivatePipe(io.BytesIO(blob)).event('engine_ready', 1)
                self.assertNotIn('do-not-log', str(error.exception))

    def test_completion_allowlist_drops_credentials_and_raw_error(self):
        result = p.safe_engine_completion({'status': 'blocked', 'token': 'secret',
                                          'password': 'secret', 'error': 'raw secret', 'engine_process_gone': True})
        self.assertEqual(result, {'status': 'blocked', 'engine_process_gone': True})

    def test_download_digest_mismatch_blocks(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = p.ENGINE_URL
        response.read.side_effect = [b'incorrect binary', b'']
        with tempfile.TemporaryDirectory() as directory, patch.object(p.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
                p.download_engine(Path(directory) / 'binary')

    def test_download_unapproved_redirect_blocks(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = 'https://unapproved.example/binary'
        with tempfile.TemporaryDirectory() as directory, patch.object(p.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'download destination'):
                p.download_engine(Path(directory) / 'binary')

    def test_native_lifecycle_uses_public_api_not_signals_or_monkeypatch(self):
        source = (Path(__file__).parents[1] / 'windows_worker.py').read_text()
        lifecycle = (Path(__file__).parents[1] / 'worker_lifecycle.py').read_text()
        self.assertIn('worker.exit_gracefully()', lifecycle)
        self.assertIn('orderly_stop(worker, result)', source)
        self.assertIn('lifespan=lifespan', source)
        for forbidden in ('os.kill(', 'SIGTERM', 'SIGINT', 'unittest.mock', 'setattr(', '._loop', '._exit_forcefully'):
            self.assertNotIn(forbidden, source)

    def test_engine_db_url_contains_no_password_interpolation(self):
        source = (Path(__file__).parents[1] / 'engine_linux.py').read_text()
        self.assertIn("'PGPASSFILE': str(passfile)", source)
        self.assertIn("postgresql://bridgepg@127.0.0.1:{pg_port}/postgres?sslmode=disable", source)
        self.assertIn("'--no-api'", source)
        self.assertIn("'SERVER_PROMETHEUS_ENABLED': 'false'", source)
        self.assertNotIn('postgresql://bridgepg:{password}', source)


class CompletionTests(unittest.TestCase):
    def worker(self):
        return {'status': 'passed', 'platform': 'win32', 'sdk_version': '1.42.1+bridgewin1',
                'task_count': 3, 'graceful_stop_requested': True, 'graceful_stop_completed': True,
                'worker_start_zero_exit': True, 'active_children_after_stop': [],
                'observed_listener_exits': [{'pid': 124, 'exitcode': 0}],
                'tasks': [{'marker': f'bridge-native-{i}', 'renderer': 'fake', 'platform': 'win32', 'pid': 123} for i in range(3)]}

    def test_missing_graceful_evidence_or_crashed_child_blocks(self):
        p.validate_worker_completion(self.worker(), 123)
        for changes in ({'graceful_stop_completed': False}, {'observed_listener_exits': []},
                        {'observed_listener_exits': [{'pid': 124, 'exitcode': 1}]},
                        {'task_count': 2}, {'active_children_after_stop': [124]}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                p.validate_worker_completion({**self.worker(), **changes}, 123)

    def test_forced_engine_cleanup_never_passes(self):
        value = {'status': 'passed', 'engine_exit_code': 0, 'engine_orderly_stop': True,
                 'engine_process_gone': True, 'postgres_process_gone': True,
                 'postgres_pidfile_absent': True, 'temporary_directory_removed': True}
        p.validate_engine_completion(value)
        with self.assertRaises(RuntimeError):
            p.validate_engine_completion({**value, 'forced_engine_cleanup': True})

    def test_arbitrary_exception_text_is_omitted(self):
        self.assertEqual(p.public_error(RuntimeError('token=do-not-log')), {'error_type': 'RuntimeError'})

    def test_early_private_failure_preserves_only_safe_evidence(self):
        import json
        payload = {'event': 'engine_complete', 'status': 'blocked', 'token': 'do-not-log',
                   'engine_log_categories': ['syscall_not_implemented']}
        with self.assertRaises(p.ProtocolFailure) as error:
            p.PrivatePipe(io.BytesIO((json.dumps(payload) + '\n').encode())).event('engine_ready', 1)
        public = p.public_error(error.exception)
        self.assertNotIn('do-not-log', repr(public))
        self.assertEqual(public['private_failure_evidence']['engine_log_categories'], ['syscall_not_implemented'])

    def test_same_task_shutdown_records_after_loop_stop(self):
        import asyncio
        spec = importlib.util.spec_from_file_location('worker_lifecycle', Path(__file__).parents[1] / 'worker_lifecycle.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loop = asyncio.new_event_loop()
        class Worker:
            async def exit_gracefully(self):
                loop.stop()
        evidence = {}
        task = loop.create_task(module.orderly_stop(Worker(), evidence))
        try:
            loop.run_forever()
            self.assertTrue(task.done())
            self.assertIsNone(task.exception())
            self.assertTrue(evidence['graceful_stop_completed'])
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
