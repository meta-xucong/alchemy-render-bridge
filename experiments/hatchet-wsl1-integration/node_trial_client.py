"""TEST ONLY native node client. No BridgeStore/database/storage access."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from render_bridge.fake import fixture_png


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate response field')
        value[key] = item
    return value


def invalid_constant(_value):
    raise ValueError('Non-finite response value')


class NodeClient:
    def __init__(self, config, journal):
        self.port = config['port']
        self.token = config['token']
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Invalid loopback port')
        if not isinstance(self.token, str) or len(self.token) != 43 or not self.token.isascii() or not all(c.isalnum() or c in '-_' for c in self.token):
            raise ValueError('Invalid temporary token')
        self.journal = Path(journal)
        self.prompt_digest = None

    def request(self, key, action, content=b'{}'):
        if key not in ('happy', 'unknown', 'blocked') or action not in ('start', 'artifact', 'commit', 'unknown'):
            raise ValueError('Invalid fixture operation')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('POST', f'/test-only/node/v1/{key}/{action}', body=content,
                               headers={'Authorization': 'Bearer ' + self.token,
                                        'Content-Type': 'application/octet-stream'})
            response = connection.getresponse()
            raw = response.read(16385)
            if len(raw) > 16384:
                raise RuntimeError('Oversized server response')
            value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            if response.status == 409 and value == {'error': 'physical_slot_busy'}:
                return {'outcome': 'physical_slot_busy'}
            if response.status != 200:
                raise RuntimeError('Node protocol request failed')
            return value
        finally:
            connection.close()

    def run(self, key):
        grant = self.request(key, 'start')
        if grant == {'outcome': 'physical_slot_busy'}:
            return grant
        expected_request = {'prompt': '合成图片\nSynthetic fixture only',
                            'parameters': {'width': 32, 'height': 32}, 'references': []}
        if (not isinstance(grant, dict) or set(grant) != {'may_execute', 'request'}
                or type(grant['may_execute']) is not bool or grant['request'] != expected_request
                or type(grant['request']['prompt']) is not str
                or type(grant['request']['parameters']['width']) is not int
                or type(grant['request']['parameters']['height']) is not int
                or type(grant['request']['references']) is not list):
            raise RuntimeError('Invalid start grant or frozen request')
        self.prompt_digest = hashlib.sha256(grant['request']['prompt'].encode('utf-8')).hexdigest()
        if grant['may_execute'] is False:
            return {'outcome': 'duplicate_suppressed'}
        if key == 'unknown':
            if self.request(key, 'unknown') != {'marked_unknown': True}:
                raise RuntimeError('Unknown transition not confirmed')
            return {'outcome': 'execution_unknown'}
        if key != 'happy':
            raise RuntimeError('Unexpected grant for blocked fixture')
        # Node-owned fsynced execution journal, unrelated to server-owned files.
        with self.journal.open('a', encoding='utf-8') as stream:
            stream.write(key + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        parameters = grant['request']['parameters']
        content = fixture_png(parameters['width'], parameters['height'])
        digest = hashlib.sha256(content).hexdigest()
        if self.request(key, 'artifact', content) != {'uploaded': True, 'sha256': digest}:
            raise RuntimeError('Upload verification failed')
        if self.request(key, 'commit') != {'committed': True, 'sha256': digest}:
            raise RuntimeError('Commit verification failed')
        return {'outcome': 'artifact_committed'}
