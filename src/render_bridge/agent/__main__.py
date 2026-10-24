"""Explicit development delivery, not a polling scheduler."""
import argparse
import json
import os
from pathlib import Path

from . import HttpNodeClient, NodeAgent
from .registry import ExecutorRegistry
from .maintenance import maintain_agent


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
    reconcile = sub.add_parser('reconcile-codex-interrupted',
        help='Verify a completed image from the same aborted Codex turn; upload the original attempt only')
    reconcile.add_argument('task_id')
    reconcile.add_argument('rollout_file', type=Path,
        help='Codex local rollout JSONL containing the bound image item and terminal turn_aborted event')
    completed = sub.add_parser('reconcile-codex-completed',
        help='Verify a completed image from the same Codex rollout after Agent stream loss')
    completed.add_argument('task_id')
    completed.add_argument('rollout_file', type=Path,
        help='Codex local rollout JSONL containing the bound image item and terminal task_complete event')
    notice = sub.add_parser('manual-notify', help='Explicitly send an ordinary Discord task notice, NOT /imagine')
    notice.add_argument('task_id')
    notice.add_argument('--request-sha256', required=True)
    notice.add_argument('--enable-discord-notifications', action='store_true', required=True,
                        help='Authorize this task prompt/parameters to the configured webhook channel')
    maintenance = sub.add_parser('maintain', help='Reclaim acknowledged managed media after retention')
    maintenance.add_argument('--retention-seconds', type=int, default=86400)
    maintenance.add_argument('--batch-size', type=int, default=20)
    args = parser.parse_args(argv)
    token = os.environ.get('BRIDGE_NODE_TOKEN')
    if not token:
        parser.error('Set BRIDGE_NODE_TOKEN in the process environment')
    client = HttpNodeClient(args.url, token)
    try:
        registry = ExecutorRegistry.from_config(args.state_dir / 'executors', args.config)
        agent = NodeAgent(client, args.state_dir, registry=registry)
        if args.command == 'manual-notify':
            from .discord_notice import DiscordTaskNotifier
            notifier = DiscordTaskNotifier(os.environ.get('BRIDGE_DISCORD_WEBHOOK_URL'))
            result = agent.manual_notify(args.task_id, notifier,
                confirmed_request_sha256=args.request_sha256)
        elif args.command == 'maintain':
            result = maintain_agent(agent, retention_seconds=args.retention_seconds, batch_size=args.batch_size)
        elif args.command == 'manual-import':
            result = agent.manual_import(args.task_id, args.file,
                confirmed_request_sha256=args.request_sha256,
                confirmed_single_original=args.confirm_single_original)
        elif args.command == 'reconcile-codex-interrupted':
            result = agent.reconcile_codex_interrupted(args.task_id, args.rollout_file)
        elif args.command == 'reconcile-codex-completed':
            result = agent.reconcile_codex_completed(args.task_id, args.rollout_file)
        else:
            result = agent.execute_task(args.task_id)
        print(json.dumps(result))
        return 0 if result['outcome'] in ('artifact_committed', 'already_committed', 'awaiting_user', 'notification_delivered', 'maintenance_complete') else 2
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
