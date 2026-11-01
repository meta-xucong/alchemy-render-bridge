"""Source HTTP -> isolated executor -> receipt/download/ack contract tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.fake import fixture_png, run_fake_once


class FakeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scope = SourceScope("mock-alchemy", "test-workspace")
        self.store = BridgeStore(self.root / "bridge.sqlite", self.root / "artifacts")
        self.app = create_app(store=self.store, source_tokens={"fixture-only": self.scope})
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test.invalid",
            headers={"Authorization": "Bearer fixture-only"},
        )
        self.starts = self.root / "independent-start-count.jsonl"

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    def task_request(self, **overrides):
        payload = {
            "schema_version": "1", "origin_key": "origin-e2e", "binding_id": "opaque-binding",
            "operation": "image.generate", "execution_profile_id": "fake-image",
            "execution_profile_revision": "1", "prompt": "A synthetic fixture only.",
            "parameters": {"width": 32, "height": 24, "seed": 0}, "references": [],
            "output_slot": {"slot_id": "primary", "count": 1},
            "deadline": (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "routing": "bridge_only",
        }
        payload.update(overrides)
        return payload

    async def test_source_to_fake_to_download_ack_and_replays(self):
        content = fixture_png(16, 16)
        digest = hashlib.sha256(content).hexdigest()
        input_response = await self.client.post("/bridge/v1/inputs", json={
            "input_key": "input-e2e", "original_sha256": digest,
            "size": len(content), "mime_type": "image/png",
        })
        self.assertLess(input_response.status_code, 300, input_response.text)
        input_id = input_response.json()["input_id"]
        response = await self.client.put(f"/bridge/v1/inputs/{input_id}/content", content=content, headers={"Content-Type": "image/png"})
        self.assertLess(response.status_code, 300, response.text)
        request = self.task_request(references=[{"input_ref": input_id, "role": "reference", "hash": digest}])
        response = await self.client.post("/bridge/v1/tasks", json=request)
        self.assertLess(response.status_code, 300, response.text)
        task = response.json()
        task_id = task["remote_task_id"]
        # HTTP creation and repeated GETs must not execute a provider.
        self.assertFalse(self.starts.exists())
        self.assertEqual((await self.client.post("/bridge/v1/tasks", json=request)).json()["remote_task_id"], task_id)
        result = run_fake_once(self.store, self.scope, task_id, node_id="fixture-node", executor_instance="instance-1", start_log=self.starts)
        self.assertTrue(result["executed"])
        receipt = result["receipt"]
        receipt_id = receipt["receipt_id"]
        response = await self.client.get(f"/bridge/v1/receipts/{receipt_id}")
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.get(f"/bridge/v1/artifacts/{receipt['artifact_id']}/content")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), receipt["original_sha256"])
        # Independent external-side counter, not merely final artifact cardinality.
        for _ in range(100):
            try:
                run_fake_once(self.store, self.scope, task_id, node_id="fixture-node", executor_instance="instance-1", start_log=self.starts)
            except DomainError as exc:
                self.assertIn(exc.status, (409, 410))
        self.assertEqual(len(self.starts.read_text().splitlines()), 1)
        ack = {
            "client_receipt_id": "mock-native-registration-1", "decision": "accepted",
            "reason_code": "fixture_import_confirmed", "original_hash": receipt["original_sha256"],
            "native_registration_ref": "mock-output-only",
        }
        first = await self.client.post(f"/bridge/v1/receipts/{receipt_id}/ack", json=ack)
        self.assertLess(first.status_code, 300, first.text)
        for _ in range(10):
            replay = await self.client.post(f"/bridge/v1/receipts/{receipt_id}/ack", json=ack)
            self.assertEqual(replay.json(), first.json())
        # Reopen persistent store: artifacts and receipt survive server reconstruction.
        reopened = BridgeStore(self.root / "bridge.sqlite", self.root / "artifacts")
        self.assertEqual(reopened.get_task(self.scope, task_id)["remote_task_id"], task_id)
        self.assertEqual(len(self.starts.read_text().splitlines()), 1)

    async def test_cancel_before_fake_never_starts(self):
        response = await self.client.post("/bridge/v1/tasks", json=self.task_request())
        self.assertLess(response.status_code, 300, response.text)
        task_id = response.json()["remote_task_id"]
        response = await self.client.post(f"/bridge/v1/tasks/{task_id}/cancel")
        self.assertLess(response.status_code, 300, response.text)
        with self.assertRaises(DomainError):
            run_fake_once(self.store, self.scope, task_id, node_id="fixture-node", executor_instance="instance-1", start_log=self.starts)
        self.assertFalse(self.starts.exists())

    def test_fixture_limits_do_not_accept_bool(self):
        with self.assertRaises(ValueError):
            fixture_png(True, 16)
        with self.assertRaises(ValueError):
            fixture_png(1025, 16)
