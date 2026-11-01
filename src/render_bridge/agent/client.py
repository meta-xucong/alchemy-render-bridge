"""Node HTTP client. Never opens the Bridge database or storage directory."""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import quote, urlsplit

import httpx


class NodeHTTPError(RuntimeError):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(f'Node HTTP request failed ({status}: {code})')


def validated_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Endpoint must not contain credentials, query or fragment')
    if parsed.path not in ('', '/') or not parsed.hostname:
        raise ValueError('Endpoint must be an origin without a path')
    if parsed.scheme == 'https':
        return url.rstrip('/')
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == 'localhost'
    if parsed.scheme != 'http' or not local:
        raise ValueError('Non-loopback endpoints require HTTPS')
    return url.rstrip('/')


class HttpNodeClient:
    prefix = '/bridge/node/v1'

    def __init__(self, url: str, token: str, *, transport=None):
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('An explicit node token is required')
        ca_bundle = os.environ.get('BRIDGE_CA_BUNDLE')
        self.http = httpx.Client(base_url=validated_url(url),
                                 headers={'Authorization': 'Bearer ' + token},
                                 timeout=30, follow_redirects=False, trust_env=False,
                                 verify=ca_bundle or True,
                                 transport=transport)

    @staticmethod
    def key(value):
        return quote(str(value), safe='')

    def request(self, method, path, **kwargs):
        response = self.http.request(method, self.prefix + path, **kwargs)
        if not 200 <= response.status_code < 300:
            try:
                code = response.json()['error']['code']
                if not isinstance(code, str) or len(code) > 64:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                code = 'request_failed'
            raise NodeHTTPError(response.status_code, code)
        return response

    def heartbeat(self, state, profiles, *, profile_health=None):
        payload = {'state': state, 'profiles': profiles}
        if profile_health is not None:
            payload['profile_health'] = profile_health
        return self.request('POST', '/heartbeat', json=payload, timeout=5).json()

    def attention(self, task_id, attempt_id, code):
        return self.request('POST', f'/tasks/{self.key(task_id)}/attention',
                            json={'attempt_id': attempt_id, 'code': code}).json()

    def task(self, task_id):
        return self.request('GET', '/tasks/' + self.key(task_id)).json()

    def next_task(self):
        return self.request('GET', '/tasks/next', timeout=5).json().get('task_id')

    def claim(self, task_id, mode='execute'):
        return self.request('POST', f'/tasks/{self.key(task_id)}/claim', json={'mode': mode}).json()

    def manual_ready(self, task_id, claim_id):
        return self.request('POST', '/tasks/' + self.key(task_id) + '/manual-ready',
                            json={'claim_id': claim_id}).json()

    def start(self, task_id, claim_id):
        return self.request('POST', f'/tasks/{self.key(task_id)}/start', json={'claim_id': claim_id}).json()

    def terminal_failure(self, task_id, attempt_id, evidence):
        return self.request('POST', f'/tasks/{self.key(task_id)}/terminal-failure',
                            json={'attempt_id': attempt_id, 'evidence': evidence}).json()

    def unknown(self, task_id, claim_id):
        return self.request('POST', f'/tasks/{self.key(task_id)}/unknown', json={'claim_id': claim_id}).json()

    def input_content(self, task_id, input_id, claim_id):
        return self.request('GET', f'/tasks/{self.key(task_id)}/inputs/{self.key(input_id)}/content',
                            headers={'X-Bridge-Claim': claim_id}).content

    def create_upload(self, task_id, claim_id, payload):
        return self.request('POST', f'/tasks/{self.key(task_id)}/uploads',
                            json={'claim_id': claim_id, 'payload': payload}).json()

    def put_upload(self, upload_id, claim_id, content, mime_type):
        return self.request('PUT', f'/uploads/{self.key(upload_id)}/content', content=content,
                            headers={'X-Bridge-Claim': claim_id, 'Content-Type': mime_type}).json()

    def commit(self, upload_id, claim_id):
        return self.request('POST', f'/uploads/{self.key(upload_id)}/commit',
                            json={'claim_id': claim_id}).json()

    def close(self):
        self.http.close()
