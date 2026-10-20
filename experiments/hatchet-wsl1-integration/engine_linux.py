"""Private pipe protocol. stdout includes a temporary token; NEVER log it."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from postgres_probe import command, diagnostic_categories, extract

ENGINE_SHA256 = '18ddacae0005042bd982328bcb8d370cca6907352a1ab2a3c8406001d31e7dee'


def emit(event):
    print(json.dumps(event), flush=True)


def instruction(expected, timeout):
    if not select.select([sys.stdin], [], [], timeout)[0] or sys.stdin.readline().strip() != expected:
        raise RuntimeError('Missing or unexpected private controller instruction')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def pid_gone(pid):
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def main(jar, engine_binary):
    root = data = pg_ctl = None
    pg_pid = engine = None
    result = {'event': 'engine_complete', 'status': 'blocked'}
    try:
        user = pwd.getpwuid(os.getuid())
        if user.pw_name != 'nobody' or os.getuid() == 0 or user.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
            raise RuntimeError('Require existing non-login nobody account')
        root = Path(tempfile.mkdtemp(prefix='bridge-engine-', dir='/tmp'))
        home, tmp = root / 'home', root / 'tmp'
        home.mkdir(mode=0o700)
        tmp.mkdir(mode=0o700)
        runtime, data = root / 'runtime', root / 'data'
        extract(Path(jar), runtime)
        pg_ctl = runtime / 'bin/pg_ctl'
        password = secrets.token_hex(32)
        pwfile = root / 'password'
        pwfile.write_text(password + '\n')
        pwfile.chmod(0o600)
        command([str(runtime / 'bin/initdb'), '-D', str(data), '--username=bridgepg',
                 '--auth-host=password', '--auth-local=reject', '--encoding=UTF8', '--locale=C', '--pwfile=' + str(pwfile)])
        pg_port = free_port()
        (data / 'postgresql.auto.conf').write_text("listen_addresses = '127.0.0.1'\nunix_socket_directories = ''\nport = %d\n" % pg_port)
        (data / 'pg_hba.conf').write_text('host all bridgepg 127.0.0.1/32 password\n')
        command([str(pg_ctl), 'start', '-D', str(data), '-l', str(root / 'postgres.log'), '-w', '-t', '20'])
        pg_pid = int((data / 'postmaster.pid').read_text().splitlines()[0])
        emit({'event': 'engine_postgres_ready', 'port': pg_port})
        instruction('postgres-loopback-verified', 20)
        binary = Path(engine_binary).read_bytes()
        if hashlib.sha256(binary).hexdigest() != ENGINE_SHA256:
            raise RuntimeError('Sidecar digest mismatch')
        sidecar = root / 'hatchet-sidecar'
        sidecar.write_bytes(binary)
        sidecar.chmod(0o700)
        passfile = root / 'pgpass'
        passfile.write_text(f'127.0.0.1:{pg_port}:postgres:bridgepg:{password}\n')
        passfile.chmod(0o600)
        password = None
        grpc_port = free_port()
        handshake_path = root / 'handshake.json'
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(tmp),
                       'LANG': 'C.UTF-8', 'PGPASSFILE': str(passfile),
                       'SERVER_PROMETHEUS_ENABLED': 'false', 'SERVER_SECURITY_CHECK_ENABLED': 'false',
                       'SERVER_OTEL_METRICS_ENABLED': 'false', 'SERVER_OTEL_COLLECTOR_URL': '',
                       'SERVER_ANALYTICS_POSTHOG_ENABLED': 'false', 'SERVER_ANALYTICS_AGGREGATE_ENABLED': 'false',
                       'DO_NOT_TRACK': '1'}
        # URL has NO password. Exact pgx v5.11.0 reads PGPASSFILE.
        database_url = f'postgresql://bridgepg@127.0.0.1:{pg_port}/postgres?sslmode=disable'
        with (root / 'engine.log').open('wb') as log:
            engine = subprocess.Popen([str(sidecar), '--database-url', database_url,
                                       '--grpc-port', str(grpc_port), '--no-api', '--log-level', 'warn',
                                       '--handshake-file', str(handshake_path)],
                                      cwd=root, env=environment, stdin=subprocess.PIPE,
                                      stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            while not handshake_path.exists():
                if engine.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Sidecar did not become ready')
                time.sleep(0.2)
            handshake = json.loads(handshake_path.read_text())
            if handshake.get('grpc_address') != f'127.0.0.1:{grpc_port}' or not isinstance(handshake.get('token'), str):
                raise RuntimeError('Unexpected private sidecar handshake')
            # Only the parent private pipe receives this token. Do not add logs.
            emit({'event': 'engine_ready', 'grpc_port': grpc_port, 'pg_port': pg_port,
                  'token': handshake['token'], 'tenant_id': handshake['tenant_id']})
            handshake = None
            instruction('worker-completed-stop-engine', 210)
            engine.stdin.close()  # Official sidecar EOF-triggered Shutdown(ctx).
            if engine.wait(timeout=40) != 0:
                raise RuntimeError('Sidecar graceful shutdown failed')
            result.update(status='passed', engine_orderly_stop=True, engine_exit_code=0)
    except Exception as exc:
        # Do not serialize arbitrary sidecar output, env, handshake or credentials.
        result['error_type'] = type(exc).__name__
        result['error'] = str(exc) if isinstance(exc, RuntimeError) else 'Controller operation failed'
        if root is not None:
            for name in ('engine.log', 'postgres.log'):
                path = root / name
                if path.exists():
                    with path.open('rb') as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell() - 16384))
                        result[name.replace('.', '_') + '_categories'] = diagnostic_categories(stream.read(16384))
    finally:
        if engine is not None and engine.poll() is None:
            result['status'] = 'blocked'
            try:
                if engine.stdin and not engine.stdin.closed:
                    engine.stdin.close()
                engine.wait(timeout=40)
            except Exception:
                engine.kill()
                engine.wait(timeout=5)
                result['forced_engine_cleanup'] = True
        result['engine_process_gone'] = engine is None or engine.poll() is not None
        if data is not None and (data / 'postmaster.pid').exists():
            try:
                command([str(pg_ctl), 'stop', '-D', str(data), '-m', 'fast', '-w', '-t', '20'])
            except Exception:
                result.update(status='blocked', postgres_shutdown_failed=True)
        result['postgres_process_gone'] = pid_gone(pg_pid)
        result['postgres_pidfile_absent'] = data is None or not (data / 'postmaster.pid').exists()
        if not result['postgres_process_gone'] or not result['postgres_pidfile_absent']:
            result['status'] = 'blocked'
        if root is not None and result['engine_process_gone'] and result['postgres_process_gone'] and result['postgres_pidfile_absent']:
            shutil.rmtree(root)
        result['temporary_directory_removed'] = root is None or not root.exists()
        emit(result)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
