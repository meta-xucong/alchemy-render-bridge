"""Versioned Codex stdio building blocks; real rendering remains locally gated.

The installed codex-cli 0.163.0 generated schemas on 2026-10-20 confirm
item/completed params {threadId, turnId, item}; imageGeneration has savedPath,
status, result and id. A schema is NOT proof of account/model image capability.
Execution is disabled until explicit local version/capability acceptance.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence

from .base import AttemptFiles, ExecutionRequest, ExecutionResult, MAX_ARTIFACT_BYTES

PINNED_CLI_VERSION = 'codex-cli 0.163.0'
MAX_LINE = 1_048_576


def probe_codex(executable: str = 'codex') -> dict:
    """Only version/help in a fresh empty home. Never inspect saved credentials."""
    with tempfile.TemporaryDirectory(prefix='bridge-codex-probe-') as directory:
        env = dict(os.environ, CODEX_HOME=directory)
        try:
            version = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                     encoding='utf-8', timeout=10, env=env, check=False)
            help_result = subprocess.run([executable, 'app-server', '--help'], capture_output=True,
                                         text=True, encoding='utf-8', timeout=10, env=env, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {'status': 'needs_attention', 'code': 'codex_unavailable', 'image_generation_verified': False}
        value = version.stdout.strip()
        supported = version.returncode == 0 and value == PINNED_CLI_VERSION and help_result.returncode == 0
        return {'status': 'needs_attention',
                'code': 'local_image_acceptance_required' if supported else 'codex_version_not_validated',
                'cli_version': value[:120], 'schema_version_matches': supported,
                'stdio_advertised': '--stdio' in help_result.stdout,
                'image_generation_verified': False}


class StdioSession:
    """Bounded JSON-line subprocess transport, with no shell or auto-approval.

    argv is trusted local configuration, never a source API request. The caller
    controls which methods are sent. Tests use a fixed synthetic Python process;
    the actual `codex app-server --stdio` transport requires later local approval.
    Closing the subprocess does not prove upstream execution was cancelled.
    """
    def __init__(self, argv: Sequence[str], cwd: Path, *, timeout: float = 10):
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError('Expected explicit argument vector')
        self.timeout = timeout
        self.sequence = 0
        self.events = []
        self.messages = queue.Queue(maxsize=256)
        self.closed = threading.Event()
        self.process = subprocess.Popen(list(argv), cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding='utf-8', bufsize=1, shell=False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                raw = self.process.stdout.readline(MAX_LINE + 1)
                if not raw:
                    message = EOFError('Subprocess ended')
                elif len(raw) > MAX_LINE:
                    message = ValueError('Subprocess message exceeds limit')
                else:
                    message = json.loads(raw)
                try:
                    self.messages.put(message, timeout=.5)
                except queue.Full:
                    self.closed.set()
                    return
                if isinstance(message, Exception):
                    return
        except Exception:
            try:
                self.messages.put_nowait(ValueError('Invalid subprocess response'))
            except queue.Full:
                pass

    def send(self, message: dict):
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False)
        if len(raw) > MAX_LINE:
            raise ValueError('Subprocess request exceeds limit')
        self.process.stdin.write(raw + '\n')
        self.process.stdin.flush()

    def receive(self, *, timeout: float | None = None) -> dict:
        try:
            value = self.messages.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty:
            raise TimeoutError('No subprocess response') from None
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, dict):
            raise ValueError('Expected JSON object')
        if 'method' in value and 'id' in value:
            # A request for approval/input/tool execution belongs to a human.
            # Do not respond with approval or invoke the requested tool.
            raise PermissionError('Codex requested user attention')
        return value

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Subprocess request deadline exceeded')
            value = self.receive(timeout=remaining)
            if value.get('id') == request_id and 'method' not in value:
                if 'error' in value or 'result' not in value:
                    raise RuntimeError('Codex protocol request failed')
                return value['result']
            if 'method' not in value or len(self.events) >= 256:
                raise ValueError('Unexpected or excessive subprocess events')
            self.events.append(value)

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()
        self.reader.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@dataclass(frozen=True)
class CodexConfig:
    """Trusted local configuration, never populated from an incoming task.

    Acceptance flags attest a local account/model and tool-policy smoke test;
    the schema alone cannot establish either capability.
    """
    enabled: bool = False
    executable: str = 'codex'
    model: str = ''
    image_capability_verified: bool = False
    tool_policy_verified: bool = False
    timeout: float = 120


class CodexExecutor:
    def __init__(self, root: Path, config: CodexConfig | None = None):
        self.root = Path(root)
        self.config = config or CodexConfig()

    def preflight(self, request: ExecutionRequest) -> ExecutionResult | None:
        """Check local acceptance and version only; never starts a thread or turn."""
        config = self.config
        if not (config.enabled and config.model and config.image_capability_verified
                and config.tool_policy_verified):
            return ExecutionResult('needs_attention', 'codex_local_acceptance_required')
        probe = probe_codex(config.executable)
        if not (probe.get('schema_version_matches') and probe.get('stdio_advertised')):
            return ExecutionResult('needs_attention', 'codex_version_not_validated', details=probe)
        return None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        files = AttemptFiles(self.root, request, 'codex-cli')
        cached = files.cached()
        if cached:
            return cached
        completion = files.path / 'codex-completion.json'
        if completion.exists():
            saved = json.loads(completion.read_text(encoding='utf-8'))
            return self.collect_completed(request, **saved)
        config = self.config
        if not (config.enabled and config.model and config.image_capability_verified
                and config.tool_policy_verified):
            return ExecutionResult('needs_attention', 'codex_local_acceptance_required', details={
                'protocol_cli_version': PINNED_CLI_VERSION, 'image_generation_verified': False})
        if (files.path / 'started').exists():
            return ExecutionResult('execution_unknown', 'codex_prior_start_requires_reconciliation')
        probe = probe_codex(config.executable)
        if not (probe.get('schema_version_matches') and probe.get('stdio_advertised')):
            return ExecutionResult('needs_attention', 'codex_version_not_validated', details=probe)
        inputs = [{'type': 'text', 'text': request.prompt}]
        if request.parameters:
            # These are instructions, not invented image API parameters.
            inputs.append({'type': 'text', 'text': 'Requested rendering parameters: ' +
                           json.dumps(request.parameters, ensure_ascii=False, sort_keys=True)})
        for ref in request.references:
            path = Path(ref['path'])
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError('Reference original unavailable')
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref['hash']:
                raise ValueError('Reference original hash mismatch')
            inputs.append({'type': 'localImage', 'path': str(path.resolve())})
        if not files.begin():
            return ExecutionResult('execution_unknown', 'codex_prior_start_requires_reconciliation')
        cwd = str(files.path.resolve())
        try:
            with StdioSession([config.executable, 'app-server', '--stdio'],
                              files.path.resolve(), timeout=config.timeout) as session:
                session.request('initialize', {'clientInfo': {
                    'name': 'alchemy-render-bridge', 'version': '1'}})
                session.send({'method': 'initialized'})
                response = session.request('thread/start', {
                    'model': config.model, 'cwd': cwd,
                    'approvalPolicy': 'untrusted', 'approvalsReviewer': 'user',
                    'sandbox': 'workspace-write',
                    'developerInstructions': 'Generate exactly one image using the image generation tool. '
                        'Save the original in the current working directory. Do not substitute text, '
                        'shell rendering, browsing, or unrelated tools for image generation.'})
                thread_id = response['thread']['id']
                if not isinstance(thread_id, str) or not thread_id:
                    raise ValueError('Missing Codex thread identity')
                files.write_exclusive(files.path / 'codex-thread.json',
                                      json.dumps({'thread_id': thread_id}).encode())
                response = session.request('turn/start', {
                    'threadId': thread_id, 'input': inputs, 'cwd': cwd,
                    'approvalPolicy': 'untrusted', 'approvalsReviewer': 'user',
                    'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [cwd],
                        'networkAccess': False, 'excludeSlashTmp': True,
                        'excludeTmpdirEnvVar': True}})
                turn_id = response['turn']['id']
                if not isinstance(turn_id, str) or not turn_id:
                    raise ValueError('Missing Codex turn identity')
                files.write_exclusive(files.path / 'codex-turn.json',
                                      json.dumps({'thread_id': thread_id, 'turn_id': turn_id}).encode())
                events = list(session.events)
                deadline = time.monotonic() + config.timeout
                def ended():
                    return any(event.get('method') == 'turn/completed' and
                        event.get('params', {}).get('threadId') == thread_id and
                        event.get('params', {}).get('turn', {}).get('id') == turn_id
                        for event in events)
                while not ended():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Codex turn deadline exceeded')
                    event = session.receive(timeout=remaining)
                    if 'method' not in event or len(events) >= 256:
                        raise ValueError('Unexpected or excessive Codex events')
                    events.append(event)
                evidence = {'thread_id': thread_id, 'turn_id': turn_id, 'events': events}
                files.write_exclusive(completion, json.dumps(evidence, ensure_ascii=False).encode())
                return self.collect_completed(request, **evidence)
        except PermissionError:
            return ExecutionResult('needs_attention', 'codex_requested_user_attention')
        except (OSError, EOFError, TimeoutError, RuntimeError, ValueError, KeyError, TypeError):
            # A request may already have reached the server. Never regenerate.
            return ExecutionResult('execution_unknown', 'codex_execution_requires_reconciliation')

    def collect_completed(self, request: ExecutionRequest, *, thread_id: str,
                          turn_id: str, events: list[dict]) -> ExecutionResult:
        """Collect an already executed, precisely bound event stream.

        This does not start/retry a turn or grant the Agent permission to upload.
        Caller must supply IDs it persisted from that attempt's start responses.
        Plain text, `result` prose, and directory mtime are never image evidence.
        """
        files = AttemptFiles(self.root, request, 'codex-cli')
        if not thread_id or not turn_id:
            raise ValueError('Persisted thread and turn identity required')
        candidates = {}
        completed = False
        for event in events:
            params = event.get('params', {})
            if params.get('threadId') != thread_id:
                continue
            if event.get('method') == 'turn/completed':
                turn = params.get('turn', {})
                if turn.get('id') == turn_id:
                    completed = turn.get('status') == 'completed'
            if event.get('method') != 'item/completed' or params.get('turnId') != turn_id:
                continue
            item = params.get('item', {})
            if item.get('type') == 'imageGeneration' and item.get('status') == 'completed':
                identity = item.get('id')
                if not isinstance(identity, str) or not identity:
                    continue
                if identity in candidates and candidates[identity] != item.get('savedPath'):
                    raise ValueError('Conflicting image item identity')
                candidates[identity] = item.get('savedPath')
        if not completed:
            return ExecutionResult('execution_unknown', 'codex_turn_not_confirmed_complete')
        if len(candidates) != 1:
            return ExecutionResult('needs_attention', 'codex_single_image_event_required')
        saved = next(iter(candidates.values()))
        if not isinstance(saved, str) or not Path(saved).is_absolute():
            return ExecutionResult('needs_attention', 'codex_original_path_missing')
        path = Path(saved)
        resolved = path.resolve()
        if not resolved.is_relative_to(files.path.resolve()) or path.is_symlink():
            raise ValueError('Image event path is outside this attempt')
        if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError('Image event original is unavailable or too large')
        result = files.save(path.read_bytes(), 'codex_image_event_locally_unverified')
        return ExecutionResult(result.status, result.code, result.artifact,
                               {'thread_id': thread_id, 'turn_id': turn_id,
                                'image_item_id': next(iter(candidates))})
