"""Linux PostgreSQL-only probe; never prints or exports temporary authentication."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile

JAR_SHA256 = '1974c3a59c3ae010e9818e81637e425aad94eaac26e4ef533b42e081fe7ca345'
INNER_SHA256 = 'e09b13df10e9df257275d498b19b8c214a3e86d7a5d8cd263b0860e308e8b0b6'


def extract(jar: Path, destination: Path) -> None:
    blob = jar.read_bytes()
    if hashlib.sha256(blob).hexdigest() != JAR_SHA256:
        raise RuntimeError('PostgreSQL JAR digest mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.namelist() != ['META-INF/', 'META-INF/MANIFEST.MF', 'postgres-linux-x86_64.txz']:
            raise RuntimeError('Unexpected PostgreSQL JAR members')
        payload = archive.read('postgres-linux-x86_64.txz')
    if hashlib.sha256(payload).hexdigest() != INNER_SHA256:
        raise RuntimeError('PostgreSQL inner payload digest mismatch')
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:xz') as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 300 * 1024 * 1024:
            raise RuntimeError('PostgreSQL archive exceeds expanded size bound')
        for member in members:
            validate_member(member)
        archive.extractall(destination, filter='data')


def validate_member(member) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir() or member.issym()):
        raise RuntimeError('Unsafe PostgreSQL archive member')
    if member.issym() and (PurePosixPath(member.linkname).is_absolute() or '/' in member.linkname or member.linkname in ('', '.', '..')):
        raise RuntimeError('Unsafe PostgreSQL archive symlink')


def diagnostic_categories(raw: bytes) -> list[str]:
    """Emit fixed categories and a tightly validated library basename, never logs."""
    text = raw[-16384:].decode('utf-8', errors='replace')
    patterns = {
        'syscall_not_implemented': 'Function not implemented',
        'operation_not_permitted': 'Operation not permitted',
        'permission_denied': 'Permission denied',
        'out_of_memory': 'Cannot allocate memory',
        'disk_full': 'No space left on device',
        'shared_memory_create_failed': 'could not create shared memory',
        'shared_memory_resize_failed': 'could not resize shared memory',
        'memory_mapping_failed': 'could not map',
        'semaphore_create_failed': 'could not create semaphores',
        'socket_create_failed': 'could not create socket',
        'address_in_use': 'Address already in use',
        'configuration_invalid': 'invalid value for parameter',
        'configuration_unknown': 'unrecognized configuration parameter',
        'file_open_failed': 'could not open',
        'file_access_failed': 'could not access',
        'locale_invalid': 'invalid locale',
        'database_start_failed': 'could not start server',
        'glibc_version_missing': 'GLIBC_',
    }
    categories = [label for label, needle in patterns.items() if needle.lower() in text.lower()]
    library = re.search(r'error while loading shared libraries: (lib[A-Za-z0-9_+-]{1,60}\.so(?:\.[0-9]+){0,3}):', text)
    if library:
        categories.append('missing_shared_library:' + library.group(1))
    return categories


def command(args, timeout=30):
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if process.returncode:
        # Never include database/credential-bearing raw output in reports.
        categories = diagnostic_categories(process.stdout)
        suffix = ' [' + ','.join(categories) + ']' if categories else ''
        raise RuntimeError(Path(args[0]).name + ' failed with exit ' + str(process.returncode) + suffix)
    return process.stdout


def read_exact(connection, size):
    if size < 0 or size > 1024 * 1024:
        raise RuntimeError('Invalid PostgreSQL protocol length')
    data = b''
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise RuntimeError('PostgreSQL closed the connection')
        data += part
    return data


def message(connection):
    kind = read_exact(connection, 1)
    length = struct.unpack('!I', read_exact(connection, 4))[0]
    return kind, read_exact(connection, length - 4)


def sql_probe(port, password):
    # Cleartext password protocol is restricted to this freshly verified loopback
    # endpoint; the random password is sent only to that local PostgreSQL process,
    # never to Windows, command lines, reports, or artifacts.
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        connection.settimeout(5)
        payload = struct.pack('!I', 196608) + b'user\0bridgepg\0database\0postgres\0\0'
        connection.sendall(struct.pack('!I', len(payload) + 4) + payload)
        authenticated = False
        for _ in range(64):
            kind, body = message(connection)
            if kind == b'R':
                auth = struct.unpack('!I', body[:4])[0]
                if auth == 3 and not authenticated:
                    encoded = password.encode() + b'\0'
                    connection.sendall(b'p' + struct.pack('!I', len(encoded) + 4) + encoded)
                elif auth == 0:
                    authenticated = True
                else:
                    raise RuntimeError('Unexpected PostgreSQL auth method')
            elif kind == b'E':
                raise RuntimeError('PostgreSQL authentication rejected')
            elif kind == b'Z':
                break
        else:
            raise RuntimeError('PostgreSQL startup exceeded message bound')
        if not authenticated:
            raise RuntimeError('PostgreSQL authentication not confirmed')
        query = b'SELECT 42\0'
        connection.sendall(b'Q' + struct.pack('!I', len(query) + 4) + query)
        values = []
        for _ in range(32):
            kind, body = message(connection)
            if kind == b'D':
                if body != struct.pack('!HI', 1, 2) + b'42':
                    raise RuntimeError('Unexpected synthetic SQL result')
                values.append(42)
            elif kind == b'E':
                raise RuntimeError('PostgreSQL SQL rejected')
            elif kind == b'Z':
                break
        else:
            raise RuntimeError('SQL exceeded message bound')
        if values != [42]:
            raise RuntimeError('Synthetic SQL did not return exactly one expected row')
        connection.sendall(b'X' + struct.pack('!I', 4))


def main(jar):
    import pwd
    root = None
    pg_ctl = data = None
    postmaster_pid = None
    result = {'event': 'postgres_complete', 'status': 'blocked', 'engine_tested': False}
    try:
        account = pwd.getpwuid(os.getuid())
        if os.getuid() == 0 or account.pw_name != 'nobody' or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
            raise RuntimeError('Require existing unprivileged non-login nobody account')
        root = Path(tempfile.mkdtemp(prefix='bridge-pg-', dir='/tmp'))
        runtime, data = root / 'runtime', root / 'data'
        extract(Path(jar), runtime)
        pg_ctl = runtime / 'bin/pg_ctl'
        version = command([str(runtime / 'bin/postgres'), '--version']).decode().strip()
        if version != 'postgres (PostgreSQL) 18.3':
            raise RuntimeError('Unexpected PostgreSQL executable version')
        password = secrets.token_hex(32)
        pwfile = root / 'password'
        pwfile.write_text(password + '\n')
        pwfile.chmod(0o600)
        command([str(runtime / 'bin/initdb'), '-D', str(data), '--username=bridgepg',
                 '--auth-host=password', '--auth-local=reject', '--encoding=UTF8', '--locale=C', '--pwfile=' + str(pwfile)])
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        (data / 'postgresql.auto.conf').write_text("listen_addresses = '127.0.0.1'\nunix_socket_directories = ''\nport = %d\n" % port)
        (data / 'pg_hba.conf').write_text('host all bridgepg 127.0.0.1/32 password\n')
        command([str(pg_ctl), 'start', '-D', str(data), '-l', str(root / 'postgres.log'), '-w', '-t', '20'])
        postmaster_pid = int((data / 'postmaster.pid').read_text().splitlines()[0])
        print(json.dumps({'event': 'postgres_ready', 'port': port, 'version': version,
                          'uid': os.getuid(), 'account': account.pw_name}), flush=True)
        # Windows checks its own native listener table before granting SQL start.
        if not select.select([sys.stdin], [], [], 20)[0] or sys.stdin.readline().strip() != 'verified-loopback':
            raise RuntimeError('No native Windows loopback verification')
        sql_probe(port, password)
        result.update(status='passed', synthetic_sql=42, version=version)
    except Exception as exc:
        result['error'] = str(exc)
        if root is not None and (root / 'postgres.log').exists():
            with (root / 'postgres.log').open('rb') as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 16384))
                result['startup_diagnostic_categories'] = diagnostic_categories(log.read(16384))
    finally:
        if data is not None and (data / 'postmaster.pid').exists():
            try:
                command([str(pg_ctl), 'stop', '-D', str(data), '-m', 'fast', '-w', '-t', '20'])
            except Exception as exc:
                result.update(status='blocked', shutdown_error=str(exc))
        result['postmaster_process_gone'] = postmaster_pid is None
        if postmaster_pid is not None:
            try:
                os.kill(postmaster_pid, 0)
            except ProcessLookupError:
                result['postmaster_process_gone'] = True
            except PermissionError:
                pass
        if not result['postmaster_process_gone']:
            result['status'] = 'blocked'
        result['pidfile_present_after_stop'] = bool(data is not None and (data / 'postmaster.pid').exists())
        if result['pidfile_present_after_stop']:
            result['status'] = 'blocked'
        if root is not None and not result['pidfile_present_after_stop'] and result['postmaster_process_gone']:
            shutil.rmtree(root)
        result['temporary_directory_removed'] = root is None or not root.exists()
        print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
