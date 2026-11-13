"""Interactive enrollment and foreground polling for shared-pool node agents."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid

import httpx

from .client import validated_url
from .config import AgentConfig
from .maintenance import maintain_shared_pool_agent
from .pool_client import PoolNodeHTTPClient
from .registry import ExecutorRegistry
from .runner import NodeAgent
from render_bridge.executors.base import ExecutionRequest

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_STATE_FILE = "shared-node.json"


def _save_enrollment(state_dir: Path, value: dict):
    root = state_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(root, 0o700)
    path = root / _STATE_FILE
    if path.is_symlink() or path.exists():
        raise ValueError("Shared node state already exists; refusing to overwrite it")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        os.chmod(path, 0o600)


def _load_enrollment(state_dir: Path):
    path = state_dir.expanduser().resolve() / _STATE_FILE
    if path.is_symlink() or not path.is_file():
        raise ValueError("Shared node enrollment is missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"fake-image", "codex-image", "midjourney-manual"}
    profiles = value.get("profiles") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or not isinstance(value.get("node_token"), str)
            or not value["node_token"] or not isinstance(value.get("url"), str)
            or not isinstance(value.get("node_id"), str) or not _KEY.fullmatch(value["node_id"])
            or not isinstance(value.get("executor_instance"), str)
            or not _KEY.fullmatch(value["executor_instance"])
            or not isinstance(profiles, list) or not profiles
            or any(not isinstance(item, str) or item not in allowed for item in profiles)
            or len(set(profiles)) != len(profiles)):
        raise ValueError("Shared node enrollment is invalid")
    return value


def _registry_for_profiles(state_dir, config_path, profiles):
    registry = ExecutorRegistry.from_config(Path(state_dir).expanduser() / "executors", config_path)
    selected = list(profiles)
    missing = sorted(set(selected) - set(registry.executors))
    if not selected or missing:
        raise ValueError("Local Agent config does not enable enrolled profiles: " + ", ".join(missing))
    registry.executors = {name: registry.executors[name] for name in selected}
    return registry


def _load_bootstrap(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("One-click enrollment package is missing or unsafe")
    if path.stat().st_size > 8192:
        raise ValueError("One-click enrollment package is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("One-click enrollment package is not valid UTF-8 JSON") from exc
    expected = {"schema_version", "url", "invite_code", "profiles", "accept_shared_data"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("One-click enrollment package has an unsupported shape")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("One-click enrollment package version is unsupported")
    if type(value["accept_shared_data"]) is not bool or not value["accept_shared_data"]:
        raise ValueError("One-click package does not contain node sharing consent")
    code = value["invite_code"]
    if not isinstance(code, str) or not 16 <= len(code) <= 256 or any(ord(c) < 33 for c in code):
        raise ValueError("One-click package invitation is invalid")
    profiles = value["profiles"]
    allowed = {"fake-image", "codex-image", "midjourney-manual"}
    if (not isinstance(profiles, list) or not 1 <= len(profiles) <= 3
            or any(not isinstance(item, str) for item in profiles)
            or len(set(profiles)) != len(profiles) or not set(profiles) <= allowed):
        raise ValueError("One-click package profiles are invalid")
    endpoint = validated_url(value["url"] if isinstance(value["url"], str) else "")
    return {"url": endpoint, "invite_code": code, "profiles": profiles}


def enroll(url, state_dir, node_id, executor_instance, profiles, *, bootstrap_file=None):
    if not _KEY.fullmatch(node_id) or not _KEY.fullmatch(executor_instance):
        raise ValueError("Node and executor IDs must use letters, digits, '_' or '-'")
    allowed = {"fake-image", "codex-image", "midjourney-manual"}
    bootstrap_path = Path(bootstrap_file) if bootstrap_file is not None else None
    if bootstrap_path is not None:
        bootstrap = _load_bootstrap(bootstrap_path)
        if url is not None and validated_url(url) != bootstrap["url"]:
            raise ValueError("One-click package endpoint differs from the requested Bridge")
        if profiles is not None and list(profiles) != bootstrap["profiles"]:
            raise ValueError("One-click package profiles differ from the requested profiles")
        endpoint, code, profiles = bootstrap["url"], bootstrap["invite_code"], bootstrap["profiles"]
        print("One-click node setup accepted. By launching this package, this machine may receive participating workspaces' prompts and reference images.")
    else:
        if not url:
            raise ValueError("Bridge URL is required without a one-click enrollment package")
        endpoint = validated_url(url)
        if not profiles:
            profiles = ["fake-image"]
        print("Pool nodes may receive participating workspaces' prompts and reference images.")
        print("Those inputs may be downloaded to this machine while an assigned task runs.")
        if input("Accept this data-sharing responsibility? Type YES: ").strip() != "YES":
            raise ValueError("Sharing consent was not given")
        code = getpass.getpass("One-use node invitation code: ").strip()
        if not code:
            raise ValueError("Invitation code is required")
    if not profiles or len(set(profiles)) != len(profiles) or not set(profiles) <= allowed:
        raise ValueError("Select one or more supported profiles")
    if not endpoint.startswith("https://"):
        # Loopback remains useful for the explicit local integration test.
        from urllib.parse import urlsplit
        host = urlsplit(endpoint).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote Bridge endpoints require HTTPS")
    body = {"invite_code": code, "node_id": node_id,
            "executor_instance": executor_instance, "profiles": profiles,
            "accept_shared_data": True}
    try:
        with httpx.Client(base_url=endpoint, timeout=30, follow_redirects=False,
                          trust_env=False, verify=os.environ.get("BRIDGE_CA_BUNDLE") or True) as client:
            response = client.post("/bridge/node/v2/enroll", json=body)
    except Exception:
        raise ValueError("Bridge enrollment request failed") from None
    if not 200 <= response.status_code < 300:
        try:
            error = response.json().get("error", {}).get("code", "request_failed")
        except (ValueError, AttributeError):
            error = "request_failed"
        raise ValueError(f"Bridge enrollment rejected ({error})")
    result = response.json()
    token = result.get("node_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Bridge enrollment did not return a node token")
    _save_enrollment(state_dir, {"url": endpoint, "pool_id": result["pool_id"],
                                 "node_id": node_id, "executor_instance": executor_instance,
                                 "profiles": profiles, "node_token": token})
    if bootstrap_path is not None:
        try:
            bootstrap_path.unlink()
        except OSError:
            print("Enrollment succeeded; remove the consumed one-use package from the launcher folder.")
    print(json.dumps({"status": "enrolled", "pool_id": result["pool_id"],
                      "node_id": node_id, "executor_instance": executor_instance,
                      "state_dir": str(state_dir.expanduser().resolve()),
                      "token_saved": True}, ensure_ascii=False))
    return 0


def verify_enrollment(state_dir):
    state = _load_enrollment(state_dir)
    client = PoolNodeHTTPClient(state["url"], state["node_token"])
    try:
        value = client.status()
    except Exception:
        raise ValueError("Bridge node login check failed; verify the connection or issue a fresh node invitation") from None
    finally:
        client.close()
    node = value.get("node") if isinstance(value, dict) else None
    if (not isinstance(node, dict) or node.get("node_id") != state.get("node_id")
            or node.get("executor_instance") != state.get("executor_instance")):
        raise ValueError("Bridge node login returned a different node identity")
    print(json.dumps({"status": "authenticated", "node_id": state["node_id"]}, ensure_ascii=False))
    return 0


def preflight(config_path, state_dir, profiles):
    config = AgentConfig.load(Path(config_path))
    selected = list(profiles)
    if (not selected or len(set(selected)) != len(selected)
            or not set(selected) <= set(config.enabled_profiles)):
        missing = sorted(set(selected) - set(config.enabled_profiles))
        raise ValueError("Local Agent config does not enable invited profiles: " + ", ".join(missing))
    registry = _registry_for_profiles(state_dir, Path(config_path), selected)
    request = ExecutionRequest("node-preflight", "node-preflight", "Read-only local readiness check.")
    for profile in selected:
        executor = registry.get(profile)
        if executor is None:
            raise ValueError("Local executor is unavailable for profile: " + profile)
        check = getattr(executor, "preflight", None)
        if callable(check):
            result = check(request)
            if result is not None:
                raise ValueError("Local " + profile + " preflight blocked (" + result.code + ")")
    print(json.dumps({"status": "ready", "profiles": selected}, ensure_ascii=False))
    return 0


def run(url, state_dir, config_path, interval, retention_seconds=86400,
        maintenance_interval_seconds=900, status_port=8766):
    if type(interval) not in (int, float) or not 0.2 <= interval <= 60:
        raise ValueError("Polling interval must be from 0.2 through 60 seconds")
    if type(retention_seconds) is not int or not 60 <= retention_seconds <= 365 * 86400:
        raise ValueError("Retention must be from 60 seconds through one year")
    if type(maintenance_interval_seconds) is not int or not 60 <= maintenance_interval_seconds <= 86400:
        raise ValueError("Maintenance interval must be from 60 through 86400 seconds")
    if type(status_port) is not int or not 0 <= status_port <= 65535:
        raise ValueError("Local status port must be between 0 and 65535")
    state = _load_enrollment(state_dir)
    endpoint = validated_url(url or state["url"])
    if endpoint != state["url"]:
        raise ValueError("Endpoint differs from the enrolled Bridge")
    token = state["node_token"]
    client = PoolNodeHTTPClient(endpoint, token)
    registry = _registry_for_profiles(state_dir, config_path, state["profiles"])
    node = NodeAgent(client, state_dir.expanduser(), registry=registry)
    node._worker_running = True
    stop = threading.Event()
    status_server = None
    status_thread = None
    status_url = None

    def beat():
        delay = 5
        while not stop.wait(delay):
            ok = node.heartbeat()
            delay = 30 if ok else 5

    try:
        from .local_status import start_local_status_server
        try:
            status_server, status_thread, status_url = start_local_status_server(client, status_port)
        except OSError:
            print(json.dumps({"event": "local_status", "status": "unavailable"}), flush=True)
        ok = node.heartbeat()
        heartbeat_thread = threading.Thread(target=beat, name="shared-pool-heartbeat", daemon=True)
        heartbeat_thread.start()
        print(json.dumps({"status": "online" if ok else "connecting",
                          "node_id": state["node_id"], "pool_id": state["pool_id"],
                          "local_status_url": status_url}), flush=True)
        from .hatchet_sidecar import reconcile_pending_artifacts
        for record in node.journal.poll_recovery_records():
            try:
                outcome = node.execute_task(record["task_id"])
                print(json.dumps({"event": "recovery", "task_id": record["task_id"],
                                  "outcome": outcome.get("outcome", "unknown")}), flush=True)
            except Exception:
                print(json.dumps({"event": "recovery", "task_id": record["task_id"],
                                  "outcome": "pending"}), flush=True)
        backoff = float(interval)
        last_maintenance = 0.0
        while True:
            try:
                now = time.monotonic()
                if now - last_maintenance >= maintenance_interval_seconds:
                    result = maintain_shared_pool_agent(node,
                        retention_seconds=retention_seconds, batch_size=20)
                    if result['checked']:
                        print(json.dumps({"event": "retention", **result}), flush=True)
                    last_maintenance = now
                for result in reconcile_pending_artifacts(node):
                    print(json.dumps({"event": "artifact_delivery", **result}), flush=True)
                task_id = client.next_task()
                if task_id:
                    outcome = node.execute_task(task_id)
                    event = {"event": "task", "task_id": task_id,
                             "outcome": outcome.get("outcome", "unknown")}
                    if event["outcome"] == "awaiting_user":
                        event.update({key: outcome[key] for key in
                                      ("task_file", "prompt_file", "request_sha256") if key in outcome})
                    print(json.dumps(event), flush=True)
                backoff = float(interval)
            except Exception as exc:
                print(json.dumps({"event": "poll_retry", "error_type": type(exc).__name__}), flush=True)
                backoff = min(30.0, max(float(interval), backoff * 2))
            time.sleep(backoff)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        if status_server is not None:
            from .local_status import stop_local_status_server
            stop_local_status_server(status_server, status_thread)
        node._worker_running = False
        node.heartbeat()
        client.close()


def import_manual(state_dir, config_path, task_id, file_path, request_sha256,
                  confirm_single_original):
    if confirm_single_original is not True:
        raise ValueError("Explicit single-original confirmation is required")
    state = _load_enrollment(state_dir)
    client = PoolNodeHTTPClient(state["url"], state["node_token"])
    try:
        registry = ExecutorRegistry.from_config(state_dir.expanduser() / "executors", config_path)
        node = NodeAgent(client, state_dir.expanduser(), registry=registry)
        result = node.manual_import(task_id, file_path,
            confirmed_request_sha256=request_sha256, confirmed_single_original=True)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("outcome") in ("artifact_committed", "already_committed") else 2
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("enroll", help="Pair this machine with one shared pool")
    pair.add_argument("--url")
    pair.add_argument("--bootstrap-file", type=Path,
                      help="Consume a one-use package exported by the Bridge operator console")
    pair.add_argument("--state-dir", type=Path, required=True)
    pair.add_argument("--node-id", default="node-" + uuid.uuid4().hex[:12])
    pair.add_argument("--executor-instance", default="exec-" + uuid.uuid4().hex[:12])
    pair.add_argument("--profiles")
    verify = sub.add_parser("verify", help="Verify the saved node credential without starting work")
    verify.add_argument("--state-dir", type=Path, required=True)
    ready = sub.add_parser("preflight", help="Check local profile readiness without login or generation")
    ready.add_argument("--state-dir", type=Path, required=True)
    ready.add_argument("--config", type=Path, required=True)
    ready.add_argument("--profiles", required=True)
    worker = sub.add_parser("run", help="Run the foreground shared-pool node agent")
    worker.add_argument("--url")
    worker.add_argument("--state-dir", type=Path, required=True)
    worker.add_argument("--config", type=Path, help="Local non-secret executor configuration")
    worker.add_argument("--interval", type=float, default=2)
    worker.add_argument("--retention-seconds", type=int, default=86400)
    worker.add_argument("--maintenance-interval-seconds", type=int, default=900)
    worker.add_argument("--status-port", type=int, default=8766,
                        help="Loopback status page port; use 0 for an available ephemeral port")
    manual = sub.add_parser("manual-import", help="Import one explicitly confirmed manual-profile original")
    manual.add_argument("--state-dir", type=Path, required=True)
    manual.add_argument("--config", type=Path)
    manual.add_argument("--request-sha256", required=True)
    manual.add_argument("--confirm-single-original", action="store_true", required=True)
    manual.add_argument("task_id")
    manual.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "enroll":
            profiles = ([item.strip() for item in args.profiles.split(",") if item.strip()]
                        if args.profiles else None)
            return enroll(args.url, args.state_dir, args.node_id, args.executor_instance,
                          profiles, bootstrap_file=args.bootstrap_file)
        if args.command == "verify":
            return verify_enrollment(args.state_dir)
        if args.command == "preflight":
            profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
            return preflight(args.config, args.state_dir, profiles)
        if args.command == "run":
            return run(args.url, args.state_dir, args.config, args.interval,
                       args.retention_seconds, args.maintenance_interval_seconds,
                       args.status_port)
        return import_manual(args.state_dir, args.config, args.task_id, args.file,
                             args.request_sha256, args.confirm_single_original)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__,
                          "detail": str(exc) if isinstance(exc, ValueError) else "operation failed"}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
