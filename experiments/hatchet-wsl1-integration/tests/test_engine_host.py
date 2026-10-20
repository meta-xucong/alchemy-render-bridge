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
                'task_count': 5, 'fake_render_starts': 1, 'node_journal_removed': True,
                'frozen_prompt_sha256': 'cfdc2217a30328caae330b9120feb5a53791022bff5e343ba5e9e774d5ff2351',
                'reference_count': 0, 'graceful_stop_requested': True, 'graceful_stop_completed': True,
                'worker_start_zero_exit': True, 'active_children_after_stop': [],
                'observed_listener_exits': [{'pid': 124, 'exitcode': 0}],
                'tasks': [{'marker': marker, 'renderer': 'fake', 'platform': 'win32', 'pid': 123, 'outcome': outcome}
                          for marker, outcome in [('happy', 'artifact_committed'), ('happy', 'duplicate_suppressed'),
                                                  ('unknown', 'execution_unknown'), ('unknown', 'duplicate_suppressed'),
                                                  ('blocked', 'physical_slot_busy')]]}

    def test_missing_graceful_evidence_or_crashed_child_blocks(self):
        p.validate_worker_completion(self.worker(), 123)
        for changes in ({'graceful_stop_completed': False}, {'observed_listener_exits': []},
                        {'observed_listener_exits': [{'pid': 124, 'exitcode': 1}]},
                        {'task_count': 2}, {'fake_render_starts': 2}, {'node_journal_removed': False},
                        {'frozen_prompt_sha256': 'wrong'}, {'reference_count': 1}, {'active_children_after_stop': [124]}):
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


class NodeAdapterBoundaryTests(unittest.TestCase):
    def evidence(self):
        return {'status': 'passed', 'start_grants': {'happy': 1, 'unknown': 1, 'blocked': 0},
                'artifact_sha256': 'a' * 64, 'artifact_bytes': 100, 'receipt_ack': 'accepted',
                'unknown_retains_slot': True, 'source_transport': 'domain_simulator',
                'node_transport': 'test_only_loopback_http'}

    def test_exact_three_loopback_listener_delta(self):
        ports = [43210, 43211, 43212]
        self.assertEqual(len(p.validate_delta([], [row('127.0.0.1', port) for port in ports], ports)), 3)

    def test_node_config_is_fixed_loopback_port_and_opaque_token(self):
        self.assertEqual(p.validate_node_config({'port': 43212, 'token': 'a' * 43}), {'port': 43212, 'token': 'a' * 43})
        for config in ({'port': True, 'token': 'a' * 43}, {'port': 0, 'token': 'a' * 43},
                       {'port': 43212, 'token': 'secret invalid!'}, {'port': 43212, 'token': 'a' * 44}):
            with self.assertRaises(RuntimeError):
                p.validate_node_config(config)

    def test_node_evidence_never_copies_private_token(self):
        self.assertEqual(p.validate_node_evidence({**self.evidence(), 'token': 'do-not-log'}), self.evidence())

    def test_node_ledger_claims_are_fail_closed(self):
        for change in ({'start_grants': {'happy': 2, 'unknown': 1, 'blocked': 0}},
                       {'unknown_retains_slot': False}, {'receipt_ack': 'pending'},
                       {'artifact_sha256': 'secret'}, {'artifact_bytes': True},
                       {'source_transport': 'real_http'}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                p.validate_node_evidence({**self.evidence(), **change})

    def test_temp_root_cleanup_even_when_popen_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'node-runtime'
            root.mkdir()
            (root / 'fixture').write_text('synthetic')
            self.assertTrue(p.cleanup_owned_runtime(None, root))
            self.assertFalse(root.exists())

    def test_temp_root_not_deleted_under_live_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'node-runtime'
            root.mkdir()
            node = MagicMock()
            node.poll.return_value = None
            with self.assertRaises(RuntimeError):
                p.cleanup_owned_runtime(node, root)
            self.assertTrue(root.exists())

    def test_node_cleanup_cannot_be_skipped_or_failed(self):
        p.validate_node_stop({'status': 'passed', 'temporary_directory_removed': True})
        for value in ({}, {'status': 'passed', 'temporary_directory_removed': False},
                      {'status': 'blocked', 'temporary_directory_removed': True}):
            with self.assertRaises(RuntimeError):
                p.validate_node_stop(value)


class WorkflowWiringTests(unittest.TestCase):
    def test_bridge_interpreter_is_derived_on_runner_not_in_job_env(self):
        workflow = (Path(__file__).parents[3] / '.github/workflows/hatchet-wsl1.yml').read_text()
        # GitHub context availability: jobs.<job_id>.env cannot use runner.
        job_env = workflow.split('    env:\n', 1)[1].split('    defaults:', 1)[0]
        self.assertNotIn('${{', job_env)
        self.assertNotIn('BRIDGE_TRIAL_PYTHON', job_env)
        create = workflow.split('- name: Create isolated Bridge interpreter', 1)[1].split('      - name:', 1)[0]
        cleanup = workflow.split('- name: Remove owned Bridge interpreter', 1)[1].split('      - name:', 1)[0]
        derivation = "$venv = Join-Path $env:RUNNER_TEMP 'bridge-node-venv-${{ github.run_id }}-${{ github.run_attempt }}'"
        self.assertIn(derivation, create)
        self.assertIn(derivation, cleanup)
        self.assertIn('python -m venv "$venv"', create)
        self.assertIn('if ($LASTEXITCODE -ne 0)', create)
        self.assertIn('"BRIDGE_TRIAL_PYTHON=$interpreter" | Out-File -FilePath "$env:GITHUB_ENV" -Encoding utf8 -Append', create)
        self.assertIn('if: always()', cleanup)
        self.assertNotIn('$env:BRIDGE_TRIAL_PYTHON', cleanup)


if __name__ == '__main__':
    unittest.main()
