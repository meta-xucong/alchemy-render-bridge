"""TEST ONLY loopback node protocol. Not a production Agent API or scheduler.

A dedicated server subprocess owns this adapter, ledger and blobs. Fixed pre-created claims are
mapped to opaque fixture keys. No scope, claim, upload ID or filesystem path can
be selected by the node. Source receipt acceptance is a server-side domain
simulator, explicitly not additional source HTTP acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from render_bridge.domain import BridgeStore, DomainError, SourceScope

PREFIX = '/test-only/node/v1/'
FROZEN_PROMPT = '合成图片\nSynthetic fixture only'
MAX_BODY = 16384  # fixed 32x32 PNG fixture only


class NodeTrial:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bridge-node-trial-')
        self.root = Path(self.temp.name)
        try:
            self.store = BridgeStore(self.root / 'ledger.db', self.root / 'objects')
            self.scope = SourceScope('synthetic-source', 'synthetic-workspace')
            self.token = secrets.token_urlsafe(32)
            self.bindings = {}
            self.uploads = {}
            self.receipts = {}
            self.grants = {key: 0 for key in ('happy', 'unknown', 'blocked')}
            self.server = self.thread = None
            self.thread_started = False
            for key in self.grants:
                task = self.store.create_task(self.scope, {
                    'schema_version': '1', 'origin_key': key, 'binding_id': 'synthetic-binding',
                    'operation': 'image.generate', 'execution_profile_id': 'fake-image',
                    'execution_profile_revision': '1', 'prompt': FROZEN_PROMPT,
                    'parameters': {'width': 32, 'height': 32}, 'references': [],
                    'output_slot': {'slot_id': 'primary', 'count': 1}, 'routing': 'bridge_only',
                    'deadline': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()})
                task_id = task['remote_task_id']
                claim = self.store.claim_task(self.scope, task_id, node_id='native-windows-fixture',
                                              executor_instance='one-trial', lease_seconds=900)
                self.bindings[key] = (task_id, claim)
        except Exception:
            try:
                if hasattr(self, "store"):
                    self.store.close()
            finally:
                self.temp.cleanup()
            raise

    def operation(self, key, action, body):
        if key not in self.bindings:
            raise DomainError('not_found', 404)
        task_id, claim = self.bindings[key]
        claim_id = claim['claim_id']
        if action in ('start', 'unknown', 'commit') and body != b'{}':
            raise DomainError('invalid_request', 400)
        if action == 'start':
            value = self.store.start_execution(self.scope, task_id, claim_id)
            self.grants[key] += int(value['may_execute'])
            request = self.store.get_task(self.scope, task_id)['request']
            return {'may_execute': value['may_execute'], 'request': {
                name: request[name] for name in ('prompt', 'parameters', 'references')}}
        if action == 'unknown':
            self.store.mark_execution_unknown(self.scope, task_id, claim_id)
            return {'marked_unknown': True}
        if action == 'artifact':
            metadata = {'attempt_id': claim['attempt_id'], 'slot_id': 'primary',
                        'original_sha256': hashlib.sha256(body).hexdigest(),
                        'size': len(body), 'mime_type': 'image/png'}
            upload = self.store.create_upload(self.scope, task_id, claim_id, metadata)
            self.store.put_upload_content(self.scope, upload['upload_id'], claim_id, body)
            self.uploads[key] = upload['upload_id']
            return {'uploaded': True, 'sha256': metadata['original_sha256']}
        if action == 'commit':
            if key not in self.uploads:
                raise DomainError('upload_not_ready', 409)
            receipt = self.store.commit_upload(self.scope, self.uploads[key], claim_id)
            self.receipts[key] = receipt
            return {'committed': True, 'sha256': receipt['original_sha256']}
        raise DomainError('not_found', 404)

    def start(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            # One bounded request per connection; no logging of credentials/body.
            protocol_version = 'HTTP/1.0'
            def log_message(self, *_args):
                pass
            def setup(self):
                super().setup()
                self.connection.settimeout(5)
            def do_POST(self):
                try:
                    auth = self.headers.get_all('Authorization', [])
                    if len(auth) != 1 or not hmac.compare_digest(auth[0], 'Bearer ' + owner.token):
                        raise DomainError('unauthorized', 401)
                    if self.headers.get_all('Transfer-Encoding'):
                        raise DomainError('invalid_framing', 400)
                    lengths = self.headers.get_all('Content-Length', [])
                    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                        raise DomainError('invalid_framing', 400)
                    length = int(lengths[0])
                    if length > MAX_BODY:
                        raise DomainError('payload_too_large', 413)
                    if not self.path.startswith(PREFIX):
                        raise DomainError('not_found', 404)
                    parts = self.path[len(PREFIX):].split('/')
                    if len(parts) != 2:
                        raise DomainError('not_found', 404)
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise DomainError('invalid_framing', 400)
                    value, status = owner.operation(*parts, body), 200
                except DomainError as exc:
                    value, status = {'error': exc.code}, exc.status
                except Exception:
                    value, status = {'error': 'request_failed'}, 500
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(raw)
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.thread_started = True
        return {'port': self.port, 'token': self.token}

    def verify(self):
        """Source simulator verifies immutable bytes and acknowledges receipt."""
        receipt = self.receipts['happy']
        content, mime = self.store.get_artifact_content(self.scope, receipt['artifact_id'])
        digest = hashlib.sha256(content).hexdigest()
        if digest != receipt['original_sha256'] or mime != 'image/png':
            raise RuntimeError('Artifact verification failed')
        ack = {'client_receipt_id': 'fixture-receipt', 'decision': 'accepted',
               'reason_code': 'fixture_verified', 'original_hash': digest}
        self.store.ack_receipt(self.scope, receipt['receipt_id'], ack)
        view = self.store.get_receipt(self.scope, receipt['receipt_id'])
        unknown = self.store.get_task(self.scope, self.bindings['unknown'][0])
        if (self.grants != {'happy': 1, 'unknown': 1, 'blocked': 0}
                or view['delivery_ack'] != 'accepted' or unknown['state'] != 'execution_unknown'
                or set(self.receipts) != {'happy'}):
            raise RuntimeError('Ledger gate failed')
        return {'status': 'passed', 'start_grants': dict(self.grants), 'artifact_sha256': digest,
                'artifact_bytes': len(content), 'receipt_ack': 'accepted',
                'unknown_retains_slot': True, 'source_transport': 'domain_simulator',
                'node_transport': 'test_only_loopback_http'}

    def close(self):
        errors = []
        if self.server:
            actions = [self.server.server_close]
            if self.thread_started:
                actions = [self.server.shutdown, *actions, lambda: self.thread.join(timeout=10)]
            for action in actions:
                try:
                    action()
                except Exception:
                    errors.append('server_cleanup_failed')
            if self.thread_started and self.thread.is_alive():
                errors.append('server_thread_alive')
        try:
            self.store.close()
        except Exception:
            errors.append('store_cleanup_failed')
        try:
            self.temp.cleanup()
        except Exception:
            errors.append('temporary_cleanup_failed')
        if self.root.exists():
            errors.append('temporary_files_survived')
        if errors:
            raise RuntimeError('Node trial cleanup incomplete')


def main():
    """Private controller protocol. Never publish raw child stdout."""
    trial = None
    status = 'blocked'
    try:
        if sys.stdin.readline().strip() != 'start':
            raise RuntimeError('Missing startup command')
        trial = NodeTrial()
        config = trial.start()
        print(json.dumps({'event': 'node_ready', **config}), flush=True)
        if sys.stdin.readline().strip() != 'verify':
            raise RuntimeError('Missing verification command')
        evidence = trial.verify()
        print(json.dumps({'event': 'node_verified', **evidence}), flush=True)
        if sys.stdin.readline().strip() != 'stop':
            raise RuntimeError('Missing stop command')
        status = 'passed'
    except Exception:
        status = 'blocked'
    finally:
        removed = False
        if trial is not None:
            try:
                trial.close()
                removed = True
            except Exception:
                status = 'blocked'
        print(json.dumps({'event': 'node_stopped', 'status': status,
                          'temporary_directory_removed': removed}), flush=True)
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
