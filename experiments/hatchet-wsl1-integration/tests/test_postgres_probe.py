import importlib.util
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location('postgres_probe', Path(__file__).parents[1] / 'postgres_probe.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class ArchiveTests(unittest.TestCase):
    def test_bad_jar_digest_blocks_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.jar'
            path.write_bytes(b'not the pinned artifact')
            with self.assertRaisesRegex(RuntimeError, 'JAR digest mismatch'):
                p.extract(path, Path(directory) / 'out')
            self.assertFalse((Path(directory) / 'out').exists())

    def test_archive_paths_and_types_fail_closed(self):
        for name, kind, link in [('/etc/file', tarfile.REGTYPE, ''), ('../escape', tarfile.REGTYPE, ''),
                                 ('dev/x', tarfile.CHRTYPE, ''), ('lib/link', tarfile.SYMTYPE, '../escape'),
                                 ('lib/link', tarfile.SYMTYPE, '/etc/file'), ('lib/hard', tarfile.LNKTYPE, 'x')]:
            with self.subTest(name=name, kind=kind):
                item = tarfile.TarInfo(name)
                item.type, item.linkname = kind, link
                with self.assertRaises(RuntimeError):
                    p.validate_member(item)

    def test_bundled_library_relative_symlink_is_allowed(self):
        item = tarfile.TarInfo('lib/libpq.so')
        item.type, item.linkname = tarfile.SYMTYPE, 'libpq.so.5.18'
        p.validate_member(item)


class ProtocolTests(unittest.TestCase):
    def test_select42_protocol_success(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        rows = [(b'R', struct.pack('!I', 3)), (b'R', struct.pack('!I', 0)), (b'Z', b'I'),
                (b'D', struct.pack('!HI', 1, 2) + b'42'), (b'Z', b'I')]
        with patch.object(p.socket, 'create_connection', return_value=connection) as connect, patch.object(p, 'message', side_effect=rows):
            p.sql_probe(43210, 'synthetic-test-password')
            connect.assert_called_once_with(('127.0.0.1', 43210), timeout=5)
        sent = [call.args[0] for call in connection.sendall.call_args_list]
        self.assertTrue(any(b'SELECT 42\0' in value for value in sent))
        self.assertEqual(sent[-1], b'X' + struct.pack('!I', 4))

    def test_auth_error_never_includes_server_payload(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(p.socket, 'create_connection', return_value=connection), patch.object(p, 'message', return_value=(b'E', b'secret details')):
            with self.assertRaisesRegex(RuntimeError, '^PostgreSQL authentication rejected$'):
                p.sql_probe(43210, 'do-not-log')

    def test_protocol_lengths_are_bounded(self):
        for size in (-1, 1024 * 1024 + 1):
            with self.assertRaises(RuntimeError):
                p.read_exact(MagicMock(), size)

    def test_read_exact_handles_fragmented_tcp(self):
        connection = MagicMock()
        connection.recv.side_effect = [b'4', b'2']
        self.assertEqual(p.read_exact(connection, 2), b'42')

    def test_diagnostics_emit_categories_not_raw_secrets(self):
        raw = (b'password=do-not-log Function not implemented; Permission denied; '
               b'error while loading shared libraries: libexample.so.1: missing')
        result = p.diagnostic_categories(raw)
        self.assertEqual(result, ['syscall_not_implemented', 'permission_denied',
                                  'missing_shared_library:libexample.so.1'])
        self.assertNotIn('do-not-log', repr(result))

    def test_command_failure_omits_raw_output(self):
        result = MagicMock(returncode=1, stdout=b'password=do-not-log')
        with patch.object(p.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, '^initdb failed with exit 1$'):
                p.command(['/bin/initdb'])


class HostGateTests(unittest.TestCase):
    def exercise(self, unsafe=False, cleanup_failed=False):
        import contextlib
        import json
        import sys
        spec = importlib.util.spec_from_file_location('host_preflight', Path(__file__).parents[1] / 'preflight.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        url = ('https://repo.maven.apache.org/maven2/io/zonky/test/postgres/'
               'embedded-postgres-binaries-linux-amd64/18.3.0/'
               'embedded-postgres-binaries-linux-amd64-18.3.0.jar')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            response = MagicMock()
            response.__enter__.return_value = response
            response.url = url
            response.read.side_effect = [b'test bytes', b'']
            stack.enter_context(patch.dict(sys.modules, {'postgres_probe': p}))
            stack.enter_context(patch.object(host.urllib.request, 'urlopen', return_value=response))
            digest = MagicMock()
            digest.hexdigest.return_value = p.JAR_SHA256
            stack.enter_context(patch.object(host.hashlib, 'file_digest', return_value=digest))
            stack.enter_context(patch.object(host, 'run', return_value='/tmp/test-path'))
            process = MagicMock()
            process.poll.return_value = 0
            process.wait.return_value = 0
            def popen(args, stdout, **kwargs):
                records = [{'event': 'postgres_ready', 'port': 43210},
                           {'event': 'postgres_complete', 'status': 'passed',
                            'pidfile_present_after_stop': cleanup_failed,
                            'postmaster_process_gone': not cleanup_failed,
                            'temporary_directory_removed': not cleanup_failed}]
                stdout.write(('\n'.join(json.dumps(row) for row in records) + '\n').encode())
                stdout.flush()
                return process
            stack.enter_context(patch.object(host.subprocess, 'Popen', side_effect=popen))
            rows = [{'LocalAddress': '0.0.0.0' if unsafe else '127.0.0.1', 'LocalPort': 43210, 'State': 'Listen'}]
            stack.enter_context(patch.object(host, 'windows_listener', side_effect=[rows, []]))
            if unsafe or cleanup_failed:
                with self.assertRaises(RuntimeError):
                    host.postgres_gate('bridge-test', Path(directory))
            else:
                self.assertEqual(host.postgres_gate('bridge-test', Path(directory))['status'], 'passed')
            if unsafe:
                process.stdin.write.assert_not_called()
            else:
                process.stdin.write.assert_called_once_with(b'verified-loopback\n')

    def test_unsafe_listener_never_grants_sql(self):
        self.exercise(unsafe=True)

    def test_cleanup_failure_cannot_pass(self):
        self.exercise(cleanup_failed=True)

    def test_verified_loopback_and_complete_cleanup_pass(self):
        self.exercise()


if __name__ == '__main__':
    unittest.main()
