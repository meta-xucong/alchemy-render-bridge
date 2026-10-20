"""Bounded, disposable WSL1 capability probe. No engine or credentials are created."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOTFS_URL = 'https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz'
ROOTFS_SHA256 = '2a790896740b14d637dbdc583cce1ba081ac53b9e9cdb46dc09a2f73abbd9934'
NAME_PATTERN = re.compile(r'^bridge-wsl1-[0-9a-f]{32}$')

CANARY = r'''
import json, os, socket, time
# No HTTP/file serving, authentication, or user data. Single synthetic exchange.
s = socket.socket()
s.settimeout(40)
s.bind(('127.0.0.1', 0))
s.listen(1)
port = s.getsockname()[1]
rows = [line.split() for line in open('/proc/net/tcp') if ':' in line]
owned = [row for row in rows if row[1].endswith(':%04X' % port) and row[3] == '0A']
assert len(owned) == 1 and owned[0][1] == '0100007F:%04X' % port, owned
print(json.dumps({'port': port, 'bind': '127.0.0.1', 'linux_listener_verified': True}), flush=True)
try:
    c, address = s.accept()
    with c:
        c.settimeout(5)
        assert address[0] == '127.0.0.1', address
        assert c.recv(64) == b'bridge-wsl1-probe\n'
        c.sendall(b'bridge-wsl1-ok\n')
finally:
    s.close()
'''


def decode(data: bytes) -> str:
    # wsl.exe uses UTF-16LE for redirected inventory on some runner versions.
    return data.decode('utf-16-le' if b'\0' in data[:100] else 'utf-8', errors='replace').lstrip('\ufeff').strip()


def run(args: list[str], timeout: int = 30, check: bool = True) -> str:
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    output = decode(p.stdout)
    if check and p.returncode:
        raise RuntimeError(f'{args[0]} {args[1:3]} exited {p.returncode}: {output[:1500]}')
    return output


def names() -> set[str]:
    # Read-only registration inventory avoids localized WSL "no distros" errors.
    script = r"""$ErrorActionPreference = 'Stop';
