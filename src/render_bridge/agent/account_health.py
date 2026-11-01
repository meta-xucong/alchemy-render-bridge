"""Explicitly enabled status-only CLI RPC. Never starts a thread/turn or logs data.

The user's CLI owns its existing authentication and MAY refresh it internally.
Enabling this probe is separate from health_reporting_enabled. No direct token
read, copied credentials, new login/grant, reserve opt-in or paid generation.
"""
from pathlib import Path
from render_bridge.executors.codex import StdioSession


def probe_account_health(executable, cwd, *, session_factory=StdioSession):
    try:
        with session_factory([executable, 'app-server', '--stdio'], Path(cwd), timeout=3) as session:
            session.request('initialize', {'clientInfo': {'name':'alchemy-render-bridge-health', 'version':'1'}})
            session.send({'method':'initialized'})
            response = session.request('account/rateLimits/read',
                {'supportsLunaReserve':False, 'excludeResetCreditDetails':True})
            if not isinstance(response, dict):
                return None
            # rust-v0.163.0 validates this field against the active account/user.
            # Percentages, reset times, account existence and result prose cannot
            # establish recovery. Discard ALL other response fields immediately.
            allowed = response.get('ordinaryUsageAllowed')
            return allowed if type(allowed) is bool else None
    except Exception:
        # Includes approval/auth requests, unsupported RPC, timeout and bad data.
        # Never log exception text, auto-approve, or initiate login to fix a probe.
        return None
