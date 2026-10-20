"""Explicit development delivery, not a polling scheduler."""
import argparse
import json
import os
from pathlib import Path

from . import HttpNodeClient, NodeAgent
from .registry import ExecutorRegistry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('BRIDGE_URL', 'http://127.0.0.1:8787'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, help='Local non-secret executor configuration')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run-task', help='Explicitly deliver one task for development; no waiting queue')
    run.add_argument('task_id')
    manual = sub.add_parser('manual-import', help='Import an explicitly selected single original')
    manual.add_argument('task_id')
    manual.add_argument('file', type=Path)
    manual.add_argument('--request-sha256', required=True)
    manual.add_argument('--confirm-single-original', action='store_true', required=True)
    args = parser.parse_args(argv)
    token = os.environ.get('BRIDGE_NODE_TOKEN')
    if not token:
        parser.error('Set BRIDGE_NODE_TOKEN in the process environment')
    client = HttpNodeClient(args.url, token)
    try:
        registry = ExecutorRegistry.from_config(args.state_dir / 'executors', args.config)
        agent = NodeAgent(client, args.state_dir, registry=registry)
        if args.command == 'manual-import':
            result = agent.manual_import(args.task_id, args.file,
                confirmed_request_sha256=args.request_sha256,
                confirmed_single_original=args.confirm_single_original)
        else:
            result = agent.execute_task(args.task_id)
        print(json.dumps(result))
        return 0 if result['outcome'] in ('artifact_committed', 'already_committed', 'awaiting_user') else 2
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
