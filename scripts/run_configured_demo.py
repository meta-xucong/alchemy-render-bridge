"""Run the configured server and Agent as separate processes with a mock dispatcher.

No Hatchet, external provider, account login, Alchemy, or public listener is used.
Only explicit synthetic outputs survive; temporary runtime state is removed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def run_demo(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    process = None
    result = {}
    with tempfile.TemporaryDirectory(prefix='configured-bridge-demo-') as temp:
        root = Path(temp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = dict(os.environ)
        env.update(PYTHONPATH=str(repo / 'src'), PYTHONUTF8='1',
                   BRIDGE_SOURCE_TOKEN=secrets.token_urlsafe(32),
                   BRIDGE_NODE_TOKEN=secrets.token_urlsafe(32),
                   BRIDGE_DISPATCH_TOKEN=secrets.token_urlsafe(32))
        config = {'data_dir': str(root / 'server'), 'source_id': 'demo-source',
                  'workspace_id': 'demo-workspace', 'node_id': 'demo-node',
                  'executor_instance': 'demo-executor', 'host': '127.0.0.1', 'port': port}
        path = root / 'server.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        process = subprocess.Popen([sys.executable, '-m', 'render_bridge.runtime', '--config', str(path)],
                                   cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=url, headers={'Authorization': 'Bearer ' + env['BRIDGE_SOURCE_TOKEN']},
                              timeout=5, trust_env=False) as source, httpx.Client(base_url=url,
                              headers={'Authorization': 'Bearer ' + env['BRIDGE_DISPATCH_TOKEN']},
                              timeout=5, trust_env=False) as dispatcher:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        source.get('/bridge/v1/capabilities').raise_for_status()
                        break
                    except httpx.HTTPError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Configured server failed to start') from None
                        time.sleep(.05)
                request = {'schema_version': '1', 'origin_key': 'configured-demo',
                           'binding_id': 'demo-only', 'operation': 'image.generate',
                           'execution_profile_id': 'fake-image', 'execution_profile_revision': '1',
                           'prompt': 'Synthetic configurable Bridge demo',
                           'parameters': {'width': 48, 'height': 32, 'seed': 0}, 'references': [],
                           'output_slot': {'slot_id': 'primary', 'count': 1}, 'routing': 'bridge_only',
                           'deadline': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
                def create():
                    response = source.post('/bridge/v1/tasks', json=request)
                    response.raise_for_status()
                    return response.json()
                task = create()
                task_id = task['remote_task_id']
                if create()['remote_task_id'] != task_id:
                    raise RuntimeError('Create replay changed task')
                response = dispatcher.get('/bridge/dispatch/v1/pending')
                response.raise_for_status()
                pending = response.json()['items']
                if len(pending) != 1 or pending[0]['task_id'] != task_id:
                    raise RuntimeError('Dynamic outbox did not contain the task')
                # Explicit mock-engine delivery. This is not a second scheduler.
                command = [sys.executable, '-m', 'render_bridge.agent', '--url', url,
                           '--state-dir', str(root / 'agent'), 'run-task', task_id]
                first = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=30)
                if first.returncode:
                    raise RuntimeError('Agent command failed')
                executed = json.loads(first.stdout)
                response = dispatcher.post('/bridge/dispatch/v1/' + pending[0]['dispatch_key'] + '/ack',
                                           json={'engine_run_id': 'explicit-mock-engine-run'})
                response.raise_for_status()
                second = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=30)
                if second.returncode:
                    raise RuntimeError('Agent replay command failed')
                replay = json.loads(second.stdout)
                if executed['outcome'] != 'artifact_committed' or replay['outcome'] != 'already_committed':
                    raise RuntimeError('Unexpected Agent outcomes')
                response = source.get('/bridge/v1/tasks/' + task_id)
                response.raise_for_status()
                receipt_id = response.json()['receipt_id']
                response = source.get('/bridge/v1/receipts/' + receipt_id)
                response.raise_for_status()
                receipt = response.json()
                response = source.get('/bridge/v1/artifacts/' + receipt['artifact_id'] + '/content')
                response.raise_for_status()
                content = response.content
                digest = hashlib.sha256(content).hexdigest()
                if digest != receipt['original_sha256']:
                    raise RuntimeError('Downloaded artifact hash mismatch')
                response = source.post('/bridge/v1/receipts/' + receipt_id + '/ack', json={
                    'client_receipt_id': 'configured-demo-ack', 'decision': 'accepted',
                    'reason_code': 'synthetic_demo_verified', 'original_hash': digest})
                response.raise_for_status()
                starts = list((root / 'agent' / 'executors').rglob('started'))
                if len(starts) != 1:
                    raise RuntimeError('Expected one fake execution start')
                response = dispatcher.get('/bridge/dispatch/v1/pending')
                response.raise_for_status()
                if response.json()['items']:
                    raise RuntimeError('Outbox acknowledgement was not persisted')
                (output_dir / 'synthetic.png').write_bytes(content)
                result = {'status': 'passed', 'mode': 'configured_processes_mock_dispatcher',
                          'source_transport': 'http', 'node_transport': 'http',
                          'server_process': True, 'agent_process': True,
                          'dynamic_outbox': True, 'same_origin_replay': True,
                          'agent_restart_replay': replay['outcome'], 'fake_render_starts': len(starts),
                          'artifact_sha256': digest, 'artifact_bytes': len(content),
                          'receipt_ack': 'accepted', 'real_hatchet': False, 'real_provider': False}
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            result['server_process_stopped'] = process is not None and process.poll() is not None
    result['temporary_runtime_removed'] = not root.exists()
    (output_dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output_dir.resolve()), indent=2))


if __name__ == '__main__':
    main()
