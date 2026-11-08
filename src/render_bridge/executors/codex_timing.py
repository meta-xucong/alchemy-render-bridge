"""Bounded, payload-free client timing; never execution or recovery proof."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time


class SessionTiming:
    def __init__(self):
        self.origin = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.milestones = {}
        self.pending = {}
        self.thread_id = None
        self.turn_id = None
        self.retry_count = 0
        self.first_usage = None
        self.lock = threading.Lock()

    def _mark(self, label):
        self.milestones.setdefault(label, round((time.monotonic() - self.origin) * 1000, 3))

    def request_sent(self, method, request_id, params):
        label = {'initialize': 'initialize', 'thread/start': 'thread', 'turn/start': 'turn'}.get(method)
        if label is None:
            return
        with self.lock:
            self.pending[request_id] = label
            self._mark(label + '_request_sent')
            if method == 'turn/start':
                self.thread_id = params.get('threadId')

    def observe(self, value):
        if not isinstance(value, dict):
            return
        with self.lock:
            response_id = value.get('id')
            if isinstance(response_id, int) and response_id in self.pending and 'method' not in value:
                label = self.pending.pop(response_id)
                self._mark(label + '_response_received')
                if label == 'turn':
                    result = value.get('result')
                    turn = result.get('turn') if isinstance(result, dict) else None
                    self.turn_id = turn.get('id') if isinstance(turn, dict) else None
                return
            params = value.get('params')
            if not isinstance(params, dict) or not self.thread_id or not self.turn_id:
                return
            turn = params.get('turn')
            turn_id = params.get('turnId') or (turn.get('id') if isinstance(turn, dict) else None)
            if params.get('threadId') != self.thread_id or turn_id != self.turn_id:
                return
            method = value.get('method')
            if method in ('item/agentMessage/delta', 'item/reasoning/textDelta',
                          'item/reasoning/summaryTextDelta'):
                self._mark('first_model_delta')
            elif method == 'error' and params.get('willRetry') is True:
                self.retry_count += 1
                self._mark('first_retry_notification')
            elif method == 'turn/completed':
                self._mark('turn_completed')
            elif method in ('item/started', 'item/completed'):
                item = params.get('item')
                if isinstance(item, dict) and (item.get('type') == 'imageGeneration' or
                        (item.get('type') == 'Extension' and item.get('kind') == 'image_gen.generation')):
                    self._mark('image_started' if method == 'item/started' else 'image_completed')
            elif method == 'thread/tokenUsage/updated' and self.first_usage is None:
                usage = params.get('tokenUsage')
                last = usage.get('last') if isinstance(usage, dict) else None
                if isinstance(last, dict):
                    numeric = {k: last[k] for k in ('inputTokens', 'cachedInputTokens',
                               'outputTokens', 'reasoningOutputTokens')
                               if type(last.get(k)) is int and last[k] >= 0}
                    if numeric.get('inputTokens', 0) > 0:
                        self.first_usage = numeric

    def snapshot(self):
        with self.lock:
            return {'started_at': self.started_at, 'clock': 'client_monotonic',
                    'milestones_ms': dict(self.milestones),
                    'retry_notifications': self.retry_count,
                    **({'first_usage': dict(self.first_usage)} if self.first_usage else {})}


def read_transport_events(db_path: Path, thread_id: str) -> dict:
    """Inspect the CLI's existing logs, retaining labels/times only. No network I/O.

    Schema/source: accepted CLI 0.163.0. Absence is missing evidence, not zero
    provider time. This is an explicit diagnostic, not a runtime DB dependency.
    """
    rules = (
        ('codex_core::session::startup_prewarm', 'startup websocket prewarm timed out', 'websocket_prewarm_timeout'),
        ('codex_api::endpoint::responses_websocket', 'connecting to websocket:', 'websocket_connect'),
        ('codex_core::responses_retry', 'sampling_error=request timed out', 'sampling_retry_timeout'),
        ('codex_core::responses_retry', 'idle timeout waiting for SSE', 'sampling_retry_sse_idle'),
        ('codex_core::client', 'falling back to HTTP', 'http_fallback'),
        ('codex_http_client::request', 'Compressed request body with zstd', 'http_request_prepared'),
        ('codex_http_client::client', 'Request completed method=POST', 'http_response_headers'),
        ('codex_core::stream_events_utils', 'Output item item_type=', 'model_output_item'),
        ('codex_core::stream_events_utils', 'ToolCall: exec ', 'tool_exec_dispatch'),
    )
    targets = tuple(dict.fromkeys(target for target, _, _ in rules))
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = connection.execute(
            'SELECT ts,ts_nanos,target,feedback_log_body FROM logs WHERE thread_id=? AND target IN ('
            + ','.join('?' for _ in targets) + ') ORDER BY ts,ts_nanos LIMIT 257', (thread_id, *targets)).fetchall()
    finally:
        connection.close()
    events = []
    for ts, nanos, target, body in rows[:256]:
        for expected_target, marker, label in rules:
            if target == expected_target and marker in (body or ''):
                events.append({'event': label, 'unix_ms': round(ts * 1000 + nanos / 1_000_000, 3)})
                break
    return {'thread_id': thread_id, 'source': 'codex_local_logs',
            'truncated': len(rows) > 256, 'events': events}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--thread-id', required=True)
    args = parser.parse_args()
    print(json.dumps(read_transport_events(args.db, args.thread_id), indent=2))
