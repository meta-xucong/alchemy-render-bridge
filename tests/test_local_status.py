"""Synthetic local status UI tests; no Bridge server or generation is contacted."""
from http.client import HTTPConnection
from urllib.parse import urlsplit

from render_bridge.agent.local_status import start_local_status_server, stop_local_status_server


class FakeNodeClient:
    def __init__(self):
        self.calls = 0

    def status(self):
        self.calls += 1
        return {"node": {"node_id": "fixture-node", "executor_instance": "fixture-executor"},
                "metrics": {"total_started": 2, "succeeded": 1, "failed": 0},
                "recent_executions": [], "tracking_since": "2026-11-12T00:00:00Z"}


def test_loopback_status_page_keeps_node_token_server_side_and_rejects_foreign_host():
    client = FakeNodeClient()
    server, thread, url = start_local_status_server(client, 0)
    parsed = urlsplit(url)

    def get(path, headers=None):
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = response.status, response.headers, body
        connection.close()
        return result

    try:
        status, headers, body = get("/")
        page = body.decode("utf-8")
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert "127.0.0.1" in url and "localStorage" not in page
        assert "node_token" not in page and "Authorization" not in page

        status, headers, body = get("/api/status")
        value = body.decode("utf-8")
        assert status == 200
        assert 'fixture-node' in value
        assert 'total_started' in value
        assert "default-src 'none'" in headers["Content-Security-Policy"]

        status, _, _ = get("/", {"Host": "example.invalid"})
        assert status == 403
        status, _, _ = get("/api/status", {"Origin": "http://example.invalid"})
        assert status == 403
        assert client.calls == 1
    finally:
        stop_local_status_server(server, thread)

class ClaimFakeNodeClient(FakeNodeClient):
    def __init__(self):
        super().__init__()
        self.claims = []

    def confirm_reward_claim(self, challenge):
        self.claims.append(challenge)
        return {"confirmed": True, "node_token": "bridge-bearer-secret",
                "network_credential": "network-secret"}


def test_local_claim_confirmation_requires_exact_host_origin_and_keeps_credentials_private():
    import json

    client = ClaimFakeNodeClient()
    server, thread, url = start_local_status_server(client, 0)
    parsed = urlsplit(url)
    challenge = "Q" * 43

    def post(path, body, headers=None):
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request("POST", path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        result = response.status, dict(response.headers), content
        connection.close()
        return result

    try:
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request("GET", "/")
        response = connection.getresponse()
        page = response.read().decode("utf-8")
        connection.close()
        assert "Alchemy" in page
        assert "node_token" not in page and "Authorization" not in page
        assert "network_credential" not in page and "network-secret" not in page
        assert "localStorage" not in page

        body = json.dumps({"challenge": challenge})
        status, _, _ = post("/api/reward-claims/confirm", body, {
            "Host": f"127.0.0.1:{parsed.port}", "Content-Type": "application/json",
        })
        assert status == 403
        status, _, _ = post("/api/reward-claims/confirm", body, {
            "Host": f"127.0.0.1:{parsed.port}", "Origin": "http://example.invalid",
            "Content-Type": "application/json",
        })
        assert status == 403
        status, _, _ = post("/api/reward-claims/confirm", body, {
            "Host": "example.invalid", "Origin": f"http://127.0.0.1:{parsed.port}",
            "Content-Type": "application/json",
        })
        assert status == 403
        assert client.claims == []

        status, headers, response_body = post("/api/reward-claims/confirm", body, {
            "Host": f"127.0.0.1:{parsed.port}",
            "Origin": f"http://127.0.0.1:{parsed.port}",
            "Content-Type": "application/json",
        })
        assert status == 200
        assert json.loads(response_body) == {"confirmed": True}
        assert headers.get("Access-Control-Allow-Origin") is None
        assert "bridge-bearer-secret" not in response_body.decode("utf-8")
        assert "network-secret" not in response_body.decode("utf-8")
        assert challenge not in response_body.decode("utf-8")
        assert client.claims == [challenge]
    finally:
        stop_local_status_server(server, thread)
