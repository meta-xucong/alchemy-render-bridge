"""Explicit development delivery, not a polling scheduler."""
import argparse
import json
import os
from pathlib import Path

from . import HttpNodeClient, NodeAgent
from render_bridge.executors.fake import FakeExecutor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('BRIDGE_URL', 'http://127.0.0.1:8787'))
    parser.add_argument('--state-dir', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run-task', help='Explicitly deliver one task for development; no waiting queue')
    run.add_argument('task_id')
    args = parser.parse_args(argv)
    token = os.environ.get('BRIDGE_NODE_TOKEN')
    if not token:
        parser.error('Set BRIDGE_NODE_TOKEN in the process environment')
    client = HttpNodeClient(args.url, token)
    try:
        agent = NodeAgent(client, args.state_dir)
        result = agent.execute_task(args.task_id, FakeExecutor(args.state_dir / 'executors'))
        print(json.dumps(result))
        return 0 if result['outcome'] in ('artifact_committed', 'already_committed') else 2
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
