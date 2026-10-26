"""Versioned Codex stdio building blocks; real rendering remains locally gated.

Official codex-cli 0.157.1 and 0.163.0 tags have statically verified app-server
wire parity for this adapter. A schema is NOT proof of account/model image
capability. Execution is disabled until explicit local version/capability
acceptance for the exact installed version.
"""
from __future__ import annotations

import json
import hashlib
import math
import stat
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence

from render_bridge.image_options import codex_imagegen_arguments, requested_output_mime, validate_image_request, uses_codex_adapter
from .base import AttemptFiles, ExecutionRequest, ExecutionResult, MAX_ARTIFACT_BYTES, inspect_image

SUPPORTED_CLI_VERSIONS = frozenset({'codex-cli 0.157.1', 'codex-cli 0.163.0'})
# Native imageGeneration events may include an encoded original. Keep a hard
# bound while allowing base64 expansion of the largest accepted artifact.
MAX_LINE = MAX_ARTIFACT_BYTES * 2
MAX_SESSION_BYTES = MAX_ARTIFACT_BYTES * 3
MAX_REQUEST_BYTES = 1_048_576
MAX_QUEUED_MESSAGES = 8
MAX_JSON_NESTING = 64
MAX_JSON_STRUCTURAL_TOKENS = 100_000
MAX_SESSION_STRUCTURAL_TOKENS = 100_000
MAX_ROLLOUT_LINES = 10_000
MAX_CAPTURED_LIFECYCLE_EVENTS = 256


