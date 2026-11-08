"""Timing diagnostics are observations, never execution/acceptance authority."""
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.executors.codex import CodexConfig
from render_bridge.executors.codex_timing import SessionTiming, read_transport_events


class CodexTimingTests(unittest.TestCase):
    def test_latency_separates_first_delta_from_tool_start_and_drops_payloads(self):
        with patch('render_bridge.executors.codex_timing.time.monotonic', return_value=10):
            timing = SessionTiming()
        with patch('render_bridge.executors.codex_timing.time.monotonic', return_value=11):
            timing.request_sent('turn/start', 3, {'threadId': 'thread'})
            timing.observe({'id': 3, 'result': {'turn': {'id': 'turn'}}})
        with patch('render_bridge.executors.codex_timing.time.monotonic', return_value=112):
            timing.observe({'method': 'item/agentMessage/delta', 'params': {
                'threadId': 'thread', 'turnId': 'turn', 'delta': 'private prompt'}})
        with patch('render_bridge.executors.codex_timing.time.monotonic', return_value=125):
            timing.observe({'method': 'item/started', 'params': {
                'threadId': 'thread', 'turnId': 'turn',
                'item': {'type': 'Extension', 'kind': 'image_gen.generation', 'arguments': 'secret'}}})
            timing.observe({'method': 'item/agentMessage/delta', 'params': {
                'threadId': 'thread', 'turnId': 'turn', 'delta': 'more private text'}})
        data = timing.snapshot()
        self.assertEqual(data['milestones_ms']['turn_request_sent'], 1000)
        self.assertEqual(data['milestones_ms']['first_model_delta'], 102000)
        self.assertEqual(data['milestones_ms']['image_started'], 115000)
        self.assertNotIn('private', json.dumps(data))
        self.assertNotIn('secret', json.dumps(data))

    def test_timing_ignores_other_turns_and_bounds_unknown_events(self):
        timing = SessionTiming()
        timing.request_sent('turn/start', 3, {'threadId': 'thread'})
        timing.observe({'id': 3, 'result': {'turn': {'id': 'turn'}}})
        for _ in range(2000):
            timing.observe({'method': 'item/agentMessage/delta', 'params': {
                'threadId': 'other-thread', 'turnId': 'turn', 'delta': 'secret'}})
            timing.observe({'method': 'unknown-private-method', 'params': {'item': {'type': []}}})
        self.assertNotIn('first_model_delta', timing.snapshot()['milestones_ms'])
        self.assertLess(len(json.dumps(timing.snapshot())), 500)

    def test_first_usage_and_retry_count_contain_only_numeric_metadata(self):
        timing = SessionTiming()
        timing.request_sent('turn/start', 3, {'threadId': 't'})
        timing.observe({'id': 3, 'result': {'turn': {'id': 'r'}}})
        timing.observe({'method': 'error', 'params': {'threadId': 't', 'turnId': 'r',
            'willRetry': True, 'error': {'message': 'private URL and credential'}}})
        for count in (25000, 27000):
            timing.observe({'method': 'thread/tokenUsage/updated', 'params': {
                'threadId': 't', 'turnId': 'r', 'tokenUsage': {'last': {
                    'inputTokens': count, 'cachedInputTokens': 12000,
                    'outputTokens': 900, 'reasoningOutputTokens': 20, 'extra': 'secret'}}}})
        data = timing.snapshot()
        self.assertEqual(data['retry_notifications'], 1)
        self.assertEqual(data['first_usage']['inputTokens'], 25000)
        self.assertNotIn('secret', json.dumps(data))
        self.assertNotIn('private', json.dumps(data))

    def test_readonly_network_timeline_is_thread_scoped_and_drops_headers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'logs.sqlite'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE logs(ts INTEGER,ts_nanos INTEGER,target TEXT,feedback_log_body TEXT,thread_id TEXT)')
            rows = [
                (1, 0, 'codex_api::endpoint::responses_websocket', 'connecting to websocket: wss://secret', 't'),
                (16, 0, 'codex_core::responses_retry', 'stream disconnected - retrying sampling request (1/5) sampling_error=request timed out', 't'),
                (97, 0, 'codex_core::client', 'falling back to HTTP', 't'),
                (98, 0, 'codex_http_client::client', 'Request completed method=POST url=https://secret status=200 OK headers={secret}', 't'),
                (99, 0, 'codex_core::stream_events_utils', 'Output item item_type="message" item_id="private"', 't'),
                (100, 0, 'codex_core::stream_events_utils', 'Output item item_type="message"', 'another'),
                (399, 0, 'codex_core::responses_retry', 'stream disconnected - retrying sampling request (1/5) sampling_error=stream disconnected before completion: idle timeout waiting for SSE', 't'),
            ]
            db.executemany('INSERT INTO logs VALUES(?,?,?,?,?)', rows)
            db.commit()
            db.close()
            result = read_transport_events(path, 't')
            self.assertEqual([e['event'] for e in result['events']], [
                'websocket_connect', 'sampling_retry_timeout', 'http_fallback',
                'http_response_headers', 'model_output_item', 'sampling_retry_sse_idle'])
            self.assertEqual(result['events'][2]['unix_ms'] - result['events'][0]['unix_ms'], 96000)
            self.assertNotIn('secret', json.dumps(result))
            self.assertNotIn('private', json.dumps(result))

    def test_transport_and_context_controls_are_trusted_typed_config(self):
        self.assertEqual(CodexConfig().responses_transport, 'auto')
        self.assertTrue(CodexConfig().include_skill_instructions)
        for value in ('proxy', None, True):
            with self.assertRaises(ValueError):
                CodexConfig(responses_transport=value)
        with self.assertRaises(ValueError):
            CodexConfig(include_skill_instructions='false')