$p = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss';
$a = @(); if (Test-Path $p) {
  $a = @(Get-ChildItem $p | ForEach-Object {
    $v = Get-ItemProperty $_.PSPath;
    if ($null -ne $v.DistributionName) { [string]$v.DistributionName }
  })
}; ConvertTo-Json -InputObject @($a) -Compress"""
    value = json.loads(run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script]))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError('Unverifiable WSL registration inventory')
    return set(value)


def verify_digest(path: Path) -> None:
    with path.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    if actual != ROOTFS_SHA256:
        raise RuntimeError('Official Ubuntu rootfs SHA256 mismatch; import prohibited')


def download(path: Path) -> None:
    deadline = time.monotonic() + 240
    size = 0
    with urllib.request.urlopen(ROOTFS_URL, timeout=20) as response, path.open('wb') as f:
        if response.url != ROOTFS_URL:
            raise RuntimeError('Unexpected rootfs redirect; stop for review')
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 600 * 1024 * 1024 or time.monotonic() > deadline:
                raise RuntimeError('Rootfs download exceeded size/time bound')
            f.write(chunk)
    verify_digest(path)


def validate_state(state: dict, base: Path) -> Path:
    name = state.get('distro', '')
    if not NAME_PATTERN.fullmatch(name):
        raise RuntimeError('Refusing cleanup of unowned distro name')
    root = Path(state['root']).resolve()
    if root.parent != base.resolve() or root.name != name:
        raise RuntimeError('Refusing cleanup outside owned temporary directory')
    return root


def cleanup(state_path: Path, base: Path) -> list[str]:
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        root = validate_state(state, base)
    except Exception as exc:
        return ['Invalid cleanup state; no deletion attempted: ' + str(exc)]
    errors = []
    # State is saved before import, so interrupted/partial import is included.
    try:
        if state['distro'] in names():
            run(['wsl.exe', '--terminate', state['distro']], timeout=30, check=False)
            run(['wsl.exe', '--unregister', state['distro']], timeout=60)
        if state['distro'] in names():
            raise RuntimeError('Owned distro is still registered')
    except Exception as exc:
        errors.append(str(exc))
    # Do not remove a possibly still-registered distro's backing files.
    if not errors:
        try:
            if root.exists():
                shutil.rmtree(root)
            state_path.unlink()
        except Exception as exc:
            errors.append(str(exc))
    return errors


def preflight(state_path: Path, base: Path, report_path: Path) -> int:
    result = {'status': 'blocked', 'stage': 'platform', 'engine_tested': False,
              'postgres_tested': False, 'rootfs_sha256': ROOTFS_SHA256,
              'image_os': os.environ.get('ImageOS'), 'image_version': os.environ.get('ImageVersion')}
    process = None
    log_handle = None
    try:
        if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
            raise RuntimeError('Run only on the disposable GitHub Windows runner')
        if state_path.exists():
            raise RuntimeError('Existing cleanup state must be resolved first')
        result['stage'] = 'existing_wsl1_feature'
        feature = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                       "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction Stop).State.ToString()"])
        if feature != 'Enabled':
            raise RuntimeError(f'WSL1 feature is not already enabled: {feature}; no settings changed')
        result['wsl_status'] = run(['wsl.exe', '--status'], check=False)
        result['inventory_before'] = run(['wsl.exe', '--list', '--verbose'], check=False)
        name = 'bridge-wsl1-' + uuid.uuid4().hex
        if name in names():
            raise RuntimeError('Unexpected distro-name collision')
        root = base / name
        root.mkdir()
        state_path.write_text(json.dumps({'distro': name, 'root': str(root)}), encoding='utf-8')
        result['stage'] = 'verified_rootfs_download'
        archive = root / 'ubuntu.tar.gz'
        download(archive)
        result['stage'] = 'wsl1_import'
        run(['wsl.exe', '--import', name, str(root / 'distro'), str(archive), '--version', '1'], timeout=180)
        result['inventory_imported'] = run(['wsl.exe', '--list', '--verbose'])
        matching = [line for line in result['inventory_imported'].splitlines() if name in line]
        if len(matching) != 1 or matching[0].split()[-1] != '1':
            raise RuntimeError('Imported distro is not verifiably WSL1')
        result['stage'] = 'linux_startup'
        result['linux_kernel'] = run(['wsl.exe', '-d', name, '-u', 'root', '--exec', '/bin/uname', '-a'])
        result['stage'] = 'loopback_canary'
        log_path = root / 'canary.log'
        log_handle = log_path.open('wb')
        process = subprocess.Popen(['wsl.exe', '-d', name, '-u', 'root', '--exec',
                                    '/usr/bin/python3', '-u', '-c', CANARY], stdout=log_handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 25
        ready = None
        while time.monotonic() < deadline:
            lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
            if lines:
                try:
                    ready = json.loads(lines[0])
                except ValueError:
                    raise RuntimeError('Linux canary failed: ' + '\n'.join(lines)[:1500])
                break
            if process.poll() is not None:
                raise RuntimeError('Linux canary exited before ready')
            time.sleep(0.2)
        if not ready or ready.get('bind') != '127.0.0.1' or ready.get('linux_listener_verified') is not True:
            raise RuntimeError('No verified loopback-only Linux canary listener')
        with socket.create_connection(('127.0.0.1', ready['port']), timeout=5) as connection:
            connection.sendall(b'bridge-wsl1-probe\n')
            if connection.recv(64) != b'bridge-wsl1-ok\n':
                raise RuntimeError('Unexpected canary response')
        if process.wait(timeout=10) != 0:
            raise RuntimeError('Linux canary did not exit cleanly')
        result['canary'] = ready
        result['status'] = 'passed'
        result['stage'] = 'preflight_complete_engine_and_postgres_not_tested'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        try:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        except Exception as exc:
            result['process_cleanup_error'] = str(exc)
        try:
            if log_handle:
                log_handle.close()
        except Exception as exc:
            result['log_cleanup_error'] = str(exc)
        try:
            errors = cleanup(state_path, base)
        except Exception as exc:
            errors = ['Unexpected cleanup failure: ' + str(exc)]
        result['cleanup_errors'] = errors
        if errors or result.get('process_cleanup_error') or result.get('log_cleanup_error'):
            result['status'] = 'blocked'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    base = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())).resolve()
    state = base / 'bridge-wsl1-owned.json'
    if args.cleanup:
        errors = cleanup(state, base)
        print(json.dumps({'cleanup_errors': errors}))
        return bool(errors)
    return preflight(state, base, Path('result.json'))


if __name__ == '__main__':
    raise SystemExit(main())
