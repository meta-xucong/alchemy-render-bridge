import json
from pathlib import Path

import pytest

from render_bridge.agent import pool_node


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"node_token": "node-secret", "pool_id": "pool-1"}


class _Client:
    request_body = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, path, *, json):
        assert path == "/bridge/node/v2/enroll"
        self.request_body = json
        type(self).request_body = json
        return _Response()


def _write_bootstrap(path: Path, **updates):
    value = {
        "schema_version": 1,
        "url": "https://bridge.example",
        "invite_code": "single-use-invitation-code",
        "profiles": ["fake-image"],
        "accept_shared_data": True,
    }
    value.update(updates)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_bootstrap_enrollment_saves_node_auth_and_consumes_one_use_file(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "agent-state"
    package = tmp_path / "node-enrollment.json"
    _write_bootstrap(package)
    monkeypatch.setattr(pool_node.httpx, "Client", _Client)

    assert pool_node.enroll(None, state_dir, "node-1", "exec-1", None,
                            bootstrap_file=package) == 0

    saved = json.loads((state_dir / "shared-node.json").read_text(encoding="utf-8"))
    assert saved["url"] == "https://bridge.example"
    assert saved["node_token"] == "node-secret"
    assert _Client.request_body == {
        "invite_code": "single-use-invitation-code",
        "node_id": "node-1",
        "executor_instance": "exec-1",
        "profiles": ["fake-image"],
        "accept_shared_data": True,
    }
    assert not package.exists()
    output = capsys.readouterr().out
    assert "single-use-invitation-code" not in output
    assert "node-secret" not in output


@pytest.mark.parametrize("updates", [
    {"schema_version": True},
    {"accept_shared_data": False},
    {"profiles": []},
    {"profiles": ["unknown-profile"]},
    {"url": "http://bridge.example"},
    {"extra": "unexpected"},
])
def test_invalid_bootstrap_is_rejected_without_network_or_consuming_file(tmp_path, monkeypatch, updates):
    package = tmp_path / "node-enrollment.json"
    _write_bootstrap(package, **updates)

    def unexpected_client(**_kwargs):
        raise AssertionError("invalid bootstrap must be rejected before network access")

    monkeypatch.setattr(pool_node.httpx, "Client", unexpected_client)
    with pytest.raises(ValueError):
        pool_node.enroll(None, tmp_path / "state", "node-1", "exec-1", None,
                         bootstrap_file=package)
    assert package.exists()


def test_preflight_limits_runtime_profiles_to_the_invitation(tmp_path):
    config = tmp_path / "agent.json"
    config.write_text(json.dumps({
        "enabled_profiles": ["fake-image", "midjourney-manual"],
        "codex": {"enabled": False},
    }), encoding="utf-8")

    assert pool_node.preflight(config, tmp_path / "state", ["fake-image"]) == 0
    registry = pool_node._registry_for_profiles(tmp_path / "state", config, ["fake-image"])
    assert sorted(registry.executors) == ["fake-image"]
    with pytest.raises(ValueError, match="does not enable enrolled profiles"):
        pool_node._registry_for_profiles(tmp_path / "state", config, ["codex-image"])


def test_preflight_blocks_unaccepted_codex_without_attempting_login_or_generation(tmp_path):
    config = tmp_path / "agent.json"
    config.write_text(json.dumps({
        "enabled_profiles": ["codex-image"],
        "codex": {"enabled": False},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="codex_local_acceptance_required"):
        pool_node.preflight(config, tmp_path / "state", ["codex-image"])


def test_verify_enrollment_checks_saved_node_identity(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "agent-state"
    state_dir.mkdir()
    (state_dir / "shared-node.json").write_text(json.dumps({
        "url": "https://bridge.example",
        "pool_id": "pool-1",
        "node_id": "node-1",
        "executor_instance": "exec-1",
        "profiles": ["fake-image"],
        "node_token": "node-secret",
    }), encoding="utf-8")

    class StatusClient:
        def __init__(self, url, token):
            assert url == "https://bridge.example"
            assert token == "node-secret"

        def status(self):
            return {"node": {"node_id": "node-1", "executor_instance": "exec-1"}}

        def close(self):
            pass

    monkeypatch.setattr(pool_node, "PoolNodeHTTPClient", StatusClient)
    assert pool_node.verify_enrollment(state_dir) == 0
    assert "node-secret" not in capsys.readouterr().out
