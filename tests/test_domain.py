import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from render_bridge.domain import BridgeStore, DomainError, SourceScope


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.scope = SourceScope("source-a", "workspace-a")
        self.other_source = SourceScope("source-b", "workspace-a")
        self.other_workspace = SourceScope("source-a", "workspace-b")
        self.store = self.reopen()

    def reopen(self):
        return BridgeStore(self.root / "ledger.db", self.root / "objects", clock=lambda: self.now)

    def payload(self, origin="origin-one", references=None):
        return {
            "schema_version": "1", "origin_key": origin, "binding_id": "opaque-binding",
            "operation": "image.generate", "execution_profile_id": "fake-image",
            "execution_profile_revision": "1", "prompt": "合成图片\nSynthetic\tfixture\r\n",
            "parameters": {"width": 32, "height": 32},
            "references": references or [], "output_slot": {"slot_id": "primary", "count": 1},
            "deadline": datetime.fromtimestamp(self.now + 86400, timezone.utc).isoformat(),
            "routing": "bridge_only",
        }

    @staticmethod
    def png(size=(32, 32)):
        data = io.BytesIO()
        Image.new("RGB", size, "blue").save(data, format="PNG")
        return data.getvalue()

    @staticmethod
    def metadata(data):
        return {"original_sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data), "mime_type": "image/png"}

    def input(self, key="input-one"):
        content = self.png()
        payload = {"input_key": key, **self.metadata(content)}
        reserved = self.store.create_input(self.scope, payload)
        ready = self.store.put_input_content(self.scope, reserved["input_id"], content)
        return ready, content, payload

    def task(self, origin="origin-one"):
        return self.store.create_task(self.scope, self.payload(origin))

    def started(self, origin="origin-one"):
        task = self.task(origin)
        claim = self.store.claim_task(self.scope, task["remote_task_id"],
                                      node_id="node", executor_instance="instance")
        grant = self.store.start_execution(self.scope, task["remote_task_id"], claim["claim_id"])
        return task, claim, grant

    def started_codex(self, origin="codex-origin"):
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects",
                                 clock=lambda: self.now,
                                 enabled_profiles=("fake-image", "codex-image"))
        payload = self.payload(origin)
        payload["execution_profile_id"] = "codex-image"
        payload["execution_profile_revision"] = "1"
        task = self.store.create_task(self.scope, payload)
        claim = self.store.claim_task(self.scope, task["remote_task_id"],
                                      node_id="node", executor_instance="instance")
        grant = self.store.start_execution(self.scope, task["remote_task_id"], claim["claim_id"])
        return task, claim, grant

    def ready_upload(self):
        task, claim, grant = self.started()
        data = self.png()
        payload = {"attempt_id": grant["attempt_id"], "slot_id": "primary", **self.metadata(data)}
        upload = self.store.create_upload(self.scope, task["remote_task_id"], claim["claim_id"], payload)
        self.store.put_upload_content(self.scope, upload["upload_id"], claim["claim_id"], data)
        return task, claim, upload, data

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(DomainError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_full_digest_idempotency_and_reopen(self):
        payload = self.payload()
        task = self.store.create_task(self.scope, payload)
        reordered = dict(reversed(list(payload.items())))
        self.assertEqual(task, self.reopen().create_task(self.scope, reordered))
        self.assertEqual(task, self.reopen().get_task_by_origin(self.scope, payload["origin_key"]))
        changes = {
            "binding_id": "other", "prompt": "different", "parameters": {"width": 31},
            "deadline": datetime.fromtimestamp(self.now + 1000, timezone.utc).isoformat(),
        }
        for key, value in changes.items():
            changed = copy.deepcopy(payload)
            changed[key] = value
            self.assert_error("idempotency_conflict", self.store.create_task, self.scope, changed)
        other = self.store.create_task(self.other_source, payload)
        self.assertNotEqual(task["remote_task_id"], other["remote_task_id"])

    def test_concurrent_create_single_task(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.reopen().create_task(self.scope, self.payload()), range(32)))
        self.assertEqual(1, len({result["remote_task_id"] for result in results}))
        self.assertEqual(1, len({result["attempt_id"] for result in results}))

    def test_configured_queue_limit_is_atomic_and_replay_precedes_capacity(self):
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects", clock=lambda: self.now,
                                 max_queued_tasks=1)
        first = self.store.create_task(self.scope, self.payload("first"))
        self.assertEqual(first, self.store.create_task(self.scope, self.payload("first")))
        self.assert_error("queue_capacity", self.store.create_task, self.scope, self.payload("second"))
        caps = self.store.capabilities(self.scope)
        self.assertEqual({"active_tasks": 1, "max_queued_tasks": 1}, caps["queue"])
        self.assertEqual(1, caps["limits"]["max_queued_tasks"])

    def test_configured_queue_limit_admits_one_of_two_concurrent_new_origins(self):
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects", clock=lambda: self.now,
                                 max_queued_tasks=1)
        barrier = threading.Barrier(2)

        def create(origin):
            barrier.wait()
            try:
                store = BridgeStore(self.root / "ledger.db", self.root / "objects", clock=lambda: self.now,
                                    max_queued_tasks=1)
                return store.create_task(self.scope, self.payload(origin))
            except DomainError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ("first", "second")))
        self.assertEqual(1, sum(not isinstance(item, DomainError) for item in results))
        failures = [item for item in results if isinstance(item, DomainError)]
        self.assertEqual(["queue_capacity"], [item.code for item in failures])

    def test_input_replay_pins_and_cleanup_tombstone(self):
        ready, data, metadata = self.input()
        self.assertEqual(ready["input_id"], self.reopen().create_input(self.scope, metadata)["input_id"])
        self.assertEqual(ready, self.store.put_input_content(self.scope, ready["input_id"], data))
        refs = [{"input_ref": ready["input_id"], "role": "reference", "hash": ready["original_sha256"]}]
        task = self.store.create_task(self.scope, self.payload(references=refs))
        unused, _, unused_metadata = self.input("unused")
        self.now += 3601
        self.assertEqual(1, self.store.cleanup_expired_inputs(self.scope))
        self.assertEqual("ready", self.store.get_input(self.scope, ready["input_id"])["state"])
        self.assertEqual("queued", self.store.get_task(self.scope, task["remote_task_id"])["state"])
        self.assertEqual("expired", self.store.get_input(self.scope, unused["input_id"])["state"])
        self.assertEqual(unused["input_id"], self.store.create_input(self.scope, unused_metadata)["input_id"])
        self.assert_error("input_expired", self.store.put_input_content, self.scope, unused["input_id"], data)
        changed = dict(unused_metadata, original_sha256="f" * 64)
        self.assert_error("idempotency_conflict", self.store.create_input, self.scope, changed)

    def test_inputs_require_ready_scope_hash_and_expiry(self):
        data = self.png()
        reserved = self.store.create_input(self.scope, {"input_key": "x", **self.metadata(data)})
        ref = {"input_ref": reserved["input_id"], "role": "reference", "hash": self.metadata(data)["original_sha256"]}
        self.assert_error("input_not_ready", self.store.create_task, self.scope, self.payload(references=[ref]))
        self.store.put_input_content(self.scope, reserved["input_id"], data)
        self.assert_error("not_found", self.store.create_task, self.other_workspace, self.payload(references=[ref]))
        bad = dict(ref, hash="a" * 64)
        self.assert_error("input_hash_mismatch", self.store.create_task, self.scope, self.payload(references=[bad]))
        self.now += 3601
        self.assert_error("input_expired", self.store.create_task, self.scope, self.payload(references=[ref]))

    def test_references_order_in_digest(self):
        a, _, _ = self.input("a")
        b, _, _ = self.input("b")
        refs = [{"input_ref": row["input_id"], "role": "reference", "hash": row["original_sha256"]}
                for row in (a, b)]
        self.store.create_task(self.scope, self.payload(references=refs))
        self.assert_error("idempotency_conflict", self.store.create_task, self.scope,
                          self.payload(references=list(reversed(refs))))

    def test_media_validation(self):
        data = self.png()
        row = self.store.create_input(self.scope, {"input_key": "x", **self.metadata(data)})
        self.assert_error("size_mismatch", self.store.put_input_content, self.scope, row["input_id"], data[:-1])
        changed = bytes([data[0] ^ 1]) + data[1:]
        self.assert_error("hash_mismatch", self.store.put_input_content, self.scope, row["input_id"], changed)
        invalid = b"not an image"
        bad = self.store.create_input(self.scope, {"input_key": "invalid", **self.metadata(invalid)})
        self.assert_error("invalid_media", self.store.put_input_content, self.scope, bad["input_id"], invalid)
        oversized = self.png((1450, 1450))
        large = self.store.create_input(self.scope, {"input_key": "large", **self.metadata(oversized)})
        self.assert_error("image_too_large", self.store.put_input_content, self.scope, large["input_id"], oversized)

    def test_one_start_under_100_concurrent_redeliveries(self):
        task = self.task()
        claim = self.store.claim_task(self.scope, task["remote_task_id"],
                                      node_id="node", executor_instance="instance")
        with ThreadPoolExecutor(max_workers=10) as pool:
            grants = list(pool.map(lambda _: self.reopen().start_execution(
                self.scope, task["remote_task_id"], claim["claim_id"]), range(100)))
        self.assertEqual(1, sum(grant["may_execute"] for grant in grants))
        self.assertEqual(1, len({grant["start_grant_id"] for grant in grants}))

    def test_reclaim_fences_old_claim_without_new_execution(self):
        task, claim, grant = self.started()
        self.store.mark_execution_unknown(self.scope, task["remote_task_id"], claim["claim_id"])
        self.now += 61
        self.assert_error("execution_already_started", self.reopen().claim_task, self.scope,
                          task["remote_task_id"], node_id="node", executor_instance="new")
        recovery = self.reopen().claim_task(self.scope, task["remote_task_id"],
                                           node_id="node", executor_instance="new", mode="upload_only")
        self.assertEqual(claim["epoch"] + 1, recovery["epoch"])
        self.assert_error("stale_claim", self.store.start_execution, self.scope,
                          task["remote_task_id"], claim["claim_id"])
        replay = self.store.start_execution(self.scope, task["remote_task_id"], recovery["claim_id"])
        self.assertFalse(replay["may_execute"])
        self.assertEqual(grant["start_grant_id"], replay["start_grant_id"])
        self.assertEqual("execution_unknown", self.store.get_task(self.scope, task["remote_task_id"])["state"])

    def test_unstarted_reclaim_can_start_once(self):
        task = self.task()
        first = self.store.claim_task(self.scope, task["remote_task_id"], node_id="node", executor_instance="old")
        self.now += 61
        second = self.store.claim_task(self.scope, task["remote_task_id"], node_id="node", executor_instance="new")
        self.assert_error("stale_claim", self.store.start_execution, self.scope, task["remote_task_id"], first["claim_id"])
        self.assertTrue(self.store.start_execution(self.scope, task["remote_task_id"], second["claim_id"])["may_execute"])

    def test_unknown_and_cancellation_hold_physical_slot(self):
        first, claim, _ = self.started()
        self.store.mark_execution_unknown(self.scope, first["remote_task_id"], claim["claim_id"])
        second = self.task("second")
        other = self.store.claim_task(self.scope, second["remote_task_id"], node_id="node", executor_instance="other")
        self.assert_error("physical_slot_busy", self.store.start_execution, self.scope,
                          second["remote_task_id"], other["claim_id"])
        result = self.store.cancel_task(self.scope, first["remote_task_id"])
        self.assertEqual("cancel_requested", result["state"])
        self.assert_error("physical_slot_busy", self.store.start_execution, self.scope,
                          second["remote_task_id"], other["claim_id"])

    def test_cancel_before_start_is_terminal(self):
        task = self.task()
        claim = self.store.claim_task(self.scope, task["remote_task_id"], node_id="node", executor_instance="x")
        self.assertEqual("canceled", self.store.cancel_task(self.scope, task["remote_task_id"])["state"])
        self.assert_error("task_not_executable", self.store.start_execution, self.scope,
                          task["remote_task_id"], claim["claim_id"])

    def test_receipt_ack_restart_and_scope(self):
        task, claim, upload, data = self.ready_upload()
        receipt = self.store.commit_upload(self.scope, upload["upload_id"], claim["claim_id"])
        self.assertEqual(receipt, self.reopen().commit_upload(self.scope, upload["upload_id"], claim["claim_id"]))
        self.assertEqual((data, "image/png"), self.reopen().get_artifact_content(self.scope, receipt["artifact_id"]))
        ack = {"client_receipt_id": "client-receipt", "decision": "accepted",
               "reason_code": "imported", "original_hash": receipt["original_sha256"],
               "native_registration_ref": "opaque"}
        confirmed = self.store.ack_receipt(self.scope, receipt["receipt_id"], ack)
        self.assertEqual("accepted", confirmed["delivery_ack"])
        self.assertEqual(confirmed, self.reopen().ack_receipt(self.scope, receipt["receipt_id"], ack))
        self.assert_error("idempotency_conflict", self.store.ack_receipt, self.scope,
                          receipt["receipt_id"], dict(ack, decision="rejected"))
        self.assertEqual("artifacts_ready", self.store.cancel_task(self.scope, task["remote_task_id"])["state"])
        for wrong in (self.other_source, self.other_workspace):
            for fn, args in (
                (self.store.get_task, (task["remote_task_id"],)),
                (self.store.cancel_task, (task["remote_task_id"],)),
                (self.store.get_receipt, (receipt["receipt_id"],)),
                (self.store.get_artifact_content, (receipt["artifact_id"],)),
                (self.store.ack_receipt, (receipt["receipt_id"], ack)),
                (self.store.commit_upload, (upload["upload_id"], claim["claim_id"])),
            ):
                self.assert_error("not_found", fn, wrong, *args)

    def test_cancel_first_rejects_commit_and_commit_first_preserves_receipt(self):
        task, claim, upload, _ = self.ready_upload()
        self.store.cancel_task(self.scope, task["remote_task_id"])
        self.assert_error("task_not_accepting", self.store.commit_upload,
                          self.scope, upload["upload_id"], claim["claim_id"])
        self.assertIsNone(self.store.get_task(self.scope, task["remote_task_id"])["receipt_id"])

    def test_cancel_commit_race_linearizes(self):
        task, claim, upload, _ = self.ready_upload()
        barrier = threading.Barrier(2)
        def cancel():
            barrier.wait()
            return self.reopen().cancel_task(self.scope, task["remote_task_id"])
        def commit():
            barrier.wait()
            try:
                return self.reopen().commit_upload(self.scope, upload["upload_id"], claim["claim_id"])
            except DomainError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(cancel), pool.submit(commit)
            a.result()
            outcome = b.result()
        final = self.store.get_task(self.scope, task["remote_task_id"])
        if isinstance(outcome, DomainError):
            self.assertEqual("task_not_accepting", outcome.code)
            self.assertEqual("cancel_requested", final["state"])
            self.assertIsNone(final["receipt_id"])
        else:
            self.assertEqual("artifacts_ready", final["state"])
            self.assertEqual(outcome["receipt_id"], final["receipt_id"])

    def test_upload_only_recovers_same_bytes_not_new_attempt(self):
        task, first, upload, data = self.ready_upload()
        self.now += 61
        recovery = self.store.claim_task(self.scope, task["remote_task_id"],
                                         node_id="node", executor_instance="recovery", mode="upload_only")
        self.assert_error("stale_claim", self.store.commit_upload, self.scope,
                          upload["upload_id"], first["claim_id"])
        receipt = self.store.commit_upload(self.scope, upload["upload_id"], recovery["claim_id"])
        self.assertEqual(task["attempt_id"], receipt["attempt_id"])
        self.assertEqual(data, self.store.get_artifact_content(self.scope, receipt["artifact_id"])[0])

    def test_unknown_codex_attempt_requires_upload_only_claim(self):
        task, execute_claim, grant = self.started_codex()
        self.store.mark_execution_unknown(self.scope, task["remote_task_id"], execute_claim["claim_id"])
        data = self.png()
        payload = {"attempt_id": grant["attempt_id"], "slot_id": "primary", **self.metadata(data)}
        self.assert_error("upload_only_claim_required", self.store.create_upload, self.scope,
                          task["remote_task_id"], execute_claim["claim_id"], payload)

        self.now += 61
        recovery = self.store.claim_task(self.scope, task["remote_task_id"], node_id="node",
                                         executor_instance="instance", mode="upload_only")
        upload = self.store.create_upload(self.scope, task["remote_task_id"], recovery["claim_id"], payload)
        self.store.put_upload_content(self.scope, upload["upload_id"], recovery["claim_id"], data)
        receipt = self.store.commit_upload(self.scope, upload["upload_id"], recovery["claim_id"])
        self.assertEqual(task["attempt_id"], receipt["attempt_id"])

    def test_unknown_codex_attempt_rejects_old_execute_during_existing_upload(self):
        task, execute_claim, grant = self.started_codex()
        data = self.png()
        payload = {"attempt_id": grant["attempt_id"], "slot_id": "primary", **self.metadata(data)}
        upload = self.store.create_upload(self.scope, task["remote_task_id"], execute_claim["claim_id"], payload)
        self.store.put_upload_content(self.scope, upload["upload_id"], execute_claim["claim_id"], data)
        self.store.mark_execution_unknown(self.scope, task["remote_task_id"], execute_claim["claim_id"])
        self.assert_error("upload_only_claim_required", self.store.put_upload_content, self.scope,
                          upload["upload_id"], execute_claim["claim_id"], data)
        self.assert_error("upload_only_claim_required", self.store.commit_upload, self.scope,
                          upload["upload_id"], execute_claim["claim_id"])

    def test_output_slot_attempt_hash_and_dimensions(self):
        task, claim, grant = self.started()
        data = self.png((31, 32))
        payload = {"attempt_id": grant["attempt_id"], "slot_id": "primary", **self.metadata(data)}
        self.assert_error("upload_binding_mismatch", self.store.create_upload, self.scope,
                          task["remote_task_id"], claim["claim_id"], dict(payload, attempt_id="other"))
        self.assert_error("upload_binding_mismatch", self.store.create_upload, self.scope,
                          task["remote_task_id"], claim["claim_id"], dict(payload, slot_id="other"))
        upload = self.store.create_upload(self.scope, task["remote_task_id"], claim["claim_id"], payload)
        self.assert_error("output_dimensions_mismatch", self.store.put_upload_content, self.scope,
                          upload["upload_id"], claim["claim_id"], data)
        self.assert_error("upload_not_ready", self.store.commit_upload, self.scope, upload["upload_id"], claim["claim_id"])

    def test_file_tampering_fails_closed(self):
        _, claim, upload, _ = self.ready_upload()
        receipt = self.store.commit_upload(self.scope, upload["upload_id"], claim["claim_id"])
        target = self.root / "objects" / (upload["upload_id"] + ".blob")
        target.unlink()
        target.symlink_to("/etc/hosts")
        self.assert_error("storage_unavailable", self.store.get_artifact_content, self.scope, receipt["artifact_id"])

    def test_invalid_capabilities_fields_and_nonfinite(self):
        for key, value in (("operation", "shell.exec"), ("execution_profile_revision", "2"),
                           ("routing", "fallback")):
            self.assert_error("unsupported_capability", self.store.create_task, self.scope,
                              dict(self.payload(), **{key: value}))
        bad = self.payload()
        bad["parameters"]["width"] = True
        self.assert_error("invalid_integer", self.store.create_task, self.scope, bad)
        bad["parameters"]["width"] = float("nan")
        self.assert_error("invalid_integer", self.store.create_task, self.scope, bad)
        self.assert_error("invalid_fields", self.store.create_task, self.scope, dict(self.payload(), owner="other"))

    def test_deadline_expiry_and_lease_expiry_are_fail_closed(self):
        task, claim, _ = self.started()
        self.now += 61
        self.assertEqual("execution_unknown", self.reopen().get_task(self.scope, task["remote_task_id"])["state"])
        self.assert_error("claim_expired", self.store.start_execution, self.scope,
                          task["remote_task_id"], claim["claim_id"])
        queued = self.task("queued")
        self.now += 86401
        self.assertEqual("expired", self.store.get_task(self.scope, queued["remote_task_id"])["state"])
        self.assertEqual("expired", self.store.cancel_task(self.scope, queued["remote_task_id"])["state"])
        self.assert_error("task_expired", self.store.claim_task, self.scope, queued["remote_task_id"],
                          node_id="node", executor_instance="new")

    def test_deadline_requires_utc_rfc3339(self):
        for deadline in ("2027-01-15", "2027-01-15 12:00:00+00:00", "2027-01-15T12:00:00+01:00"):
            self.assert_error("invalid_deadline", self.store.create_task, self.scope,
                              dict(self.payload(), deadline=deadline))

    def test_storage_quota_includes_orphaned_files(self):
        from render_bridge.domain import MAX_STORAGE_BYTES
        data = self.png()
        row = self.store.create_input(self.scope, {"input_key": "x", **self.metadata(data)})
        orphan = self.root / "objects" / ".pending-orphan"
        with orphan.open("wb") as stream:
            stream.truncate(MAX_STORAGE_BYTES)
        self.assert_error("storage_capacity", self.store.put_input_content, self.scope, row["input_id"], data)

    def test_accepted_result_releases_slot_for_next_task(self):
        _, claim, upload, _ = self.ready_upload()
        self.store.commit_upload(self.scope, upload["upload_id"], claim["claim_id"])
        _, _, grant = self.started("next")
        self.assertTrue(grant["may_execute"])

    def test_missing_pinned_input_is_not_advertised_ready(self):
        ready, data, _ = self.input()
        path = self.root / "objects" / (ready["input_id"] + ".blob")
        path.unlink()
        self.assert_error("storage_unavailable", self.store.get_input, self.scope, ready["input_id"])
        self.assert_error("storage_unavailable", self.store.put_input_content, self.scope, ready["input_id"], data)
        refs = [{"input_ref": ready["input_id"], "role": "reference", "hash": ready["original_sha256"]}]
        self.assert_error("storage_unavailable", self.store.create_task, self.scope, self.payload(references=refs))

    def test_origin_keys_are_recoverable_path_segments_but_binding_is_opaque(self):
        for origin in ("a/b", ".", "..", "a.b", "中文", "-start", "a b", "", "a" * 129):
            self.assert_error("invalid_origin_key", self.store.create_task, self.scope, self.payload(origin))
            self.assert_error("invalid_origin_key", self.store.get_task_by_origin, self.scope, origin)
        payload = self.payload("Origin_1-safe")
        payload["binding_id"] = "不透明/../native.key"
        task = self.store.create_task(self.scope, payload)
        self.assertEqual(task, self.store.get_task_by_origin(self.scope, "Origin_1-safe"))

    def test_compound_task_read_uses_one_snapshot_during_commit(self):
        task, claim, upload, _ = self.ready_upload()
        original_scoped = self.store._scoped
        writer = self.reopen()
        def commit_after_task_read(con, table, object_id, scope):
            row = original_scoped(con, table, object_id, scope)
            if table == "tasks":
                writer.commit_upload(scope, upload["upload_id"], claim["claim_id"])
            return row
        with patch.object(self.store, "_scoped", side_effect=commit_after_task_read):
            result = self.store.get_task(self.scope, task["remote_task_id"])
        self.assertEqual("uploading", result["state"])
        self.assertIsNone(result["receipt_id"])
        after = self.store.get_task(self.scope, task["remote_task_id"])
        self.assertEqual("artifacts_ready", after["state"])
        self.assertIsNotNone(after["receipt_id"])

    def test_post_rename_rollback_reuses_exact_object_at_full_quota(self):
        from render_bridge.domain import MAX_STORAGE_BYTES
        data = self.png()
        row = self.store.create_input(self.scope, {"input_key": "crash", **self.metadata(data)})
        original_store = self.store._store_bytes
        def crash_after_rename(con, object_id, content):
            original_store(con, object_id, content)
            raise RuntimeError("injected crash before database commit")
        with patch.object(self.store, "_store_bytes", side_effect=crash_after_rename):
            with self.assertRaises(RuntimeError):
                self.store.put_input_content(self.scope, row["input_id"], data)
        self.assertEqual("pending", self.store.get_input(self.scope, row["input_id"])["state"])
        with (self.root / "objects" / ".pending-fill").open("wb") as stream:
            stream.truncate(MAX_STORAGE_BYTES - len(data))
        result = self.reopen().put_input_content(self.scope, row["input_id"], data)
        self.assertEqual("ready", result["state"])
        self.assertEqual(row["input_id"], result["input_id"])

    def test_orphan_reuse_rejects_different_bytes_and_symlink(self):
        data = self.png()
        row = self.store.create_input(self.scope, {"input_key": "crash", **self.metadata(data)})
        target = self.root / "objects" / (row["input_id"] + ".blob")
        target.write_bytes(b"x" * len(data))
        self.assert_error("storage_integrity", self.store.put_input_content, self.scope, row["input_id"], data)
        target.unlink()
        target.symlink_to("/etc/hosts")
        self.assert_error("storage_integrity", self.store.put_input_content, self.scope, row["input_id"], data)

    def test_directory_barrier_is_platform_specific_and_errors_propagate(self):
        with patch("render_bridge.domain.sys.platform", "win32"):
            with patch("render_bridge.domain.os.open") as opened:
                self.store._sync_artifact_directory()
                opened.assert_not_called()
        with patch("render_bridge.domain.sys.platform", "linux"):
            with patch("render_bridge.domain.os.open", return_value=123) as opened:
                with patch("render_bridge.domain.os.fsync") as synced:
                    with patch("render_bridge.domain.os.close") as closed:
                        self.store._sync_artifact_directory()
                        opened.assert_called_once()
                        synced.assert_called_once_with(123)
                        closed.assert_called_once_with(123)
            with patch("render_bridge.domain.os.open", side_effect=PermissionError("open")):
                with self.assertRaises(PermissionError):
                    self.store._sync_artifact_directory()
            with patch("render_bridge.domain.os.open", return_value=123):
                with patch("render_bridge.domain.os.fsync", side_effect=OSError("fsync")):
                    with patch("render_bridge.domain.os.close") as closed:
                        with self.assertRaises(OSError):
                            self.store._sync_artifact_directory()
                        closed.assert_called_once_with(123)

    def test_directory_sync_failure_rolls_back_and_retry_repeats_barrier(self):
        data = self.png()
        row = self.store.create_input(self.scope, {"input_key": "sync", **self.metadata(data)})
        for _ in range(2):
            with patch.object(self.store, "_sync_artifact_directory",
                              side_effect=OSError("injected directory sync failure")) as synced:
                with self.assertRaises(OSError):
                    self.store.put_input_content(self.scope, row["input_id"], data)
                synced.assert_called_once_with()
            self.assertEqual("pending", self.store.get_input(self.scope, row["input_id"])["state"])
            self.assertEqual([], list(self.store.root.glob(".pending-*")))
        with patch.object(self.store, "_sync_artifact_directory",
                          wraps=self.store._sync_artifact_directory) as synced:
            ready = self.store.put_input_content(self.scope, row["input_id"], data)
            synced.assert_called_once_with()
        self.assertEqual("ready", ready["state"])
        self.assertEqual(data, self.store._read_bytes(row["input_id"] + ".blob", self.metadata(data)))

    def test_file_sync_and_rename_failures_abort_without_ready_input(self):
        data = self.png()
        for index, operation in enumerate(("os.fsync", "os.replace")):
            row = self.store.create_input(
                self.scope, {"input_key": f"failure-{index}", **self.metadata(data)})
            with patch(f"render_bridge.domain.{operation}", side_effect=OSError(operation)):
                with self.assertRaises(OSError):
                    self.store.put_input_content(self.scope, row["input_id"], data)
            self.assertEqual("pending", self.store.get_input(self.scope, row["input_id"])["state"])
            self.assertEqual([], list(self.store.root.glob(".pending-*")))
            self.assertFalse((self.store.root / (row["input_id"] + ".blob")).exists())

    def test_read_rejects_symlink_to_identical_bytes_without_nofollow(self):
        ready, data, _ = self.input()
        target = self.store.root / (ready["input_id"] + ".blob")
        other = self.root / "same.png"
        other.write_bytes(data)
        target.unlink()
        target.symlink_to(other)
        with patch("render_bridge.domain.os.O_NOFOLLOW", 0, create=True):
            self.assert_error("storage_unavailable", self.store.get_input,
                              self.scope, ready["input_id"])

    def test_read_rejects_replaced_inode_between_lstat_and_open(self):
        ready, data, _ = self.input()
        target = self.store.root / (ready["input_id"] + ".blob")
        replacement = self.root / "replacement.png"
        replacement.write_bytes(data)
        original_open = os.open
        def swap_then_open(path, flags, *args, **kwargs):
            if Path(path) == target:
                os.replace(replacement, target)
            return original_open(path, flags, *args, **kwargs)
        with patch("render_bridge.domain.os.open", side_effect=swap_then_open):
            self.assert_error("storage_unavailable", self.store.get_input,
                              self.scope, ready["input_id"])


if __name__ == "__main__":
    unittest.main()
