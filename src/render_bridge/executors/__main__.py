"""Explicit offline executor commands; real Codex rendering is not enabled."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from . import ExecutionRequest, FakeExecutor, MidjourneyManualExecutor, probe_codex


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    probe = sub.add_parser('probe-codex', help='Version/help only; no login or generation')
    probe.add_argument('--executable', default='codex')
    for name in ('fake', 'manual-export', 'manual-import'):
        command = sub.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--request', type=Path, required=True,
                             help='JSON task_id/attempt_id/prompt/parameters/references')
        if name == 'manual-import':
            command.add_argument('--file', type=Path, required=True)
            command.add_argument('--confirm-request-sha256', required=True)
            command.add_argument('--confirm-single-original', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'probe-codex':
            result = probe_codex(args.executable)
        else:
            if args.request.stat().st_size > 65536:
                raise ValueError('Request file too large')
            payload = json.loads(args.request.read_text(encoding='utf-8'))
            request = ExecutionRequest(**payload)
            if args.command == 'fake':
                result = FakeExecutor(args.root).execute(request)
            else:
                executor = MidjourneyManualExecutor(args.root)
                if args.command == 'manual-export':
                    result = executor.execute(request)
                else:
                    result = executor.import_original(request, args.file,
                        confirmed_request_sha256=args.confirm_request_sha256,
                        confirmed_single_original=args.confirm_single_original)
            result = asdict(result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (OSError, TypeError, ValueError):
        print(json.dumps({'status': 'needs_attention', 'code': 'invalid_local_executor_request'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
