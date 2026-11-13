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
