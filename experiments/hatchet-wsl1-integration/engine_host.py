"""Windows controller: private credential pipes, public allowlisted evidence only."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

ENGINE_SHA256 = '18ddacae0005042bd982328bcb8d370cca6907352a1ab2a3c8406001d31e7dee'
ENGINE_URL = 'https://github.com/hatchet-dev/hatchet-embedded/releases/download/v0.110.5/hatchet-embedded-sidecar_linux_amd64'
CANDIDATE_SHA256 = 'f8fbe9ed1944a1c1718b1655b17dddd55ba5e79920d99beabf68c98ad9d8af92'


class PublicFailure(RuntimeError):
    """Only fixed messages constructed by this harness may reach public evidence."""


class ProtocolFailure(PublicFailure):
    def __init__(self, value):
        super().__init__('Private process reported failure before expected event')
        self.evidence = safe_engine_completion(value)


def public_error(exc):
    result = {'error_type': type(exc).__name__}
    if isinstance(exc, PublicFailure):
        result['error'] = str(exc)
    if isinstance(exc, ProtocolFailure):
        result['private_failure_evidence'] = exc.evidence
    return result


class PrivatePipe:
    def __init__(self, stream):
        self.messages = queue.Queue(maxsize=8)
        def reader():
            try:
                while True:
                    line = stream.readline(65537)
                    if not line or len(line) > 65536:
                        self.messages.put(None)
                        return
                    self.messages.put(line)
            except Exception:
                self.messages.put(None)
        self.thread = threading.Thread(target=reader, daemon=True)
        self.thread.start()

    def event(self, expected, timeout):
        try:
            raw = self.messages.get(timeout=timeout)
            value = json.loads(raw) if raw else None
        except Exception:
            raise PublicFailure('Private protocol unavailable or invalid') from None
        if isinstance(value, dict) and value.get('event') == 'engine_complete' and expected != 'engine_complete':
            raise ProtocolFailure(value)
        if not isinstance(value, dict) or value.get('event') != expected:
            raise PublicFailure('Unexpected private protocol event')
        return value


def listener_inventory(run):
    script = ("$ErrorActionPreference = 'Stop'; "
              "$a = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | "
              "ForEach-Object { [pscustomobject]@{ LocalAddress = [string]$_.LocalAddress; "
              "LocalPort = [int]$_.LocalPort; State = [string]$_.State } }); "
              "ConvertTo-Json -InputObject @($a) -Compress")
    return json.loads(run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], timeout=15))


def endpoints(rows):
    if not isinstance(rows, list):
        raise PublicFailure('Invalid native listener inventory')
    result = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('LocalAddress'), str)
                or type(row.get('LocalPort')) is not int or not 1 <= row['LocalPort'] <= 65535
                or row.get('State') != 'Listen'):
            raise PublicFailure('Invalid native listener inventory row')
        endpoint = row['LocalAddress'], row['LocalPort']
        if endpoint in result:
            raise PublicFailure('Duplicate native listener inventory row')
        result.add(endpoint)
    return result


def validate_delta(before, after, expected_ports):
    baseline, current = endpoints(before), endpoints(after)
    expected = {('127.0.0.1', port) for port in expected_ports}
    if current - baseline != expected or not expected.issubset(current):
        raise PublicFailure('Unexpected or missing native listener delta')
    return [{'LocalAddress': address, 'LocalPort': port} for address, port in sorted(expected)]


def send(process, value):
    process.stdin.write(value.encode('utf-8') + b'\n')
    process.stdin.flush()


def download_engine(destination):
    deadline = time.monotonic() + 120
    with urllib.request.urlopen(ENGINE_URL, timeout=20) as response, destination.open('wb') as output:
        final = urllib.parse.urlparse(response.url)
        if final.scheme != 'https' or final.hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise PublicFailure('Unexpected engine download destination')
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 150 * 1024 * 1024 or time.monotonic() > deadline:
                raise PublicFailure('Engine download exceeds bound')
            output.write(chunk)
    with destination.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ENGINE_SHA256:
            raise PublicFailure('Engine digest mismatch; execution prohibited')


def safe_engine_completion(value):
    result = {}
    if value.get('status') in ('passed', 'blocked'):
        result['status'] = value['status']
    for key in ('engine_orderly_stop', 'engine_process_gone', 'postgres_process_gone',
                'postgres_pidfile_absent', 'temporary_directory_removed', 'forced_engine_cleanup',
                'postgres_shutdown_failed'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if type(value.get('engine_exit_code')) is int:
        result['engine_exit_code'] = value['engine_exit_code']
    if value.get('error_type') in ('RuntimeError', 'TimeoutExpired', 'PermissionError', 'FileNotFoundError',
                                  'OSError', 'ValueError', 'JSONDecodeError', 'BrokenPipeError'):
        result['error_type'] = value['error_type']
    categories = {'syscall_not_implemented', 'operation_not_permitted', 'permission_denied',
                  'out_of_memory', 'disk_full', 'shared_memory_create_failed', 'shared_memory_resize_failed',
                  'memory_mapping_failed', 'semaphore_create_failed', 'socket_create_failed',
                  'address_in_use', 'configuration_invalid', 'configuration_unknown', 'file_open_failed',
                  'file_access_failed', 'locale_invalid', 'database_start_failed', 'glibc_version_missing'}
    for key in ('engine_log_categories', 'postgres_log_categories'):
        if isinstance(value.get(key), list):
            result[key] = [item for item in value[key][:32] if isinstance(item, str) and
                           (item in categories or re.fullmatch(r'missing_shared_library:lib[A-Za-z0-9_+-]{1,60}\.so(?:\.[0-9]+){0,3}', item))]
    return result


def validate_worker_completion(value, pid):
    tasks = value.get('tasks')
    if (value.get('status') != 'passed' or value.get('platform') != 'win32'
            or value.get('sdk_version') != '1.42.1+bridgewin1'
            or value.get('task_count') != 3 or not isinstance(tasks, list) or len(tasks) != 3
            or value.get('graceful_stop_requested') is not True
            or value.get('graceful_stop_completed') is not True
            or not (value.get('worker_start_returned') is True or value.get('worker_start_zero_exit') is True)
            or value.get('active_children_after_stop') != []
            or not isinstance(value.get('observed_listener_exits'), list) or not value['observed_listener_exits']
            or any(not isinstance(child, dict) or type(child.get('pid')) is not int or child.get('exitcode') != 0
                   for child in value['observed_listener_exits'])
            or any(value.get(key) for key in ('task_error_type', 'shutdown_error_type', 'error_type'))):
        raise PublicFailure('Native worker task or orderly shutdown gate failed')
    for number, task in enumerate(tasks):
        if task != {'marker': f'bridge-native-{number}', 'renderer': 'fake', 'platform': 'win32', 'pid': pid}:
            raise PublicFailure('Native worker task result identity mismatch')


def validate_engine_completion(value):
    if (value.get('status') != 'passed' or type(value.get('engine_exit_code')) is not int or value.get('engine_exit_code') != 0
            or value.get('forced_engine_cleanup') or value.get('postgres_shutdown_failed')
            or any(value.get(key) is not True for key in ('engine_orderly_stop', 'engine_process_gone',
                       'postgres_process_gone', 'postgres_pidfile_absent', 'temporary_directory_removed'))):
        raise PublicFailure('Engine orderly stop or cleanup evidence incomplete')


def engine_gate(name, root, run, windows_listener, validate_listener):
    result = {'status': 'blocked', 'stage': 'verify_candidate', 'engine_sha256': ENGINE_SHA256,
              'candidate_wheel_sha256': CANDIDATE_SHA256}
    linux = worker = None
    try:
        candidate = Path(__file__).parent.parent / 'hatchet-windows-compat/dist/hatchet_sdk-1.42.1+bridgewin1-py3-none-any.whl'
        with candidate.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != CANDIDATE_SHA256:
                raise PublicFailure('Candidate wheel digest mismatch')
        binary = root / 'hatchet-sidecar'
        result['stage'] = 'download_engine'
        download_engine(binary)
        def linux_path(path):
            return run(['wsl.exe', '-d', name, '-u', 'nobody', '--exec', '/usr/bin/wslpath', '-u', str(path.resolve())])
        script = linux_path(Path(__file__).with_name('engine_linux.py'))
        jar = linux_path(root / 'postgres.jar')
        executable = linux_path(binary)
        baseline = listener_inventory(run)
        result['baseline_listener_count'] = len(endpoints(baseline))
        result['stage'] = 'engine_postgres_start'
        linux = subprocess.Popen(['wsl.exe', '-d', name, '-u', 'nobody', '--exec',
                                  '/usr/bin/python3', '-u', script, jar, executable],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        private = PrivatePipe(linux.stdout)
        pg = private.event('engine_postgres_ready', 70)
        pg_port = pg['port']
        validate_listener(windows_listener(pg_port), pg_port)
        validate_delta(baseline, listener_inventory(run), [pg_port])
        send(linux, 'postgres-loopback-verified')
        result['stage'] = 'engine_start'
        handshake = private.event('engine_ready', 130)
        grpc_port = handshake['grpc_port']
        if handshake.get('pg_port') != pg_port or not isinstance(handshake.get('token'), str) or not handshake['token'].startswith('ey'):
            raise PublicFailure('Invalid private engine handshake')
        result['grpc_listener'] = validate_listener(windows_listener(grpc_port), grpc_port)
        result['engine_listener_delta'] = validate_delta(baseline, listener_inventory(run), [pg_port, grpc_port])
        result['stage'] = 'native_windows_worker'
        worker = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('windows_worker.py'))],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'OTEL_SDK_DISABLED': 'true'})
        control = PrivatePipe(worker.stdout)
        send(worker, json.dumps({'grpc_port': grpc_port, 'tenant_id': handshake['tenant_id'], 'token': handshake['token']}))
        handshake = None
        first = control.event('worker_registered_task_completed', 75)
        if first.get('native_pid') != worker.pid:
            raise PublicFailure('Worker evidence did not come from native worker process')
        result['worker_listener_delta'] = validate_delta(baseline, listener_inventory(run), [pg_port, grpc_port])
        send(worker, 'worker-listeners-verified')
        completed = control.event('worker_complete', 150)
        if worker.wait(timeout=10) != 0:
            raise PublicFailure('Native worker did not exit successfully')
        validate_worker_completion(completed, worker.pid)
        result['worker'] = {key: completed[key] for key in (
            'status', 'platform', 'tasks', 'task_count', 'sdk_version', 'graceful_stop_requested',
            'graceful_stop_completed', 'worker_start_returned', 'worker_start_zero_exit',
            'active_children_after_stop', 'observed_listener_exits', 'task_error_type', 'shutdown_error_type', 'error_type') if key in completed}
        result['stage'] = 'engine_orderly_shutdown'
        send(linux, 'worker-completed-stop-engine')
        completion = private.event('engine_complete', 80)
        if linux.wait(timeout=10) != 0:
            raise PublicFailure('Linux controller did not exit successfully')
        result['engine_completion'] = safe_engine_completion(completion)
        validate_engine_completion(completion)
        if windows_listener(pg_port) != [] or windows_listener(grpc_port) != []:
            raise PublicFailure('Owned engine/database listener survived shutdown')
        validate_delta(baseline, listener_inventory(run), [])
        result.update(status='passed', stage='real_engine_smoke_complete', owned_listeners_after_stop=[],
                      post_shutdown_listener_delta=[])
    except Exception as exc:
        result.update(public_error(exc))
    finally:
        cleanup_errors = []
        if worker is not None:
            try:
                if worker.poll() is None:
                    result.update(status='blocked', forced_worker_cleanup=True)
                    run(['taskkill.exe', '/PID', str(worker.pid), '/T', '/F'], timeout=15, check=False)
                    worker.wait(timeout=5)
                if worker.stdin and not worker.stdin.closed:
                    worker.stdin.close()
            except Exception as exc:
                cleanup_errors.append('native_worker:' + type(exc).__name__)
        if linux is not None:
            try:
                if linux.stdin and not linux.stdin.closed:
                    linux.stdin.close()  # EOF requests controller child cleanup.
            except Exception as exc:
                cleanup_errors.append('controller_stdin:' + type(exc).__name__)
            try:
                if linux.poll() is None:
                    try:
                        linux.wait(timeout=80)
                    except subprocess.TimeoutExpired:
                        result.update(status='blocked', forced_controller_cleanup=True)
                        linux.kill()
                        linux.wait(timeout=5)
                if linux.stdout:
                    linux.stdout.close()
            except Exception as exc:
                cleanup_errors.append('controller_process:' + type(exc).__name__)
        result['cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            result['status'] = 'blocked'
        # Outer preflight finally unregisters the entire uniquely owned distro.
    return result
