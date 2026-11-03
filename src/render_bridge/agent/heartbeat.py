"""Worker liveness reporting only; retries liveness and never schedules rendering."""
import threading
import time
import os
import sys
from datetime import datetime, timezone

import httpx


class HeartbeatLoop:
    def __init__(self, node, interval=30):
        if not 10 <= interval <= 60:
            raise ValueError('Heartbeat interval must be between 10 and 60 seconds')
        self.node, self.interval = node, interval
        self.retry_interval = 5
        self.stop = threading.Event()
        self.thread = None
        self.status_thread = None
        self.last_heartbeat_ok = False

    def _next_interval(self, heartbeat_ok):
        return self.interval if heartbeat_ok is not False else self.retry_interval

    def __enter__(self):
        self.node._worker_running = True
        self.last_heartbeat_ok = self.node.heartbeat()
        self._report()
        self.thread = threading.Thread(
            target=self._run,
            args=(self._next_interval(self.last_heartbeat_ok),),
            name='bridge-heartbeat',
            daemon=True,
        )
        self.thread.start()
        self.status_thread = threading.Thread(
            target=self._report_loop,
            name='bridge-status-display',
            daemon=True,
        )
        self.status_thread.start()
        return self

    def _run(self, next_interval=None):
        delay = self.interval if next_interval is None else next_interval
        while not self.stop.wait(delay):
            self.last_heartbeat_ok = self.node.heartbeat()
            delay = self._next_interval(self.last_heartbeat_ok)

    def _report_loop(self):
        while not self.stop.wait(5):
            self._report()

    @staticmethod
    def _paint(text, color):
        if not sys.stdout.isatty():
            return text
        return f'\033[{color}m{text}\033[0m'

    @staticmethod
    def _alchemy_health(path):
        base_url = os.environ.get('BRIDGE_ALCHEMY_BASE_URL', '').rstrip('/')
        if not base_url:
            return '状态未知'
        try:
            response = httpx.get(base_url + path, timeout=2, trust_env=False)
            response.raise_for_status()
            payload = response.json()
            return '运行中' if payload.get('ok') is True or payload.get('status') == 'ok' else '未连接'
        except Exception:
            return '未连接'

    @staticmethod
    def _execution_lease_expired(journal, unresolved):
        if not journal or not unresolved:
            return False
        for record in journal.terminal_recovery_records():
            if record.get('state') not in ('start_requested', 'execution_unknown'):
                continue
            expires_at = record.get('claim_expires_at')
            if not isinstance(expires_at, str):
                continue
            try:
                expires = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _unknown_attention_code(journal, unresolved):
        if not journal or not unresolved:
            return None
        for record in journal.terminal_recovery_records():
            if record.get('state') in ('start_requested', 'execution_unknown'):
                code = record.get('attention_code')
                if isinstance(code, str):
                    return code
        return None

    def _report(self):
        heartbeat_ok = self.last_heartbeat_ok
        worker_running = getattr(self.node, '_worker_running', False)
        active = bool(getattr(self.node, '_active', False))
        journal = getattr(self.node, 'journal', None)
        unresolved = bool(journal and journal.occupied_by_other(''))
        lease_expired = self._execution_lease_expired(journal, unresolved)
        attention_code = self._unknown_attention_code(journal, unresolved)
        registry = getattr(self.node, 'registry', None)
        codex_reported = 'codex-image' in getattr(registry, 'executors', {})
        if not worker_running:
            codex_node_state = '离线'
        elif active:
            codex_node_state = ('在线 / 执行中（租约已过期）' if lease_expired and heartbeat_ok else
                                '在线 / 执行中' if heartbeat_ok else '连接未知 / 本机执行中')
        elif unresolved:
            codex_node_state = ('在线 / Codex 超时，结果待对账' if attention_code == 'codex_turn_timed_out' and heartbeat_ok else
                                '连接未知 / Codex 超时，槽位仍保留' if attention_code == 'codex_turn_timed_out' else
                                '在线 / 租约已过期，槽位仍保留' if lease_expired and heartbeat_ok else
                                '连接未知 / 租约已过期，槽位仍保留' if lease_expired else
                                '在线 / 待对账' if heartbeat_ok
                                else '连接未知 / 本机槽位待对账')
        elif not codex_reported:
            codex_node_state = '需处理'
        elif not heartbeat_ok:
            codex_node_state = '状态未知'
        else:
            codex_node_state = '在线 / 空闲'
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        if active:
            execution_line = ('1 / 1 (busy; lease expired, still held)' if lease_expired
                              else '1 / 1 (busy)')
        elif unresolved:
            execution_line = ('0 / 1 (error: Codex turn timed out; held for reconciliation)'
                              if attention_code == 'codex_turn_timed_out' else
                              '0 / 1 (held: lease expired; reconcile same attempt)'
                              if lease_expired else
                              '0 / 1 (held: reconciliation required)')
        elif not codex_reported:
            execution_line = 'unknown / 1 (Codex profile not reported)'
        elif heartbeat_ok:
            execution_line = '0 / 1 (idle)'
        else:
            execution_line = 'unknown / 1 (node unavailable)'
        active_count = 1 if active else 0
        print('', flush=True)
        print(self._paint(f'[{stamp}] Alchemy VPS status', '36'), flush=True)
        print(
            f'V1 / V3 / Lab ({os.environ.get("BRIDGE_ALCHEMY_BASE_URL", "VPS")}): '
            f'{self._alchemy_health("/healthz")} | '
            f'V2 ({os.environ.get("BRIDGE_ALCHEMY_BASE_URL", "VPS")}): '
            f'{self._alchemy_health("/api/v2/health")}',
            flush=True,
        )
        auth_status = 'authenticated' if heartbeat_ok else 'disconnected / retrying'
        print(self._paint(f'Alchemy -> Bridge: {auth_status} | Codex Node: {codex_node_state}', '32' if heartbeat_ok else '31'), flush=True)
        login_status = os.environ.get('BRIDGE_CODEX_CLI_LOGIN_STATUS', 'unknown')
        login_color = '32' if login_status == 'verified' else '33'
        print(self._paint(f'Codex CLI login: {login_status} locally', login_color), flush=True)
        profile_status = 'reported' if codex_reported else 'not reported'
        print(self._paint(f'Codex profile: {profile_status}', '32' if codex_reported else '33'), flush=True)
        execution_color = ('32' if codex_reported and not active and not unresolved and heartbeat_ok
                           else '33' if active or unresolved else '31')
        print(self._paint(f'Codex execution line: {execution_line}', execution_color), flush=True)
        print(f'Bridge active/queued tasks: {active_count} / unavailable / unavailable | admission: unavailable', flush=True)
        print('Real image generation: not verified', flush=True)
        print('-' * 88, flush=True)

    def __exit__(self, *_exc):
        self.node._worker_running = False
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.status_thread:
            self.status_thread.join(timeout=1)
        self.last_heartbeat_ok = self.node.heartbeat()
        self._report()