def probe_codex(executable: str = 'codex') -> dict:
    """Only version/help in a fresh empty home. Never inspect saved credentials."""
    with tempfile.TemporaryDirectory(prefix='bridge-codex-probe-') as directory:
        # Do not inherit account credentials or the user's real home. These
        # commands only inspect the installed CLI, not its saved account state.
        env = {key: os.environ[key] for key in
               ('PATH', 'PATHEXT', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'COMSPEC',
                'TEMP', 'TMP', 'SystemDrive') if key in os.environ}
        env.update(CODEX_HOME=directory, HOME=directory, USERPROFILE=directory)
        try:
            version = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                     encoding='utf-8', timeout=10, env=env, check=False)
            help_result = subprocess.run([executable, 'app-server', '--help'], capture_output=True,
                                         text=True, encoding='utf-8', timeout=10, env=env, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = 'tool_missing' if isinstance(exc, FileNotFoundError) else 'probe_failed'
            return {'status': 'needs_attention', 'code': 'codex_unavailable',
                    'health_reason': reason, 'image_generation_verified': False}
        value = version.stdout.strip()
        supported = (version.returncode == 0 and value in SUPPORTED_CLI_VERSIONS
                     and help_result.returncode == 0)
        return {'status': 'needs_attention',
                'code': 'local_image_acceptance_required' if supported else 'codex_version_not_validated',
                'health_reason': 'probe_failed' if version.returncode or help_result.returncode else 'ready' if supported else 'version_unsupported',
                'cli_version': value[:120], 'schema_version_matches': supported,
                'stdio_advertised': '--stdio' in help_result.stdout,
                'image_generation_verified': False}


def _image_activity_markers(value):
    """Find JSON objects that identify native image-generation activity."""
    markers = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key in ('type', 'kind', 'tool', 'tool_name', 'toolName'):
                marker = current.get(key)
                if not isinstance(marker, str):
                    continue
                normalized = marker.casefold()
                if (normalized in ('imagegeneration', 'image_generation', 'image-generation')
                        or normalized.startswith('image_gen.')
                        or normalized.startswith('image_generation.')):
                    markers.append(current)
                    break
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return markers


def health_reason_from_codex_error(info):
    """Allowlisted rust-v0.163.0 structured codes; never classify error prose."""
    if info == 'unauthorized':
        return 'auth_unavailable'
    if info == 'usageLimitExceeded':
        return 'quota_exhausted'
    if isinstance(info, str) and info in {'rateLimitExceeded', 'serverOverloaded', 'internalServerError'}:
        return 'probe_failed'
    if isinstance(info, dict) and len(info) == 1:
        variant = next(iter(info))
        if variant in {'httpConnectionFailed', 'responseStreamConnectionFailed',
                       'responseStreamDisconnected', 'responseTooManyFailedAttempts'}:
            details = info[variant]
            if isinstance(details, dict):
                status = details.get('httpStatusCode')
                if type(status) is int and status == 401:
                    return 'auth_unavailable'
                if status is None or (type(status) is int and (status == 429 or 500 <= status <= 599)):
                    return 'probe_failed'
    return None


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
        self.event_counts = {'item/completed': 0, 'turn/completed': 0,
                             'item/commandExecution/outputDelta': 0,
                             'other_notification': 0}
        self.ignored_notifications = 0
        self.messages = queue.Queue(maxsize=MAX_QUEUED_MESSAGES)
        self.bytes_read = 0
        self.structural_tokens = 0
        self.closed = threading.Event()
        self.process = subprocess.Popen(list(argv), cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        bufsize=0, shell=False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                remaining = MAX_SESSION_BYTES - self.bytes_read
                if remaining <= 0:
                    message = ValueError('Subprocess session exceeds limit')
                else:
                    read_limit = min(MAX_LINE, remaining)
                    raw = self.process.stdout.readline(read_limit)
                    if not raw:
                        message = EOFError('Subprocess ended')
                    elif not raw.endswith(b'\n'):
                        reason = ('Subprocess session exceeds limit'
                                  if read_limit == remaining
                                  else 'Subprocess message exceeds limit')
                        message = ValueError(reason)
                    else:
                        self.bytes_read += len(raw)
                        tokens = self._json_structure_token_count(raw)
                        if (tokens is None or self.structural_tokens + tokens
                                > MAX_SESSION_STRUCTURAL_TOKENS):
                            message = ValueError('Subprocess JSON complexity exceeds limit')
                        else:
                            self.structural_tokens += tokens
                            message = json.loads(raw.decode('utf-8'))
                while not self.closed.is_set():
                    try:
                        self.messages.put(message, timeout=.1)
                        break
                    except queue.Full:
                        # Apply backpressure to the subprocess pipe instead of
                        # converting a harmless event burst into execution loss.
                        continue
                else:
                    return
                if isinstance(message, Exception):
                    return
        except Exception:
            try:
                self.messages.put_nowait(ValueError('Invalid subprocess response'))
            except queue.Full:
                pass

    @staticmethod
    def _json_structure_token_count(raw: bytes) -> int | None:
        depth = 0
        structural_tokens = 0
        in_string = False
        escaped = False
        for byte in raw:
            if in_string:
                if escaped:
                    escaped = False
                elif byte == ord('\\'):
                    escaped = True
                elif byte == ord('"'):
                    in_string = False
                continue
            if byte == ord('"'):
                in_string = True
            elif byte in (ord('{'), ord('[')):
                depth += 1
                structural_tokens += 1
                if depth > MAX_JSON_NESTING:
                    return None
            elif byte in (ord('}'), ord(']')):
                depth -= 1
                structural_tokens += 1
            elif byte in (ord(','), ord(':')):
                structural_tokens += 1
            if structural_tokens > MAX_JSON_STRUCTURAL_TOKENS:
                return None
        return structural_tokens

    def send(self, message: dict):
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('Subprocess request exceeds limit')
        self.process.stdin.write(raw + b'\n')
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
            self.capture_notification(value)

    def capture_notification(self, value: dict) -> None:
        method = value.get('method')
        if not isinstance(method, str):
            raise ValueError('Unexpected subprocess message')
        counter = method if method in self.event_counts else 'other_notification'
        self.event_counts[counter] += 1
        params = value.get('params')
        item = params.get('item') if isinstance(params, dict) else None
        image_item = (method == 'item/completed' and isinstance(item, dict)
                      and (item.get('type') == 'imageGeneration'
                           or (item.get('type') == 'Extension'
                               and item.get('kind') == 'image_gen.generation')))
        if method == 'turn/completed' or image_item:
            if len(self.events) >= MAX_CAPTURED_LIFECYCLE_EVENTS:
                raise ValueError('Too many Codex lifecycle events')
            self.events.append(value)
        else:
            # Stream deltas and unrelated notifications are deliberately not
            # retained: their count is enough for diagnostics; payloads may
            # contain prompt-adjacent text and are not execution proof.
            self.ignored_notifications += 1

    def diagnostic_summary(self) -> dict:
        return {'event_counts': dict(self.event_counts),
                'captured_lifecycle_events': len(self.events),
                'ignored_notifications': self.ignored_notifications,
                'bytes_read': self.bytes_read,
                'structural_tokens': self.structural_tokens,
                'process_exit_code': self.process.poll()}

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

    Acceptance flags are operator declarations, not measurements made by Bridge.
    Neither this config nor the doctor establishes real account/model capability.
    """
    enabled: bool = False
    executable: str = 'codex'
    model: str = ''
    accepted_cli_version: str = ''
    approval_policy: str = ''
    image_capability_verified: bool = False
    tool_policy_verified: bool = False
    timeout: float = 120

    def __post_init__(self):
        for name in ('enabled', 'image_capability_verified', 'tool_policy_verified'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('Codex acceptance flags must be JSON booleans')
        if not isinstance(self.executable, str) or not self.executable.strip() or '\x00' in self.executable:
            raise ValueError('Codex executable must be a nonempty string')
        if (not isinstance(self.accepted_cli_version, str)
                or (self.accepted_cli_version and self.accepted_cli_version not in SUPPORTED_CLI_VERSIONS)):
            raise ValueError('accepted_cli_version must be empty or an exact supported Codex version')
        if self.enabled and self.accepted_cli_version not in SUPPORTED_CLI_VERSIONS:
            raise ValueError('Enabled Codex requires an explicitly accepted supported CLI version')
        if self.approval_policy not in ('', 'on-request'):
            raise ValueError('Explicit supported Codex approval_policy must be on-request')
        if not isinstance(self.model, str):
            raise ValueError('Codex model must be a string')
        if (type(self.timeout) not in (int, float) or not math.isfinite(self.timeout)
                or self.timeout <= 0):
            raise ValueError('Codex timeout must be a positive finite number')


class CodexExecutor:
    def __init__(self, root: Path, config: CodexConfig | None = None):
        self.root = Path(root)
        self.config = config or CodexConfig()

    def preflight(self, request: ExecutionRequest) -> ExecutionResult | None:
        """Check local acceptance and version only; never starts a thread or turn."""
        validate_image_request(request.parameters, request.references, 'codex-image')
        config = self.config
        if not (config.enabled and config.model and config.approval_policy == 'on-request' and config.image_capability_verified
                and config.tool_policy_verified):
            return ExecutionResult('needs_attention', 'codex_local_acceptance_required')
        probe = probe_codex(config.executable)
        if not (probe.get('cli_version') == config.accepted_cli_version
                and probe.get('schema_version_matches') and probe.get('stdio_advertised')):
            return ExecutionResult('needs_attention', 'codex_version_not_validated', details=probe)
        return None

    def _terminal_failure(self, request, files, thread_id, turn_id, events, cli_version=None):
        # Only known pre-generation account failures with a complete bound turn
        # and no image tool activity establish physical completion. A transport
        # error, missing items, or partial image activity remains unknown.
        matched = [event.get('params', {}).get('turn', {}) for event in events
                   if event.get('method') == 'turn/completed'
                   and event.get('params', {}).get('threadId') == thread_id
                   and event.get('params', {}).get('turn', {}).get('id') == turn_id]
        if any((files.path / name).exists() for name in ('artifact.original', 'artifact.json')):
            return None
        if len(matched) != 1:
            return None
        turn = matched[0]
        error = turn.get('error')
        items = turn.get('items')
        if (turn.get('status') != 'failed' or not isinstance(error, dict)
                or error.get('codexErrorInfo') not in ('unauthorized', 'usageLimitExceeded')
                or not isinstance(items, list) or any(not isinstance(item, dict) for item in items)):
            return None
        if any(item.get('type') == 'imageGeneration' for item in items):
            return None
        for event in events:
            params = event.get('params', {})
            if (params.get('threadId') == thread_id and params.get('turnId') == turn_id
                    and ((isinstance(params.get('item'), dict)
                          and params['item'].get('type') == 'imageGeneration')
                         or event.get('method', '').startswith('item/imageGeneration/'))):
                return None
        raw = json.dumps({'thread_id': thread_id, 'turn_id': turn_id, 'events': events},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        path = files.path / 'codex-terminal-failure.json'
        try:
            files.write_exclusive(path, raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('Terminal evidence changed')
        reason = health_reason_from_codex_error(error['codexErrorInfo'])
        return ExecutionResult('terminal_failure', 'codex_terminal_account_failure', details={
            'profile_health_reason': reason,
            'terminal_failure': {'schema_version': '1', 'thread_id': thread_id,
                'turn_id': turn_id, 'reason_code': reason,
                'completion_sha256': hashlib.sha256(raw).hexdigest()},
            **({'cli_version': cli_version} if cli_version in SUPPORTED_CLI_VERSIONS else {})})

    def recover_terminal_failure(self, request):
        files = AttemptFiles(self.root, request, 'codex-cli')
        for name in ('codex-terminal-failure.json', 'codex-completion.json'):
            path = files.path / name
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                terminal_evidence = {key: saved[key] for key in
                    ('thread_id', 'turn_id', 'events', 'cli_version') if key in saved}
                return self._terminal_failure(request, files, **terminal_evidence)
        return None

    def recover_completed(self, request: ExecutionRequest) -> ExecutionResult | None:
        """Recover completed local evidence only; never probe or launch a CLI."""
        validate_image_request(request.parameters, request.references, 'codex-image')
        files = AttemptFiles(self.root, request, 'codex-cli')
        terminal = self.recover_terminal_failure(request)
        if terminal is not None:
            return terminal
        interrupted = files.path / 'codex-aborted-image.json'
        if interrupted.exists():
            try:
                evidence = json.loads(interrupted.read_text(encoding='utf-8'))
                return self.reconcile_interrupted_image(
                    request, Path(evidence['rollout_path']), _expected_evidence=evidence,
                    bridge_binding=evidence.get('bridge_binding'))
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                return ExecutionResult('execution_unknown', 'codex_interrupted_image_evidence_invalid')
        completed_rollout = files.path / 'codex-completed-rollout-image.json'
        if completed_rollout.exists():
            try:
                evidence = json.loads(completed_rollout.read_text(encoding='utf-8'))
                return self.reconcile_completed_rollout_image(
                    request, Path(evidence['rollout_path']), _expected_evidence=evidence,
                    bridge_binding=evidence.get('bridge_binding'))
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                return ExecutionResult('execution_unknown', 'codex_completed_rollout_evidence_invalid')
        cached = files.cached()
        if (cached is not None and cached.artifact is not None
                and cached.artifact.provenance in {
                    'codex_aborted_turn_review_only', 'codex_completed_rollout_reconciled'}):
            return ExecutionResult('execution_unknown',
                                   'codex_interrupted_image_evidence_missing')
        if cached:
            return cached
        completion = files.path / 'codex-completion.json'
        if completion.exists():
            saved = json.loads(completion.read_text(encoding='utf-8'))
            return self.collect_completed(request, **saved)
        return None

    def reconcile_interrupted_image(self, request: ExecutionRequest, rollout_path: Path,
                                    *, _expected_evidence: dict | None = None,
                                    bridge_binding: dict | None = None) -> ExecutionResult:
        return self._reconcile_terminal_image(
            request, rollout_path, terminal_event='turn_aborted', terminal_reason='interrupted',
            contract='codex-aborted-image-reconcile/v1', evidence_name='codex-aborted-image.json',
            provenance='codex_aborted_turn_review_only', review_only=True,
            _expected_evidence=_expected_evidence,
            bridge_binding=bridge_binding)

    def reconcile_completed_rollout_image(self, request: ExecutionRequest, rollout_path: Path,
                                          *, _expected_evidence: dict | None = None,
                                          bridge_binding: dict | None = None) -> ExecutionResult:
        """Recover one original from a durable completed turn after stream loss."""
        return self._reconcile_terminal_image(
            request, rollout_path, terminal_event='task_complete', terminal_reason=None,
            contract='codex-completed-rollout-reconcile/v1',
            evidence_name='codex-completed-rollout-image.json',
            provenance='codex_completed_rollout_reconciled', review_only=False,
            _expected_evidence=_expected_evidence,
            bridge_binding=bridge_binding)

    def _reconcile_terminal_image(self, request: ExecutionRequest, rollout_path: Path, *,
                                  terminal_event: str, terminal_reason: str | None,
                                  contract: str, evidence_name: str, provenance: str, review_only: bool,
                                  _expected_evidence: dict | None,
                                  bridge_binding: dict | None) -> ExecutionResult:
        """Accept one event-bound original only after its same-turn terminal event is durable.

        This path never starts, resumes, or retries a Codex turn. It is used
        only when the Agent lost its own event stream and must verify a saved
        local Codex rollout before uploading the existing original.
        """
        validate_image_request(request.parameters, request.references, 'codex-image')
        if (not isinstance(bridge_binding, dict)
                or not isinstance(bridge_binding.get('task_digest'), str)
                or len(bridge_binding['task_digest']) != 64
                or any(c not in '0123456789abcdef' for c in bridge_binding['task_digest'])
                or bridge_binding.get('output_slot') != {'count': 1, 'slot_id': 'primary'}
                or bridge_binding.get('profile_id') != 'codex-image'
                or not isinstance(bridge_binding.get('profile_revision'), str)
                or not bridge_binding['profile_revision']
                or not isinstance(bridge_binding.get('start_grant_id'), str)
                or not bridge_binding['start_grant_id']):
            return ExecutionResult('execution_unknown', 'codex_interrupted_image_bridge_binding_missing')
        files = AttemptFiles(self.root, request, 'codex-cli')
        if any((files.path / name).exists() or (files.path / name).is_symlink()
               for name in ('codex-completion.json', 'codex-terminal-failure.json')):
            return ExecutionResult('execution_unknown',
                                   'codex_interrupted_image_conflicting_terminal_evidence')
        thread_file = files.path / 'codex-thread.json'
        turn_file = files.path / 'codex-turn.json'
        if not (files.path / 'started').is_file() or not thread_file.is_file() or not turn_file.is_file():
            return ExecutionResult('execution_unknown', 'codex_interrupted_image_attempt_binding_missing')
        try:
            saved_thread = json.loads(thread_file.read_text(encoding='utf-8'))['thread_id']
            saved_turn = json.loads(turn_file.read_text(encoding='utf-8'))
            thread_id, turn_id = saved_turn['thread_id'], saved_turn['turn_id']
            if saved_thread != thread_id or not thread_id or not turn_id:
                raise ValueError('Persisted Codex thread/turn binding differs')
        except (OSError, ValueError, KeyError, TypeError):
            return ExecutionResult('execution_unknown', 'codex_interrupted_image_attempt_binding_invalid')
        try:
            rollout_path = Path(rollout_path)
            if rollout_path.is_symlink() or (hasattr(rollout_path, 'is_junction') and rollout_path.is_junction()):
                raise ValueError('Rollout path is linked')
            rollout = rollout_path.resolve(strict=True)
            if not rollout.is_file() or rollout.stat().st_size > MAX_SESSION_BYTES:
                raise ValueError('Rollout is not a bounded regular file')
            raw = rollout.read_bytes()
            if not raw or len(raw) > MAX_SESSION_BYTES or not raw.endswith(b'\n'):
                raise ValueError('Rollout exceeds its size limit')
            records = []
            def unique_object(pairs):
                value = {}
                for key, item_value in pairs:
                    if key in value:
                        raise ValueError('Duplicate rollout JSON key')
                    value[key] = item_value
                return value
            for line in raw.splitlines():
                if len(records) >= MAX_ROLLOUT_LINES or not line:
                    raise ValueError('Rollout record count or framing is invalid')
                record = json.loads(line, object_pairs_hook=unique_object)
                if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
                    raise ValueError('Rollout record schema is invalid')
                ordinal = record.get('ordinal')
                if type(ordinal) is not int or (records and ordinal <= records[-1]['ordinal']):
                    raise ValueError('Rollout event order is invalid')
                records.append(record)
            if not records or records[0].get('type') != 'session_meta':
                raise ValueError('Rollout session identity is missing')
            if records[0]['payload'].get('id') != thread_id:
                raise ValueError('Rollout belongs to another Codex thread')
            digest = hashlib.sha256(raw).hexdigest()
            events = [(index, row['type'], row['payload']) for index, row in enumerate(records)]
            for _, _, payload in events:
                if (payload.get('turn_id') == turn_id
                        and payload.get('thread_id') is not None
                        and payload.get('thread_id') != thread_id):
                    raise ValueError('Lifecycle event belongs to another Codex thread')
            terminal = [(i, p) for i, t, p in events
                        if t == 'event_msg' and p.get('type') == terminal_event
                        and p.get('turn_id') == turn_id]
            if len(terminal) != 1 or terminal[0][0] != len(records) - 1:
                raise ValueError('Matching terminal turn event is required at end of rollout')
            if terminal_reason is not None and terminal[0][1].get('reason') != terminal_reason:
                raise ValueError('Terminal turn reason differs from the required recovery state')
            if not any(t == 'event_msg' and p.get('type') == 'task_started'
                       and p.get('turn_id') == turn_id for _, t, p in events):
                raise ValueError('Rollout does not contain the bound started turn')
            if not any(t == 'turn_context' and p.get('turn_id') == turn_id
                       for _, t, p in events):
                raise ValueError('Rollout does not contain the bound turn context')
            conflicting_terminal = 'task_complete' if terminal_event == 'turn_aborted' else 'turn_aborted'
            if any(t == 'event_msg' and p.get('type') in ('turn/completed', conflicting_terminal)
                   and p.get('turn_id') == turn_id for _, t, p in events):
                raise ValueError('Contradictory turn terminal event')
            image_items = []
            for index, record_type, payload in events:
                markers = _image_activity_markers(payload)
                if not markers:
                    continue
                event_thread = payload.get('thread_id')
                event_turn = payload.get('turn_id')
                if event_thread is not None and event_thread != thread_id:
                    continue
                if event_turn is not None and event_turn != turn_id:
                    continue
                if (record_type != 'event_msg' or event_thread != thread_id
                        or event_turn != turn_id):
                    raise ValueError('Image activity is not bound to the expected event/thread/turn')
                item = payload.get('item')
                if (not isinstance(item, dict) or len(markers) != 1 or markers[0] is not item):
                    raise ValueError('Unknown or malformed image-generation activity')
                image_items.append((index, payload.get('type'), item))
            if len(image_items) != 1:
                raise ValueError('Exactly one image-generation item is required')
            image_index, event_type, item = image_items[0]
            if (event_type != 'item_completed' or item.get('type') != 'Extension'
                    or item.get('kind') != 'image_gen.generation'
                    or item.get('status') != 'completed'
                    or not isinstance(item.get('id'), str) or not item['id']
                    or not isinstance(item.get('savedPath'), str)):
                raise ValueError('Image item is not a completed native generation')
            if image_index >= terminal[0][0]:
                raise ValueError('Image must complete before the aborted turn event')
            source = Path(item['savedPath'])
            if not source.is_absolute():
                raise ValueError('Event-bound original path must be absolute')
            resolved = source.resolve(strict=True)
            if (source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction())
                    or not self._is_codex_generated_image(resolved, thread_id)
                    or resolved.suffix.lower() not in ('.png', '.jpg', '.jpeg')
                    or resolved.stem != item['id']):
                raise ValueError('Event-bound original path is outside the exact Codex thread/item')
            attempt_root = files.path.resolve(strict=True)
            content = self._read_scoped_original(source, attempt_root, thread_id, resolved)
            verified = inspect_image(content, provenance=provenance,
                                     path=files.path / 'artifact.original')
            if ((requested := requested_output_mime(request.parameters, 'codex-image'))
                    and verified.mime_type != requested):
                return ExecutionResult('execution_unknown', 'codex_terminal_image_output_mismatch')
            evidence = {
                'schema_version': '1', 'contract': contract,
                'task_id': request.task_id, 'attempt_id': request.attempt_id,
                'request_sha256': request.digest, 'thread_id': thread_id, 'turn_id': turn_id,
                'image_item_id': item['id'], 'rollout_path': str(rollout),
                'rollout_sha256': digest, 'terminal_event': terminal_event,
                **({'terminal_reason': terminal_reason} if terminal_reason is not None else {}),
                'source_path': str(resolved),
                'original_sha256': verified.sha256, 'size': verified.size,
                'mime_type': verified.mime_type,
                'dimensions': {'width': verified.width, 'height': verified.height},
                'bridge_binding': bridge_binding,
                'review_only': review_only,
            }
            evidence_path = files.path / evidence_name
            if _expected_evidence is not None and _expected_evidence != evidence:
                raise ValueError('Persisted interrupted-image evidence changed')
            try:
                files.write_exclusive(evidence_path, json.dumps(
                    evidence, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            except FileExistsError:
                if json.loads(evidence_path.read_text(encoding='utf-8')) != evidence:
                    raise ValueError('Terminal-image evidence conflicts with persisted proof')
            artifact = files.save(content, provenance)
            if artifact.status != 'ready' or artifact.artifact is None:
                return artifact
            return ExecutionResult(artifact.status, artifact.code, artifact.artifact,
                                   {**artifact.details, 'review_only': review_only,
                                    'reconciliation': evidence})
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, RecursionError):
            return ExecutionResult('execution_unknown', 'codex_terminal_image_evidence_invalid')

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        recovered = self.recover_completed(request)
        if recovered is not None:
            return recovered
        files = AttemptFiles(self.root, request, 'codex-cli')
        completion = files.path / 'codex-completion.json'
        config = self.config
        if not (config.enabled and config.model and config.approval_policy == 'on-request' and config.image_capability_verified
                and config.tool_policy_verified):
            return ExecutionResult('needs_attention', 'codex_local_acceptance_required', details={
                'protocol_cli_versions': sorted(SUPPORTED_CLI_VERSIONS), 'image_generation_verified': False})
        if (files.path / 'started').exists():
            return ExecutionResult('execution_unknown', 'codex_prior_start_requires_reconciliation')
        probe = probe_codex(config.executable)
        if not (probe.get('cli_version') == config.accepted_cli_version
                and probe.get('schema_version_matches') and probe.get('stdio_advertised')):
            return ExecutionResult('needs_attention', 'codex_version_not_validated', details=probe)
        adapted = uses_codex_adapter(request.parameters, 'codex-image')
        native_options = codex_imagegen_arguments(request.parameters, probe['cli_version'])
        instructions = 'Generate exactly one image using the image generation tool. Save the original in the current working directory. Do not substitute text, shell rendering, browsing, or unrelated tools for image generation.'
        if native_options['transparent_background']:
            instructions += ' Invoke the image generation tool with transparent_background=true and preserve alpha transparency.'
        if adapted and request.references:
            reference_paths = [str(Path(ref['path']).resolve()) for ref in request.references]
            instructions += (' Invoke the image generation tool with referenced_image_paths set to '
                + json.dumps(reference_paths, ensure_ascii=False) + '. Do not use num_last_images_to_include.')
        inputs = [{'type': 'text', 'text': request.prompt}]
        if not adapted and request.parameters:
            inputs.append({'type': 'text', 'text': (
                'Requested rendering parameters (image_options quality auto means unspecified; '
                'output_format is an original-artifact acceptance requirement, not a native '
                'control; do not convert or rerender to meet it): '
                + json.dumps(request.parameters, ensure_ascii=False, sort_keys=True))})
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
        session = None
        stage = 'initialize'
        try:
            with StdioSession([config.executable, '-c', 'mcp_servers={}',
                               '-c', 'plugins={}', '--enable', 'image_generation',
                               'app-server', '--stdio'],
                              files.path.resolve(), timeout=config.timeout) as session:
                session.request('initialize', {'clientInfo': {
                    'name': 'alchemy-render-bridge', 'version': '1'}})
                session.send({'method': 'initialized'})
                stage = 'thread_start'
                response = session.request('thread/start', {
                    'model': config.model, 'cwd': cwd,
                    'approvalPolicy': config.approval_policy, 'approvalsReviewer': 'user',
                    'sandbox': 'workspace-write',
                    'developerInstructions': instructions})
                thread_id = response['thread']['id']
                if not isinstance(thread_id, str) or not thread_id:
                    raise ValueError('Missing Codex thread identity')
                files.write_exclusive(files.path / 'codex-thread.json',
                                      json.dumps({'thread_id': thread_id}).encode())
                stage = 'turn_start'
                response = session.request('turn/start', {
                    'threadId': thread_id, 'input': inputs, 'cwd': cwd,
                    'approvalPolicy': config.approval_policy, 'approvalsReviewer': 'user',
                    'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [cwd],
                        'networkAccess': False, 'excludeSlashTmp': True,
                        'excludeTmpdirEnvVar': True}})
                turn_id = response['turn']['id']
                if not isinstance(turn_id, str) or not turn_id:
                    raise ValueError('Missing Codex turn identity')
                files.write_exclusive(files.path / 'codex-turn.json',
                                      json.dumps({'thread_id': thread_id, 'turn_id': turn_id}).encode())
                events = session.events
                stage = 'turn_wait'
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
                    session.capture_notification(event)
                evidence = {'thread_id': thread_id, 'turn_id': turn_id, 'events': events,
                            'cli_version': probe['cli_version'],
                            'event_summary': session.diagnostic_summary(),
                            **({'parameter_adaptation': {
                                'adapter_id': 'codex_cli_imagegen_v1',
                                'transparent_background': native_options['transparent_background_mapping'],
                            }} if adapted else {})}
                files.write_exclusive(completion, json.dumps(evidence, ensure_ascii=False).encode())
                return self.collect_completed(request, **evidence)
        except PermissionError:
            return ExecutionResult('needs_attention', 'codex_requested_user_attention')
        except (OSError, EOFError, TimeoutError) as exc:
            reason = 'tool_missing' if isinstance(exc, FileNotFoundError) else 'probe_failed'
            self._write_execution_diagnostic(files, stage, exc, session)
            return ExecutionResult('execution_unknown', 'codex_execution_requires_reconciliation',
                                   details={'profile_health_reason': reason,
                                            **({'event_summary': session.diagnostic_summary()} if session else {})})
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            # A request may already have reached the server. Never regenerate.
            self._write_execution_diagnostic(files, stage, exc, session)
            return ExecutionResult('execution_unknown', 'codex_execution_requires_reconciliation',
                                   details={'failure_class': type(exc).__name__,
                                            **({'event_summary': session.diagnostic_summary()} if session else {})})

    @staticmethod
    def _write_execution_diagnostic(files: AttemptFiles, stage: str, exc: Exception,
                                    session: StdioSession | None) -> None:
        """Persist only bounded, allowlisted lifecycle metadata, never payload text."""
        if session is None:
            return
        evidence = {'schema_version': '1', 'stage': stage,
                    'failure_class': type(exc).__name__,
                    'event_summary': session.diagnostic_summary()}
        try:
            files.write_exclusive(files.path / 'codex-execution-diagnostic.json',
                                  json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode())
        except (OSError, FileExistsError, ValueError):
            # The original execution result remains unknown even if diagnostics
            # cannot be persisted; never replace or rewrite existing evidence.
            return

    def collect_completed(self, request: ExecutionRequest, *, thread_id: str,
                          turn_id: str, events: list[dict],
                          cli_version: str | None = None,
                          parameter_adaptation: dict | None = None,
                          event_summary: dict | None = None) -> ExecutionResult:
        """Collect an already executed, precisely bound event stream.

        This does not start/retry a turn or grant the Agent permission to upload.
        Caller must supply IDs it persisted from that attempt's start responses.
        Plain text, `result` prose, and directory mtime are never image evidence.
        """
        validate_image_request(request.parameters, request.references, 'codex-image')
        files = AttemptFiles(self.root, request, 'codex-cli')
        if not thread_id or not turn_id:
            raise ValueError('Persisted thread and turn identity required')
        terminal = self._terminal_failure(request, files, thread_id, turn_id, events)
        if terminal is not None:
            return terminal
        candidates = {}
        completed = False
        failure_reason = None
        for event in events:
            params = event.get('params', {})
            if params.get('threadId') != thread_id:
                continue
            if event.get('method') == 'turn/completed':
                turn = params.get('turn', {})
                if turn.get('id') == turn_id:
                    completed = turn.get('status') == 'completed'
                    error = turn.get('error')
                    if turn.get('status') == 'failed' and isinstance(error, dict):
                        failure_reason = health_reason_from_codex_error(error.get('codexErrorInfo'))
            if event.get('method') != 'item/completed' or params.get('turnId') != turn_id:
                continue
            item = params.get('item', {})
            native_image = (item.get('type') == 'imageGeneration'
                            or (item.get('type') == 'Extension'
                                and item.get('kind') == 'image_gen.generation'))
            if native_image and item.get('status') == 'completed':
                identity = item.get('id')
                if not isinstance(identity, str) or not identity:
                    continue
                if identity in candidates and candidates[identity] != item.get('savedPath'):
                    raise ValueError('Conflicting image item identity')
                candidates[identity] = item.get('savedPath')
        if not completed:
            return ExecutionResult('execution_unknown', 'codex_turn_not_confirmed_complete',
                details={'profile_health_reason': failure_reason} if failure_reason else {})
        if len(candidates) != 1:
            return ExecutionResult('needs_attention', 'codex_single_image_event_required')
        image_item_id, saved = next(iter(candidates.items()))
        if not isinstance(saved, str) or not Path(saved).is_absolute():
            return ExecutionResult('needs_attention', 'codex_original_path_missing')
        path = Path(saved)
        if path.stem != image_item_id:
            return ExecutionResult('needs_attention', 'codex_image_item_identity_mismatch')
        try:
            resolved = path.resolve(strict=True)
            attempt_root = files.path.resolve(strict=True)
        except (OSError, RuntimeError):
            return ExecutionResult('needs_attention', 'codex_original_path_unavailable')
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            return ExecutionResult('needs_attention', 'codex_original_path_untrusted')
        if not (resolved.is_relative_to(attempt_root)
                or self._is_codex_generated_image(resolved, thread_id)):
            return ExecutionResult('needs_attention', 'codex_original_path_outside_owned_scope')
        try:
            content = self._read_scoped_original(
                path, attempt_root, thread_id, resolved)
        except (OSError, RuntimeError, ValueError):
            return ExecutionResult('needs_attention', 'codex_original_path_untrusted')
        result = files.save(content, 'codex_image_event_locally_unverified')
        if result.artifact is not None:
            registry = files.path / 'retention-files.json'
            registered = json.dumps({'original': 'artifact.original',
                                     'sha256': result.artifact.sha256}).encode()
            try:
                files.write_exclusive(registry, registered)
            except FileExistsError:
                if registry.read_bytes() != registered:
                    raise ValueError('Generated original registration changed')
        return ExecutionResult(result.status, result.code, result.artifact,
                               {**result.details, 'thread_id': thread_id, 'turn_id': turn_id,
                                **({'cli_version': cli_version} if cli_version in SUPPORTED_CLI_VERSIONS else {}),
                                **({'event_summary': event_summary} if event_summary else {}),
                                **({'parameter_adaptation': parameter_adaptation}
                                   if parameter_adaptation else {}),
                                'image_item_id': next(iter(candidates))})

    @classmethod
    def _read_scoped_original(cls, path: Path, attempt_root: Path,
                              thread_id: str, expected_path: Path) -> bytes:
        """Read a bounded file handle and recheck its actual target after open.

        Windows junctions can be swapped between path validation and open. The
        handle's final path and file identity are checked before bounded reads.
        """
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
        if os.name != 'nt':
            flags |= getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_ARTIFACT_BYTES:
                raise ValueError('Original is not a bounded regular file')
            final_path = cls._opened_file_path(descriptor)
            if not (cls._lexically_within(final_path, attempt_root)
                    or cls._is_codex_generated_image(final_path, thread_id)):
                raise ValueError('Opened original escaped its owned scope')
            if os.path.normcase(str(final_path)) != os.path.normcase(str(expected_path)):
                raise ValueError('Opened original changed after validation')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                content = stream.read(MAX_ARTIFACT_BYTES + 1)
            if len(content) != info.st_size or len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError('Original changed or exceeded size limit while reading')
            return content
        finally:
            os.close(descriptor)

    @staticmethod
    def _opened_file_path(descriptor: int) -> Path:
        """Return a path reported by the open handle without resolving it again."""
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes

            handle = msvcrt.get_osfhandle(descriptor)
            size = 32768
            buffer = ctypes.create_unicode_buffer(size)
            actual = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
                wintypes.HANDLE(handle), buffer, size, 0)
            if not actual or actual >= size:
                raise OSError('Unable to verify opened original path')
            name = buffer.value
            if name.startswith('\\\\?\\UNC\\'):
                name = '\\\\' + name[8:]
            elif name.startswith('\\\\?\\'):
                name = name[4:]
            return Path(os.path.normpath(name))
        if sys.platform.startswith('linux'):
            return Path(os.path.normpath(os.readlink(f'/proc/self/fd/{descriptor}')))
        raise OSError('Opened-file path verification is unsupported on this platform')

    @staticmethod
    def _lexically_within(path: Path, root: Path) -> bool:
        try:
            Path(os.path.normcase(str(path))).relative_to(
                Path(os.path.normcase(str(root))))
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_codex_generated_image(path: Path, thread_id: str) -> bool:
        """Accept only a direct child of Codex's generated_images/<thread> folder.

        Codex stores native image outputs under CODEX_HOME rather than the
        app-server thread cwd. The event path is copied into Bridge's attempt;
        the two services never share their storage directories.
        """
        if (not thread_id or Path(thread_id).name != thread_id
                or '/' in thread_id or '\\' in thread_id):
            return False
        configured_home = os.environ.get('CODEX_HOME')
        codex_home = Path(configured_home) if configured_home else Path.home() / '.codex'
        try:
            home = codex_home.resolve(strict=True)
            generated_root = codex_home / 'generated_images'
            thread_root = generated_root / thread_id
            if any(candidate.is_symlink()
                   or (hasattr(candidate, 'is_junction') and candidate.is_junction())
                   for candidate in (generated_root, thread_root)):
                return False
            resolved_generated_root = generated_root.resolve(strict=True)
            resolved_thread_root = thread_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return (resolved_generated_root.parent == home
                and resolved_thread_root.parent == resolved_generated_root
                and os.path.normcase(str(path.parent))
                == os.path.normcase(str(resolved_thread_root)))
