"""Pinned SDK sidecar: dispatch handoff or one-slot Agent worker.

Run in the separate SDK interpreter, not the Bridge/FastAPI interpreter. This
module never creates a Hatchet account or token and never logs supplied tokens.
"""
import argparse
from datetime import timedelta
import importlib.metadata
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from .client import HttpNodeClient, validated_url
from .runner import NodeAgent
from .registry import ExecutorRegistry

TASK_NAME = 'bridge-node-execute-v1'


class DispatchClient(HttpNodeClient):
    prefix = '/bridge/dispatch/v1'

    def pending(self):
        return self.request('GET', '/pending', params={'limit': 10}).json()['items']

    def acknowledge(self, dispatch_key, run_id):
        return self.request('POST', '/' + self.key(dispatch_key) + '/ack',
                            json={'engine_run_id': run_id}).json()


def dispatch_once(client, submit):
    """Deliver outbox records to Hatchet, never execute jobs here.

    A failed/unknown submit is not acknowledged. Repeated delivery is allowed:
    the server start latch and Agent journal prevent another physical execution.
    """
    count = 0
    for item in client.pending():
        run_id = submit(item['task_id'], item['dispatch_key'])
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError('Hatchet did not return a run identifier')
        client.acknowledge(item['dispatch_key'], run_id)
        count += 1
    return count


def input_type():
    from pydantic import BaseModel, ConfigDict
    class TaskInput(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        task_id: str
    return TaskInput


def make_submitter(hatchet):
    # Verified in upstream hatchet-sdk 1.42.1: features/stubs.py task(),
    # runnables/workflow.py Standalone.run(wait_for_result=False), TaskRunRef.
    Input = input_type()
    stub = hatchet.stubs.task(name=TASK_NAME, input_validator=Input)
    def submit(task_id, dispatch_key):
        ref = stub.run(input=Input(task_id=task_id), wait_for_result=False,
                       additional_metadata={'bridge_dispatch_key': dispatch_key})
        return ref.workflow_run_id
    return submit


def configured_hatchet(environ=None):
    env = os.environ if environ is None else environ
    version = importlib.metadata.version('hatchet-sdk')
    expected = '1.42.1+bridgewin1' if sys.platform == 'win32' else '1.42.1'
    if version != expected:
        raise RuntimeError(f'Requires fixed hatchet-sdk {expected}')
    token = env.get('HATCHET_CLIENT_TOKEN')
    endpoint = env.get('HATCHET_CLIENT_HOST_PORT')
    server_url = env.get('HATCHET_CLIENT_SERVER_URL')
    if not token or not endpoint or not server_url:
        raise ValueError('Supply Hatchet token, host port and server URL explicitly')
    validated_url(server_url)
    strategy = env.get('HATCHET_CLIENT_TLS_STRATEGY', 'tls')
    if strategy not in ('none', 'tls'):
        raise ValueError('Unsupported Hatchet TLS strategy')
    if strategy == 'none':
        host = urlsplit('//' + endpoint).hostname
        try:
            local = ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            local = host == 'localhost'
        if not local:
            raise ValueError('Plaintext Hatchet is limited to loopback development')
    from hatchet_sdk import Hatchet
    from hatchet_sdk.config import ClientConfig, ClientTLSConfig, HealthcheckConfig
    tls = ClientTLSConfig(strategy=strategy)
    return Hatchet(config=ClientConfig(token=token, host_port=endpoint, server_url=server_url,
                   tls_config=tls, healthcheck=HealthcheckConfig(enabled=False)))


def make_worker(hatchet, node, executor=None, name='bridge-node-one-slot'):
    Input = input_type()
    from hatchet_sdk import Context
    @hatchet.task(name=TASK_NAME, input_validator=Input, retries=2,
                  execution_timeout=timedelta(minutes=55), schedule_timeout=timedelta(hours=1))
    def execute(input: Input, ctx: Context) -> dict:
        result = node.execute_task(input.task_id, executor)
        if result['outcome'] in ('upload_pending', 'physical_slot_busy', 'failure_report_pending'):
            # Hatchet owns bounded retries; Agent reuses the same attempt/artifact.
            raise RuntimeError('Bridge attempt needs a bounded retry')
        return result
    return hatchet.worker(name, slots=1, workflows=[execute])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('BRIDGE_URL', 'http://127.0.0.1:8787'))
    sub = parser.add_subparsers(dest='command', required=True)
    dispatch = sub.add_parser('dispatcher')
    dispatch.add_argument('--once', action='store_true')
    dispatch.add_argument('--interval', type=float, default=2)
    worker = sub.add_parser('worker')
    worker.add_argument('--state-dir', type=Path, required=True)
    worker.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    client = None
    try:
        hatchet = configured_hatchet()
        if args.command == 'dispatcher':
            token = os.environ.get('BRIDGE_DISPATCH_TOKEN')
            if not token:
                raise ValueError('BRIDGE_DISPATCH_TOKEN is required')
            if not 0.2 <= args.interval <= 60:
                raise ValueError('Invalid dispatcher interval')
            client = DispatchClient(args.url, token)
            submit = make_submitter(hatchet)
            while True:
                try:
                    delivered = dispatch_once(client, submit)
                    print(json.dumps({'submitted': delivered}), flush=True)
                except Exception:
                    # Do not print arbitrary SDK/network exception text or tokens.
                    print(json.dumps({'status': 'handoff_pending'}), flush=True)
                    if args.once:
                        return 2
                if args.once:
                    return 0
                time.sleep(args.interval)
        else:
            token = os.environ.get('BRIDGE_NODE_TOKEN')
            if not token:
                raise ValueError('BRIDGE_NODE_TOKEN is required')
            client = HttpNodeClient(args.url, token)
            registry = ExecutorRegistry.from_config(args.state_dir / 'executors', args.config)
            node = NodeAgent(client, args.state_dir, registry=registry)
            from .heartbeat import HeartbeatLoop
            with HeartbeatLoop(node):
                make_worker(hatchet, node).start()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 2
    finally:
        if client:
            client.close()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
