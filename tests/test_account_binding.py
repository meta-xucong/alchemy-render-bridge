import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx

from render_bridge.account_binding import AlchemyNodeAccountStatusClient


def test_bridge_client_signs_exact_node_identity_without_sending_shared_secret():
    secret = "reward-read-test-token"
    identity = SimpleNamespace(pool_id="pool_1", node_id="node_1",
                               executor_instance="executor_1")
    seen = {}

    def respond(request):
        seen["request"] = request
        return httpx.Response(200, json={"available": True, "bound": True,
                                         "account_user_id": 41})

    client = AlchemyNodeAccountStatusClient(
        "https://alchemy.aiself.vip", secret,
        transport=httpx.MockTransport(respond), clock=lambda: 1_800_000_000)
    try:
        result = asyncio.run(client.status_for(identity))
    finally:
        asyncio.run(client.close())

    request = seen["request"]
    timestamp = "1800000000"
    message = "\n".join(("renderbridge-node-binding-v1", timestamp, "pool_1",
                          "node_1", "executor_1"))
    key = hmac.new(secret.encode(), b"alchemy-node-binding-status-key-v1",
                   hashlib.sha256).digest()
    expected = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    assert request.method == "POST"
    assert request.url.path == "/api/internal/render-bridge/node-account-binding"
    assert request.headers["x-bridge-account-status-timestamp"] == timestamp
    assert hmac.compare_digest(request.headers["x-bridge-account-status-signature"], expected)
    assert secret not in request.headers.get("authorization", "")
    assert json.loads(request.content) == {
        "pool_id": "pool_1", "node_id": "node_1", "executor_instance": "executor_1",
    }
    assert result == {"available": True, "bound": True, "account_user_id": 41}


def test_bridge_client_rejects_invalid_alchemy_response():
    def respond(request):
        return httpx.Response(200, json={"available": True, "bound": True,
                                         "account_user_id": "41", "email": "private@example.com"})

    client = AlchemyNodeAccountStatusClient(
        "https://alchemy.aiself.vip", "reward-read-test-token",
        transport=httpx.MockTransport(respond), clock=lambda: 1_800_000_000)
    try:
        result = asyncio.run(client.status_for(SimpleNamespace(
            pool_id="pool_1", node_id="node_1", executor_instance="executor_1")))
    finally:
        asyncio.run(client.close())
    assert result == {"available": False, "bound": False, "account_user_id": None}
