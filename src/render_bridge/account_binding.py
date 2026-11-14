"""Narrow server-to-server projection of Alchemy's node-account binding."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from urllib.parse import urlsplit

import httpx


ALCHEMY_ACCOUNT_API = "https://alchemy.aiself.vip"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SIGNATURE_CONTEXT = "renderbridge-node-binding-v1"
_KEY_CONTEXT = b"alchemy-node-binding-status-key-v1"


def _validated_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port in (None, 443)
    except (TypeError, ValueError):
        valid_port = False
        parsed = None
    if (parsed is None or parsed.scheme != "https" or parsed.hostname != "alchemy.aiself.vip"
            or not valid_port or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Alchemy account API must use the configured HTTPS origin")
    return value.rstrip("/")


class AlchemyNodeAccountStatusClient:
    """Queries only the active owner of the exact Bridge-authenticated node."""

    def __init__(self, base_url: str = ALCHEMY_ACCOUNT_API, signing_secret: str = "",
                 *, transport=None, clock=None):
        if (not isinstance(signing_secret, str) or not signing_secret
                or not signing_secret.isascii()):
            raise ValueError("A Bridge reward-read signing secret is required")
        self.signing_key = hmac.new(signing_secret.encode("ascii"), _KEY_CONTEXT,
                                    hashlib.sha256).digest()
        self.clock = clock
        self.http = httpx.AsyncClient(
            base_url=_validated_origin(base_url),
            timeout=httpx.Timeout(3.0, connect=2.0, pool=1.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            follow_redirects=False, trust_env=False, verify=True, transport=transport,
        )

    async def status_for(self, identity) -> dict:
        values = tuple(getattr(identity, key, None) for key in
                       ("pool_id", "node_id", "executor_instance"))
        if not all(isinstance(value, str) and _IDENTIFIER.fullmatch(value)
                   for value in values):
            return {"available": False, "bound": False, "account_user_id": None}
        timestamp = str(int(self.clock() if self.clock is not None else time.time()))
        message = "\n".join((_SIGNATURE_CONTEXT, timestamp, *values)).encode("ascii")
        signature = hmac.new(self.signing_key, message, hashlib.sha256).hexdigest()
        try:
            response = await self.http.post(
                "/api/internal/render-bridge/node-account-binding",
                json={"pool_id": values[0], "node_id": values[1],
                      "executor_instance": values[2]},
                headers={"X-Bridge-Account-Status-Timestamp": timestamp,
                         "X-Bridge-Account-Status-Signature": signature},
            )
            if response.status_code != 200:
                return {"available": False, "bound": False, "account_user_id": None}
            value = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return {"available": False, "bound": False, "account_user_id": None}
        if (not isinstance(value, dict)
                or set(value) != {"available", "bound", "account_user_id"}
                or value.get("available") is not True
                or type(value.get("bound")) is not bool):
            return {"available": False, "bound": False, "account_user_id": None}
        account_user_id = value.get("account_user_id")
        if value["bound"]:
            if type(account_user_id) is not int or account_user_id <= 0:
                return {"available": False, "bound": False, "account_user_id": None}
        elif account_user_id is not None:
            return {"available": False, "bound": False, "account_user_id": None}
        return {"available": True, "bound": value["bound"],
                "account_user_id": account_user_id}

    async def close(self):
        await self.http.aclose()
