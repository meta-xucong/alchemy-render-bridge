"""Source API security/contract tests; no sockets, providers, or paid execution."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from render_bridge.api import create_app
from render_bridge.domain import MAX_INPUT_BYTES, BridgeStore, SourceScope
from render_bridge.fake import fixture_png, run_fake_once
from render_bridge.schemas import ReceiptAck


BASE = "/bridge/v1"
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def task_payload(origin: str = "origin-1") -> dict:
    return {
        "schema_version": "1",
        "origin_key": origin,
        "binding_id": "opaque-binding-1",
        "operation": "image.generate",
        "execution_profile_id": "fake-image",
        "execution_profile_revision": "1",
        "prompt": "A synthetic image for the isolated contract test.",
        "parameters": {},
        "references": [],
        "output_slot": {"slot_id": "primary", "count": 1},
        "deadline": "2026-10-21T00:00:00Z",
        "routing": "bridge_only",
    }


class SourceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scope = SourceScope("source-a", "workspace-a")
        self.other_workspace = SourceScope("source-a", "workspace-b")
        self.other_source = SourceScope("source-b", "workspace-a")
        self.tokens = {
            "test-token-a": self.scope,
            "test-token-b": self.other_workspace,
            "test-token-c": self.other_source,
        }
        self.store = BridgeStore(
            self.root / "bridge.sqlite3", self.root / "artifacts", clock=lambda: NOW
        )
        self.app = create_app(store=self.store, source_tokens=self.tokens)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=self.app, raise_app_exceptions=False
            ),
            base_url="https://bridge.invalid",
            headers={"Authorization": "Bearer test-token-a"},
            follow_redirects=False,
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.close()
        self.temp.cleanup()

    async def reserve_input(
        self, content: bytes | None = None, key: str = "input-1", **updates
    ) -> tuple[dict, bytes]:
        content = fixture_png() if content is None else content
        payload = {
            "input_key": key,
            "original_sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mime_type": "image/png",
        }
        payload.update(updates)
        response = await self.client.post(f"{BASE}/inputs", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), content

    async def ready_input(self, key: str = "input-1") -> tuple[dict, bytes]:
        reserved, content = await self.reserve_input(key=key)
        response = await self.client.put(
            f"{BASE}/inputs/{reserved['input_id']}/content",
            content=content,
            headers={"Content-Type": "image/png"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "ready")
        return response.json(), content

    async def create_task(self, payload: dict | None = None) -> dict:
        response = await self.client.post(
            f"{BASE}/tasks", json=task_payload() if payload is None else payload
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def receipt(self) -> tuple[dict, dict]:
        task = await self.create_task()
        result = run_fake_once(
            self.store,
            self.scope,
            task["remote_task_id"],
            node_id="test-node",
            executor_instance="test-executor",
            start_log=self.root / "fake-starts.jsonl",
        )
        self.assertTrue(result["executed"])
        return task, result["receipt"]

    async def test_auth_is_required_and_does_not_echo_secrets(self):
        request = self.client.build_request("GET", f"{BASE}/capabilities")
        del request.headers["authorization"]
        response = await self.client.send(request)
        self.assertEqual(response.status_code, 401)
        for header in (
            "",
            "Basic password-secret",
            "Bearer unknown-secret",
            "Bearer  test-token-a",
            "Bearer test-token-a extra",
        ):
            response = await self.client.get(
                f"{BASE}/capabilities", headers={"Authorization": header}
            )
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
            self.assertNotIn("secret", response.text)
            self.assertNotIn("test-token-a", response.text)
        response = await self.client.get(
            f"{BASE}/capabilities",
            headers=[
                ("Authorization", "Bearer test-token-a"),
                ("Authorization", "Bearer test-token-b"),
            ],
        )
        self.assertEqual(response.status_code, 401)

    async def test_factory_requires_explicit_valid_auth_and_limits(self):
        for tokens in ({}, {"": self.scope}, {"test-token": "workspace-a"}):
            with self.assertRaises(ValueError):
                create_app(store=self.store, source_tokens=tokens)
        for limit in (0, True, MAX_INPUT_BYTES + 1):
            with self.assertRaises(ValueError):
                create_app(
                    store=self.store, source_tokens=self.tokens, max_upload_bytes=limit
                )
        self.tokens["test-token-a"] = self.other_workspace
        task = await self.create_task()
        self.assertEqual(
            self.store.get_task(self.scope, task["remote_task_id"])["remote_task_id"],
            task["remote_task_id"],
        )

    async def test_capabilities_are_experimental_and_private(self):
        response = await self.client.get(f"{BASE}/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertIn("fake-image", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("test-token", response.text)

    async def test_no_node_engine_or_implicit_server_routes(self):
        for path in (
            "/docs",
            "/openapi.json",
            f"{BASE}/nodes",
            f"{BASE}/claims",
            f"{BASE}/start-grants",
            f"{BASE}/engine",
            f"{BASE}/uploads",
        ):
            for method in ("GET", "POST"):
                response = await self.client.request(method, path)
                self.assertEqual(response.status_code, 404, (method, path, response.text))
        response = await self.client.get(f"{BASE}/capabilities/")
        self.assertEqual(response.status_code, 404)

    async def test_input_reservation_upload_and_response_loss_replay(self):
        first, content = await self.reserve_input()
        replay, _ = await self.reserve_input()
        self.assertEqual(first["input_id"], replay["input_id"])
        for _ in range(2):
            response = await self.client.put(
                f"{BASE}/inputs/{first['input_id']}/content",
                content=content,
                headers={"Content-Type": "image/png"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "ready")
        fetched = await self.client.get(f"{BASE}/inputs/{first['input_id']}")
        self.assertEqual(fetched.json()["original_sha256"], hashlib.sha256(content).hexdigest())
        changed = {
            "input_key": "input-1",
            "original_sha256": "0" * 64,
            "size": len(content),
            "mime_type": "image/png",
        }
        conflict = await self.client.post(f"{BASE}/inputs", json=changed)
        self.assertEqual(conflict.status_code, 409, conflict.text)

    async def test_input_scope_is_checked_before_upload_body_consumption(self):
        reserved, _ = await self.reserve_input()
        consumed = []

        async def body():
            consumed.append(True)
            yield b"x"

        for token in ("test-token-b", "test-token-c"):
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "image/png"}
            response = await self.client.put(
                f"{BASE}/inputs/{reserved['input_id']}/content",
                content=body(),
                headers=headers,
            )
            self.assertEqual(response.status_code, 404, response.text)
            response = await self.client.get(
                f"{BASE}/inputs/{reserved['input_id']}", headers=headers
            )
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(consumed, [])

    async def test_upload_rejects_bad_hash_bad_media_and_wrong_content_type(self):
        reserved, content = await self.reserve_input(original_sha256="0" * 64)
        response = await self.client.put(
            f"{BASE}/inputs/{reserved['input_id']}/content",
            content=content,
            headers={"Content-Type": "image/png"},
        )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        invalid, content = await self.reserve_input(b"not a PNG image", key="invalid-media")
        response = await self.client.put(
            f"{BASE}/inputs/{invalid['input_id']}/content",
            content=content,
            headers={"Content-Type": "image/png"},
        )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        valid, content = await self.reserve_input(key="wrong-content-type")
        response = await self.client.put(
            f"{BASE}/inputs/{valid['input_id']}/content",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 415)
        for record in (reserved, invalid, valid):
            fetched = await self.client.get(f"{BASE}/inputs/{record['input_id']}")
            self.assertNotEqual(fetched.json()["state"], "ready")

    async def test_upload_stops_stream_at_reservation_bound(self):
        reserved, _ = await self.reserve_input(size=10)
        consumed = []

        async def oversized():
            consumed.append(1)
            yield b"a" * 6
            consumed.append(2)
            yield b"b" * 6
            consumed.append(3)
            yield b"c" * 100

        response = await self.client.put(
            f"{BASE}/inputs/{reserved['input_id']}/content",
            content=oversized(),
            headers={"Content-Type": "image/png"},
        )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(consumed, [1, 2])

    async def test_upload_content_length_cannot_bypass_byte_limit(self):
        reserved, _ = await self.reserve_input(size=10)
        for length, content, expected in (
            ("100", b"x", 413),
            ("1", b"x" * 20, 413),
            ("1", b"xx", 400),
            ("garbage", b"x", 400),
        ):
            response = await self.client.put(
                f"{BASE}/inputs/{reserved['input_id']}/content",
                content=content,
                headers={"Content-Type": "image/png", "Content-Length": length},
            )
            self.assertEqual(response.status_code, expected, response.text)

    async def test_body_scope_and_unknown_fields_are_forbidden(self):
        for field in ("source_id", "source_instance", "workspace_id", "owner", "node_id"):
            payload = task_payload()
            payload[field] = "forged-owner-secret"
            response = await self.client.post(f"{BASE}/tasks", json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("forged-owner-secret", response.text)
        reserved = {
            "input_key": "input-1",
            "original_sha256": "0" * 64,
            "size": 5,
            "mime_type": "image/png",
            "workspace_id": "workspace-b",
        }
        response = await self.client.post(f"{BASE}/inputs", json=reserved)
        self.assertEqual(response.status_code, 422)

    async def test_task_replay_conflict_and_by_origin_are_read_only(self):
        payload = task_payload()
        first = await self.create_task(payload)
        second = await self.create_task(payload)
        self.assertEqual(first["remote_task_id"], second["remote_task_id"])
        self.assertEqual(first["task_digest"], second["task_digest"])
        for path in (
            f"{BASE}/tasks/{first['remote_task_id']}",
            f"{BASE}/tasks/by-origin/{payload['origin_key']}",
        ):
            for _ in range(3):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["state"], "queued")
        changed = copy.deepcopy(payload)
        changed["prompt"] += " Changed."
        response = await self.client.post(f"{BASE}/tasks", json=changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse((self.root / "fake-starts.jsonl").exists())

    async def test_task_ids_origins_and_cancellation_are_source_workspace_scoped(self):
        task = await self.create_task()
        for token in ("test-token-b", "test-token-c"):
            headers = {"Authorization": f"Bearer {token}"}
            for method, path in (
                ("GET", f"{BASE}/tasks/{task['remote_task_id']}"),
                ("GET", f"{BASE}/tasks/by-origin/origin-1"),
                ("POST", f"{BASE}/tasks/{task['remote_task_id']}/cancel"),
            ):
                response = await self.client.request(method, path, headers=headers)
                self.assertEqual(response.status_code, 404, response.text)
            response = await self.client.post(
                f"{BASE}/tasks", json=task_payload(), headers=headers
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotEqual(response.json()["remote_task_id"], task["remote_task_id"])

    async def test_origin_keys_are_url_safe_and_recoverable_at_boundaries(self):
        for origin in (
            "tenant/job-1", ".", "..", "percent%2Fencoded", "中文", "a.b",
            "-starts-with-hyphen", "_starts-with-underscore", "a" * 129,
        ):
            response = await self.client.post(
                f"{BASE}/tasks", json=task_payload(origin)
            )
            self.assertEqual(response.status_code, 422, (origin, response.text))
        for origin in ("a", "A1_b-C", "z" * 128):
            task = await self.create_task(task_payload(origin))
            response = await self.client.get(f"{BASE}/tasks/by-origin/{origin}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["remote_task_id"], task["remote_task_id"])
            self.assertEqual(response.json()["origin_key"], origin)

    async def test_reference_must_be_ready_and_in_same_scope(self):
        reserved, _ = await self.reserve_input()
        payload = task_payload()
        payload["references"] = [{
            "input_ref": reserved["input_id"],
            "role": "reference",
            "hash": reserved["original_sha256"],
        }]
        response = await self.client.post(f"{BASE}/tasks", json=payload)
        self.assertGreaterEqual(response.status_code, 400)
        ready, _ = await self.ready_input()
        self.assertEqual(ready["input_id"], reserved["input_id"])
        for token in ("test-token-b", "test-token-c"):
            response = await self.client.post(
                f"{BASE}/tasks",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertGreaterEqual(response.status_code, 400)
            self.assertLess(response.status_code, 500)
        task = await self.create_task(payload)
        self.assertEqual(task["state"], "queued")

    async def test_strict_integer_and_boolean_rejection(self):
        for value in (True, False, "32", 32.0, None, 0, 1025):
            payload = task_payload()
            payload["parameters"] = {"width": value}
            response = await self.client.post(f"{BASE}/tasks", json=payload)
            self.assertEqual(response.status_code, 422, (value, response.text))
        for value in (True, "1", 1.0, 2):
            payload = task_payload()
            payload["output_slot"]["count"] = value
            response = await self.client.post(f"{BASE}/tasks", json=payload)
            self.assertEqual(response.status_code, 422, (value, response.text))
        for value in (True, "1", 1.0, -1, 2**32):
            payload = task_payload()
            payload["parameters"] = {"seed": value}
            response = await self.client.post(f"{BASE}/tasks", json=payload)
            self.assertEqual(response.status_code, 422, (value, response.text))

    async def test_multiline_prompt_is_preserved_but_control_bytes_reject(self):
        payload = task_payload()
        payload["prompt"] = "First line.\nSecond line.\tKeep the prompt unchanged."
        task = await self.create_task(payload)
        response = await self.client.get(f"{BASE}/tasks/{task['remote_task_id']}")
        self.assertEqual(response.json()["request"]["prompt"], payload["prompt"])
        for control in ("\x00", "\x01", "\x1b"):
            invalid = task_payload("invalid-prompt")
            invalid["prompt"] = f"do-not-echo{control}secret"
            response = await self.client.post(f"{BASE}/tasks", json=invalid)
            self.assertGreaterEqual(response.status_code, 400)
            self.assertLess(response.status_code, 500)
            self.assertNotIn("do-not-echo", response.text)

    async def test_input_size_is_strict_and_metadata_has_no_coercion(self):
        payload = {
            "input_key": "input-1",
            "original_sha256": "0" * 64,
            "size": 1,
            "mime_type": "image/png",
        }
        for field, value in (
            ("size", True), ("size", "1"), ("size", 1.0), ("size", 0),
            ("size", MAX_INPUT_BYTES + 1), ("mime_type", "text/html"),
            ("original_sha256", "G" * 64),
        ):
            response = await self.client.post(
                f"{BASE}/inputs", json={**payload, field: value}
            )
            self.assertEqual(response.status_code, 422, response.text)

    async def test_bounds_unknown_capabilities_and_nested_unknown_fields(self):
        variants = []
        for field, value in (
            ("schema_version", "2"),
            ("operation", "video.generate"),
            ("execution_profile_id", "codex"),
            ("execution_profile_revision", "2"),
            ("routing", "fallback"),
            ("prompt", "x" * 16_001),
            ("prompt", ""),
            ("binding_id", "x" * 257),
        ):
            payload = task_payload()
            payload[field] = value
            variants.append(payload)
        payload = task_payload()
        payload["parameters"] = {"remote_shell": "do-not-execute"}
        variants.append(payload)
        payload = task_payload()
        payload["output_slot"]["extra"] = 1
        variants.append(payload)
        payload = task_payload()
        payload["references"] = [{"input_ref": "a", "role": "reference", "hash": "0" * 64}] * 9
        variants.append(payload)
        for payload in variants:
            response = await self.client.post(f"{BASE}/tasks", json=payload)
            self.assertEqual(response.status_code, 422, response.text)

    async def test_duplicate_json_keys_nonfinite_and_malformed_json_rejected(self):
        payload = json.dumps(task_payload())
        duplicate = payload.replace('"parameters": {}', '"parameters": {"width":32,"width":16}')
        bad_payloads = [
            duplicate,
            payload.replace('"origin_key": "origin-1"', '"origin_key":"a","origin_key":"b"'),
            payload.replace('"parameters": {}', '"parameters": {"width":NaN}'),
            payload.replace('"parameters": {}', '"parameters": {"width":Infinity}'),
            "{",
            "[]",
            "null",
        ]
        for content in bad_payloads:
            response = await self.client.post(
                f"{BASE}/tasks", content=content, headers={"Content-Type": "application/json"}
            )
            self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.post(
            f"{BASE}/tasks",
            content=b"\xff",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)

    async def test_json_request_limit_and_content_type(self):
        response = await self.client.post(
            f"{BASE}/tasks", content=b"x" * 65_537,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        response = await self.client.post(
            f"{BASE}/tasks", content=json.dumps(task_payload()),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status_code, 415)

    async def test_cancel_is_idempotent_and_does_not_restart(self):
        task = await self.create_task()
        for _ in range(2):
            response = await self.client.post(
                f"{BASE}/tasks/{task['remote_task_id']}/cancel"
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "canceled")
        replay = await self.create_task()
        self.assertEqual(replay["remote_task_id"], task["remote_task_id"])
        self.assertEqual(replay["state"], "canceled")

    async def test_unknown_execution_replay_and_cancel_never_restart(self):
        task = await self.create_task()
        task_id = task["remote_task_id"]
        claim = self.store.claim_task(
            self.scope, task_id, node_id="test-node", executor_instance="test-executor"
        )
        grant = self.store.start_execution(self.scope, task_id, claim["claim_id"])
        self.assertTrue(grant["may_execute"])
        self.store.mark_execution_unknown(self.scope, task_id, claim["claim_id"])
        for _ in range(3):
            response = await self.client.get(f"{BASE}/tasks/{task_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "execution_unknown")
            replay = await self.create_task()
            self.assertEqual(replay["remote_task_id"], task_id)
            self.assertEqual(replay["state"], "execution_unknown")
            self.assertEqual(replay["attempt_id"], grant["attempt_id"])
        for _ in range(2):
            response = await self.client.post(f"{BASE}/tasks/{task_id}/cancel")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "cancel_requested")
        repeated_grant = self.store.start_execution(self.scope, task_id, claim["claim_id"])
        self.assertFalse(repeated_grant["may_execute"])
        self.assertEqual(repeated_grant["attempt_id"], grant["attempt_id"])

    async def test_receipt_artifact_and_ack_are_scoped_and_replay_safe(self):
        task, receipt = await self.receipt()
        receipt_id = receipt["receipt_id"]
        artifact_id = receipt["artifact_id"]
        ack = {
            "client_receipt_id": "client-receipt-1",
            "decision": "accepted",
            "reason_code": "fixture_received",
            "original_hash": receipt["original_sha256"],
            "native_registration_ref": "opaque-registration-1",
        }
        for token in ("test-token-b", "test-token-c"):
            headers = {"Authorization": f"Bearer {token}"}
            for method, path in (
                ("GET", f"{BASE}/receipts/{receipt_id}"),
                ("GET", f"{BASE}/artifacts/{artifact_id}/content"),
                ("POST", f"{BASE}/receipts/{receipt_id}/ack"),
            ):
                response = await self.client.request(method, path, headers=headers, json=ack)
                self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.get(f"{BASE}/receipts/{receipt_id}")
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.get(f"{BASE}/artifacts/{artifact_id}/content")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, fixture_png())
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), receipt["original_sha256"])
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["content-disposition"], "attachment")
        for _ in range(2):
            response = await self.client.post(f"{BASE}/receipts/{receipt_id}/ack", json=ack)
            self.assertEqual(response.status_code, 200, response.text)
        conflict = dict(ack, decision="rejected")
        response = await self.client.post(f"{BASE}/receipts/{receipt_id}/ack", json=conflict)
        self.assertEqual(response.status_code, 409, response.text)
        fetched = await self.client.get(f"{BASE}/tasks/{task['remote_task_id']}")
        self.assertEqual(fetched.json()["state"], "artifacts_ready")
        self.assertEqual(fetched.json()["delivery_ack"], "accepted")
        starts = (self.root / "fake-starts.jsonl").read_text().splitlines()
        self.assertEqual(len(starts), 1)

    async def test_errors_never_include_internal_exception_or_request_values(self):
        with patch.object(
            self.store, "capabilities",
            side_effect=RuntimeError("/private/secret-path password=test-token-a"),
        ):
            response = await self.client.get(f"{BASE}/capabilities")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        for secret in ("private", "password", "test-token-a", "RuntimeError"):
            self.assertNotIn(secret, response.text)

    async def test_ack_optional_non_null_schema_matches_runtime(self):
        schema = ReceiptAck.model_json_schema()
        for field in ("normalized_hash", "native_registration_ref"):
            self.assertNotIn(field, schema["required"])
            definition = schema["properties"][field]
            self.assertEqual(definition["type"], "string")
            self.assertNotIn("anyOf", definition)
            self.assertNotIn("default", definition)
        _task, receipt = await self.receipt()
        ack = {
            "client_receipt_id": "client-receipt-1",
            "decision": "accepted",
            "reason_code": "fixture_received",
            "original_hash": receipt["original_sha256"],
        }
        for field in ("normalized_hash", "native_registration_ref"):
            response = await self.client.post(
                f"{BASE}/receipts/{receipt['receipt_id']}/ack",
                json={**ack, field: None},
            )
            self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.post(
            f"{BASE}/receipts/{receipt['receipt_id']}/ack", json=ack
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["acknowledgement"], ack)

    async def test_slow_store_does_not_block_event_loop_or_queue_more_work(self):
        started, release = threading.Event(), threading.Event()
        original = self.store.capabilities

        def slow_capabilities(scope):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test failed to release store operation.")
            return original(scope)

        with patch.object(self.store, "capabilities", side_effect=slow_capabilities):
            first = asyncio.create_task(self.client.get(f"{BASE}/capabilities"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                response = await asyncio.wait_for(
                    self.client.get(f"{BASE}/capabilities"), timeout=1
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["error"]["code"], "store_busy")
                self.assertEqual(response.headers["retry-after"], "1")
            finally:
                release.set()
                first_response = await first
        self.assertEqual(first_response.status_code, 200, first_response.text)
        response = await self.client.get(f"{BASE}/capabilities")
        self.assertEqual(response.status_code, 200)

    async def test_canceled_http_request_cannot_free_live_store_operation(self):
        started, release, finished = (
            threading.Event(), threading.Event(), threading.Event()
        )
        original = self.store.capabilities

        def slow_capabilities(scope):
            started.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("Test failed to release store operation.")
                return original(scope)
            finally:
                finished.set()

        with patch.object(self.store, "capabilities", side_effect=slow_capabilities):
            first = asyncio.create_task(self.client.get(f"{BASE}/capabilities"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                first.cancel()
                await asyncio.sleep(0)
                response = await asyncio.wait_for(
                    self.client.get(f"{BASE}/capabilities"), timeout=1
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                try:
                    await first
                except asyncio.CancelledError:
                    pass
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        response = await self.client.get(f"{BASE}/capabilities")
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
