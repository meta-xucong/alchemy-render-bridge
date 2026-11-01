"""Loopback-only source demo: real HTTP, existing source API, no scheduler.

The client has no ledger/filesystem access. The server wrapper is a temporary
fixture, not deployment configuration or production credential management.
"""
import hashlib
import secrets
import socket
import threading
import time

import httpx
import uvicorn

from render_bridge.api import create_app


class SourceClient:
    def __init__(self, port, token):
        self.http = httpx.Client(base_url=f'http://127.0.0.1:{port}',
                                 headers={'Authorization': 'Bearer ' + token},
                                 timeout=5, trust_env=False, follow_redirects=False)

    def request(self, method, path, **kwargs):
        response = self.http.request(method, '/bridge/v1' + path, **kwargs)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f'Source HTTP request failed: {response.status_code}')
        return response

    def create(self, payload):
        task = self.request('POST', '/tasks', json=payload).json()
        replay = self.request('POST', '/tasks', json=payload).json()
        if task['remote_task_id'] != replay['remote_task_id']:
            raise RuntimeError('Source replay changed task identity')
        return task

    def task(self, task_id):
        return self.request('GET', '/tasks/' + task_id).json()

    def download_and_ack(self, task_id):
        task = self.task(task_id)
        receipt_id = task['receipt_id']
        receipt = self.request('GET', '/receipts/' + receipt_id).json()
        response = self.request('GET', '/artifacts/' + receipt['artifact_id'] + '/content')
        content = response.content
        digest = hashlib.sha256(content).hexdigest()
        if digest != receipt['original_sha256'] or response.headers['content-type'] != 'image/png':
            raise RuntimeError('Source artifact verification failed')
        ack = {'client_receipt_id': 'fixture-receipt', 'decision': 'accepted',
               'reason_code': 'fixture_verified', 'original_hash': digest}
        first = self.request('POST', '/receipts/' + receipt_id + '/ack', json=ack).json()
        replay = self.request('POST', '/receipts/' + receipt_id + '/ack', json=ack).json()
        view = self.request('GET', '/receipts/' + receipt_id).json()
        if first != replay or view['delivery_ack'] != 'accepted':
            raise RuntimeError('Source acknowledgement replay failed')
        return content, digest

    def close(self):
        self.http.close()


class SourceServer:
    def __init__(self, store, scope):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.thread = self.client = None
        self.started = False
        try:
            self.socket.bind(('127.0.0.1', 0))
            self.port = self.socket.getsockname()[1]
            token = secrets.token_urlsafe(32)
            app = create_app(store=store, source_tokens={token: scope})
            self.server = uvicorn.Server(uvicorn.Config(
                app, host='127.0.0.1', port=self.port, access_log=False,
                log_config=None, log_level='critical', loop='asyncio',
                http='h11', lifespan='off', ws='none', timeout_graceful_shutdown=5))
            self.thread = threading.Thread(target=self.server.run,
                                           kwargs={'sockets': [self.socket]}, daemon=True)
            self.thread.start()
            self.started = True
            deadline = time.monotonic() + 10
            while not self.server.started:
                if not self.thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('Source HTTP server did not start')
                time.sleep(.01)
            self.client = SourceClient(self.port, token)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.client:
            self.client.close()
        if self.started:
            self.server.should_exit = True
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError('Source HTTP server did not stop')
        self.socket.close()
