"""Official Incoming Webhook task notices, never Midjourney execution.

Only manual-notify invokes this module. Never log URLs, exceptions or responses:
webhook URLs are credentials, and server bodies may echo private task content.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import httpx

from render_bridge.image_options import validate_image_request

_WEBHOOK = re.compile(r'https://discord\.com/api/(?:v10/)?webhooks/[0-9]{1,32}/[A-Za-z0-9_-]{1,256}')
_SNOWFLAKE = re.compile(r'[0-9]{1,32}')


class DiscordTaskNotifier:
    def __init__(self, webhook_url: str, *, transport=None):
        if not isinstance(webhook_url, str) or not _WEBHOOK.fullmatch(webhook_url):
            raise ValueError('Expected an official Discord incoming webhook URL in the local environment')
        self._url = webhook_url
        self._transport = transport

    @staticmethod
    def attachments(request):
        validate_image_request(request.parameters, request.references, 'midjourney-manual')
        if request.references:
            raise ValueError('Discord reference sharing is not enabled')
        prompt = request.prompt.encode('utf-8')
        task = request.payload() | {
            'profile_id': 'midjourney-manual', 'request_sha256': request.digest,
            'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
            'requires_human': True,
            'warning': 'Ordinary task notice only. Submit /imagine manually; no generation has started.',
        }
        task_bytes = json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(prompt) + len(task_bytes) > 1_048_576:
            raise ValueError('Task notice exceeds the bounded attachment limit')
        return prompt, task_bytes

    def send(self, request, journal, record):
        """Caller owns the Agent instance lock and verifies a live matching task."""
        previous = record.get('discord_notification')
        if previous:
            return dict(previous)
        prompt, task_bytes = self.attachments(request)
        summary = (f'Manual task {request.task_id} / attempt {request.attempt_id}\n'
                   'Profile: midjourney-manual. Complete prompt and parameters attached.\n'
                   f'Request SHA-256: {request.digest}\n'
                   'Task notice only, NOT a Midjourney submission. Submit manually and import one original.')
        assert len(summary) <= 2000
        payload = {'content': summary, 'allowed_mentions': {'parse': [], 'users': [], 'roles': []},
                   'flags': 4, 'attachments': [{'id': 0, 'filename': 'prompt.txt'},
                                               {'id': 1, 'filename': 'task.json'}]}
        evidence = {'outcome': 'notification_unknown', 'request_sha256': request.digest,
                    'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
                    'attachment_sha256': hashlib.sha256(task_bytes).hexdigest()}
        record['discord_notification'] = evidence
        journal.save(record)  # Commit the no-resend latch BEFORE crossing HTTP.
        try:
            with httpx.Client(transport=self._transport, timeout=20, follow_redirects=False,
                              trust_env=False) as client:
                response = client.post(self._url, params={'wait': 'true'},
                    data={'payload_json': json.dumps(payload)},
                    files={'files[0]': ('prompt.txt', prompt, 'text/plain; charset=utf-8'),
                           'files[1]': ('task.json', task_bytes, 'application/json')})
            if response.status_code == 200:
                body = response.json()
                message_id = body.get('id') if isinstance(body, dict) else None
                if isinstance(message_id, str) and _SNOWFLAKE.fullmatch(message_id):
                    evidence = evidence | {'outcome': 'notification_delivered', 'message_id': message_id}
            elif 400 <= response.status_code < 500:
                evidence = evidence | {'outcome': 'notification_failed', 'http_status': response.status_code}
                if response.status_code == 429:
                    # Metadata only, never sleep/retry automatically.
                    raw = response.headers.get('Retry-After')
                    if raw is None:
                        try:
                            raw = response.json().get('retry_after')
                        except (ValueError, AttributeError):
                            pass
                    try:
                        seconds = float(raw)
                        if math.isfinite(seconds) and 0 <= seconds <= 86400:
                            evidence['retry_after_seconds'] = seconds
                    except (ValueError, TypeError):
                        pass
        except Exception:
            # A failed read or transport response does not prove no message was sent.
            pass
        record['discord_notification'] = evidence
        journal.save(record)
        return dict(evidence)
