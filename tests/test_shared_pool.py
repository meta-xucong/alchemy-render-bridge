"""Synthetic opt-in pool tests; never invoke a real provider or user media."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from render_bridge.agent.pool_client import PoolNodeHTTPClient
from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.agent.runner import NodeAgent
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.executors.fake import FakeExecutor
from render_bridge.fake import fixture_png
from render_bridge.runtime import RuntimeConfig
from render_bridge.api import create_app
from render_bridge.shared_pool import SharedNodeIdentity


def task_payload(origin, now, prompt="Synthetic shared-pool fixture", profile_id="fake-image"):
    from render_bridge.profiles import get_profile
    profile = get_profile(profile_id)
    return {
        "schema_version": "1", "origin_key": origin, "binding_id": "binding-fixture",
        "operation": "image.generate", "execution_profile_id": profile.id,
        "execution_profile_revision": profile.revision, "prompt": prompt,
        "parameters": {"width": 32, "height": 32}, "references": [],
        "output_slot": {"slot_id": "primary", "count": 1}, "routing": "bridge_only",
        "deadline": (datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=1)).isoformat(),
    }


class SyncASGITransport(httpx.BaseTransport):
    """Bridge a synchronous NodeAgent client to the in-process ASGI fixture."""
    def __init__(self, app):
        self.app = app

    def handle_request(self, request):
        import asyncio
        import threading
        content = request.read()
        result = {}
        async def dispatch():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="http://127.0.0.1") as client:
                response = await client.request(request.method, str(request.url),
                                                headers=request.headers, content=content)
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=response.content, request=request)
        def run():
            try:
                result["response"] = asyncio.run(dispatch())
            except BaseException as exc:
                result["error"] = exc
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result["response"]

    def close(self):
        return None


class FirstChoice:
    def choice(self, values):
        return values[0]


class SharedPoolStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.pool_rng = FirstChoice()
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects",
                                 clock=lambda: self.now, dispatch_mode="node_pull",
                                 enabled_profiles=("fake-image", "midjourney-manual"),
                                 max_queued_tasks=100, shared_pool_enabled=True,
                                 pool_rng=self.pool_rng)
        self.pool = self.store.create_shared_pool("fixture pool")["pool_id"]
        self.scopes = []
        self.identities = []

    def add_workspace(self, index, *, scope=None, pool_id=None):
        scope = scope or SourceScope(f"source-{index}", f"workspace-{index}")
        credential = self.store.create_shared_source_credential(scope.source_id, scope.workspace_id)
        invite = self.store.create_shared_invite(pool_id or self.pool, "workspace")
        joined = self.store.accept_shared_invite(scope, invite["invite_code"])
        self.assertEqual(joined["membership"], "active")
        self.scopes.append(scope)
        return scope, credential["source_token"]

    def add_node(self, index, profiles=("fake-image",), *, pool_id=None,
                 node_id=None, executor_instance=None):
        profiles = list(profiles)
        invite = self.store.create_shared_invite(pool_id or self.pool, "node", profiles=profiles)
        paired = self.store.enroll_shared_node(invite["invite_code"], node_id or f"node-{index}",
                                              executor_instance or f"executor-{index}", profiles,
                                              accept_shared_data=True)
        identity = self.store.shared_node_from_token(paired["node_token"])
        reports = [{"profile_id": profile_id, "profile_revision": "1", "report_revision": 1,
                    "health": "available", "reason_code": "ready",
                    "evidence_kind": "operator_attested_preflight"}
                   for profile_id in profiles if profile_id != "fake-image"]
        self.store.report_shared_heartbeat(identity, "available", profiles,
                                           profile_health=reports or None)
        self.identities.append(identity)
        return identity, paired["node_token"]

    def create_task(self, scope, origin):
        return self.store.create_task(scope, task_payload(origin, self.now))["remote_task_id"]

    def collect_assignments(self, task_ids):
        found = {}
        for _ in range(20):
            for identity in self.identities:
                result = self.store.next_shared_assignment(identity)["assignment"]
                if result:
                    found[result["task_id"]] = (identity, result["assignment_id"])
            if set(found) == set(task_ids):
                return found
        self.fail(f"Not all tasks were assigned: {set(found)}")

    def test_three_workspaces_and_nodes_start_concurrently_without_duplicate_assignment(self):
        scopes = [self.add_workspace(i)[0] for i in range(3)]
        for i in range(3):
            self.add_node(i)
        tasks = [self.create_task(scope, f"task-{i}") for i, scope in enumerate(scopes)]
        assignments = self.collect_assignments(tasks)
        self.assertEqual(set(assignments), set(tasks))
        with self.store._connection() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM shared_assignments WHERE state IN ('assigned','claimed','running')").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT COUNT(DISTINCT task_id) FROM shared_assignments WHERE state IN ('assigned','claimed','running')").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT COUNT(DISTINCT node_id) FROM shared_assignments WHERE state='assigned'").fetchone()[0], 3)

        def start(item):
            identity, assignment_id = item
            claim = self.store.claim_shared_assignment(identity, assignment_id)
            return self.store.start_shared_execution(identity, assignment_id, claim["claim_id"])

        with ThreadPoolExecutor(max_workers=3) as executor:
            grants = list(executor.map(start, assignments.values()))
        self.assertTrue(all(item["may_execute"] for item in grants))
        self.assertEqual(len({item["start_grant_id"] for item in grants}), 3)
        self.assertEqual(self.store.summary_shared_pool()["slots_used"], 3)

    def test_shared_schema_v1_migrates_manual_assignment_slot_index(self):
        db = self.root / "ledger.db"
        objects = self.root / "objects"
        with self.store._transaction() as con:
            con.execute("DROP INDEX shared_one_active_assignment_per_slot")
            con.execute("CREATE UNIQUE INDEX shared_one_active_assignment_per_slot "
                "ON shared_assignments(pool_id,node_id,executor,slot) "
                "WHERE state IN ('assigned','claimed','running','execution_unknown','awaiting_user')")
            con.execute("DROP INDEX IF EXISTS shared_task_queue_by_workspace")
            con.execute("DELETE FROM shared_schema_version WHERE version>=2")
            con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(1,?)", (self.now,))
        self.store.close()
        migrated = BridgeStore(db, objects, clock=lambda: self.now, dispatch_mode="node_pull",
                               enabled_profiles=("fake-image", "midjourney-manual"),
                               max_queued_tasks=100, shared_pool_enabled=True, pool_rng=self.pool_rng)
        self.addCleanup(migrated.close)
        with migrated._connection() as con:
            index_sql = con.execute("SELECT sql FROM sqlite_master WHERE name='shared_one_active_assignment_per_slot'").fetchone()[0]
            versions = {row[0] for row in con.execute("SELECT version FROM shared_schema_version")}
        self.assertNotIn("'awaiting_user'", index_sql)
        self.assertIn(2, versions)
        self.assertIn(3, versions)
        self.assertIn(4, versions)
        with migrated._connection() as con:
            self.assertIsNotNone(con.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                                              "AND name='shared_task_queue_by_workspace'").fetchone())

    def test_random_choice_sees_only_eligible_nodes_and_assignment_is_persisted_once(self):
        scope, _token = self.add_workspace(1)
        first, _ = self.add_node(1)
        second, _ = self.add_node(2)
        task_id = self.create_task(scope, "random-task")
        result = self.store.next_shared_assignment(first)
        self.assertIsNotNone(result["assignment"])
        assignment_id = result["assignment"]["assignment_id"]
        # Polls by every node return no competing assignment; they cannot reinsert
        # the already assigned task or trip the partial unique index.
        for identity in (first, second, first, second):
            self.store.next_shared_assignment(identity)
        with self.store._connection() as con:
            rows = con.execute("SELECT assignment_id,node_id FROM shared_assignments WHERE task_id=?",
                               (task_id,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["assignment_id"], assignment_id)
        self.assertIn(rows[0]["node_id"], {first.node_id, second.node_id})

    def test_assignment_response_requires_pool_node_executor_identity(self):
        other_pool = self.store.create_shared_pool("second fixture pool")["pool_id"]
        self.add_workspace(1, pool_id=self.pool)
        second_scope, _token = self.add_workspace(2, pool_id=other_pool)
        duplicate_node = "shared-node-name"
        duplicate_executor = "shared-executor-name"
        first, _ = self.add_node(1, pool_id=self.pool, node_id=duplicate_node,
                                 executor_instance=duplicate_executor)
        second, _ = self.add_node(2, pool_id=other_pool, node_id=duplicate_node,
                                  executor_instance=duplicate_executor)
        task_id = self.create_task(second_scope, "second-pool-only")

        # The first poll may run the scheduler, but must not receive another
        # pool's assignment even when the human-readable node names collide.
        self.assertIsNone(self.store.next_shared_assignment(first)["assignment"])
        assignment = self.store.next_shared_assignment(second)["assignment"]
        self.assertEqual(assignment["task_id"], task_id)
        self.assertEqual(assignment["pool_id"], other_pool)

    def test_pre_membership_legacy_task_is_not_scheduled_or_in_pool_history(self):
        scope = SourceScope("source-before-consent", "workspace-before-consent")
        legacy = BridgeStore(self.root / "ledger.db", self.root / "objects",
            clock=lambda: self.now, dispatch_mode="hatchet",
            enabled_profiles=("fake-image", "midjourney-manual"), max_queued_tasks=100)
        historical_task = legacy.create_task(scope, task_payload("legacy-before-join", self.now))["remote_task_id"]
        legacy.close()
        self.store.close()
        # Simulate an existing v2 database upgrade: the new table is absent and
        # the migration must not infer consent for old task rows.
        con = sqlite3.connect(self.root / "ledger.db")
        try:
            con.execute("DROP TABLE shared_task_pools")
            con.execute("DROP INDEX IF EXISTS shared_task_queue_by_workspace")
            con.execute("DELETE FROM shared_schema_version WHERE version>=3")
            con.commit()
        finally:
            con.close()
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects",
            clock=lambda: self.now, dispatch_mode="node_pull",
            enabled_profiles=("fake-image", "midjourney-manual"), max_queued_tasks=100,
            shared_pool_enabled=True, pool_rng=self.pool_rng)
        self.addCleanup(self.store.close)
        self.add_workspace(3, scope=scope)
        identity, _ = self.add_node(3)
        with self.assertRaises(DomainError) as not_admitted:
            self.store.create_task(scope, task_payload("legacy-before-join", self.now))
        self.assertEqual(not_admitted.exception.code, "task_not_shared_pool_admitted")
        new_task = self.create_task(scope, "shared-after-join")

        operator_page = self.store.shared_task_history(limit=20)
        source_page = self.store.shared_task_history(scope=scope, limit=20)
        self.assertEqual([item["task_id"] for item in operator_page["items"]], [new_task])
        self.assertEqual([item["task_id"] for item in source_page["items"]], [new_task])
        self.assertEqual(self.store.summary_shared_pool()["queued_tasks"], 1)
        with self.assertRaises(DomainError):
            self.store.shared_task_detail(historical_task)
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        self.assertEqual(assignment["task_id"], new_task)

    def test_queue_summary_counts_only_admitted_tasks_once_across_pools(self):
        scope, _token = self.add_workspace(1)
        self.add_node(1)
        second_pool = self.store.create_shared_pool("second summary pool")["pool_id"]
        second_invite = self.store.create_shared_invite(second_pool, "workspace")
        self.store.accept_shared_invite(scope, second_invite["invite_code"])
        self.create_task(scope, "multi-pool-summary")
        summary = self.store.summary_shared_pool()
        self.assertEqual(summary["queued_tasks"], 1)
        self.assertEqual(summary["workspaces_joined"], 1)

    def test_scheduler_reaches_runnable_workspace_after_full_blocked_queue(self):
        self.store.max_queued_tasks = 10_000
        blocked_scope, _ = self.add_workspace(1)
        blocked_identity, _ = self.add_node(1)
        payload = json.dumps(task_payload("bulk", self.now), sort_keys=True, separators=(",", ":"))
        rows = []
        attempts = []
        admissions = []
        for index in range(10_000):
            task_id = f"bulk-blocked-{index:05d}"
            attempt_id = f"attempt-{index:05d}"
            rows.append((task_id, blocked_scope.source_id, blocked_scope.workspace_id,
                         f"bulk-{index:05d}", hashlib.sha256(task_id.encode()).hexdigest(),
                         payload, "queued", attempt_id, 0, self.now, self.now + 3600))
            attempts.append((attempt_id, task_id))
            admissions.append((task_id, self.pool, self.now))
        with self.store._transaction() as con:
            con.executemany("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.executemany("INSERT INTO attempts(id,task_id) VALUES(?,?)", attempts)
            con.executemany("INSERT INTO shared_task_pools(task_id,pool_id,admitted) VALUES(?,?,?)", admissions)
        self.store.set_shared_node_state(self.pool, blocked_identity.node_id,
                                         blocked_identity.executor_instance, "draining")

        runnable_pool = self.store.create_shared_pool("runnable pool")["pool_id"]
        self.now += 1
        runnable_scope, _ = self.add_workspace(2, pool_id=runnable_pool)
        runnable_identity, _ = self.add_node(2, pool_id=runnable_pool)
        runnable_task = self.create_task(runnable_scope, "after-full-blocked-queue")
        assignment = self.store.next_shared_assignment(runnable_identity)["assignment"]
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment["task_id"], runnable_task)

    def test_unavailable_profile_head_does_not_block_later_runnable_task(self):
        scope, _ = self.add_workspace(1)
        manual_identity, _ = self.add_node(1, ("midjourney-manual",))
        blocked_task = self.store.create_task(scope, task_payload(
            "manual-head", self.now, profile_id="midjourney-manual"))["remote_task_id"]
        fake_identity, _ = self.add_node(2)
        runnable_task = self.create_task(scope, "fake-after-manual-head")
        self.store.set_shared_node_state(self.pool, manual_identity.node_id,
                                         manual_identity.executor_instance, "draining")
        assignment = self.store.next_shared_assignment(fake_identity)["assignment"]
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment["task_id"], runnable_task)
        self.assertNotEqual(assignment["task_id"], blocked_task)

    def test_history_cursor_and_filter_lengths_are_bounded(self):
        with self.assertRaises(DomainError) as oversized_cursor:
            cursor = base64.urlsafe_b64encode(b"1800000000|" + b"a" * 600).decode("ascii").rstrip("=")
            self.store.shared_task_history(cursor=cursor)
        self.assertEqual(oversized_cursor.exception.code, "invalid_cursor")
        with self.assertRaises(DomainError) as oversized_node:
            self.store.shared_task_history(node_id="n" * 129)
        self.assertEqual(oversized_node.exception.code, "invalid_filter")

    def test_workspace_leave_blocks_new_work_and_releases_prestart_assignment(self):
        scope, _token = self.add_workspace(1)
        identity, _ = self.add_node(1)
        task = self.create_task(scope, "leave-task")
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        self.store.leave_shared_pool(scope, self.pool)
        safe_view = self.store.get_assigned_task(identity, assignment["assignment_id"], task)
        self.assertNotIn("request", safe_view)
        self.assertEqual(safe_view["assignment_state"], "released")
        with self.assertRaises(DomainError):
            self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        self.store.next_shared_assignment(identity)
        with self.store._connection() as con:
            row = con.execute("SELECT state FROM shared_assignments WHERE assignment_id=?",
                              (assignment["assignment_id"],)).fetchone()
        self.assertEqual(row["state"], "released")
        with self.store._connection() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM claims WHERE task_id=?", (task,)).fetchone())
        with self.assertRaises(DomainError) as error:
            self.store.create_task(scope, task_payload("after-leave", self.now))
        self.assertEqual(error.exception.code, "pool_membership_required")

    def test_expired_prestart_cancel_releases_assignment_immediately(self):
        scope, _token = self.add_workspace(1)
        identity, _ = self.add_node(1)
        task_id = self.create_task(scope, "expired-task")
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        self.now += 3601
        task = self.store.cancel_task(scope, task_id)
        self.assertEqual(task["state"], "expired")
        with self.store._connection() as con:
            row = con.execute("SELECT state FROM shared_assignments WHERE assignment_id=?",
                              (assignment["assignment_id"],)).fetchone()
        self.assertEqual(row["state"], "expired")

    def test_history_cursor_preserves_sub_microsecond_ordering(self):
        scope, _token = self.add_workspace(1)
        self.add_node(1)
        task_ids = []
        for index in range(12):
            self.now = 1_800_000_000.0 + index * 0.0000004
            task_ids.append(self.create_task(scope, f"cursor-{index}"))
        received = []
        cursor = None
        while True:
            page = self.store.shared_task_history(scope=scope, cursor=cursor, limit=2)
            received.extend(item["task_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(received), len(set(received)))
        self.assertEqual(set(received), set(task_ids))

    def test_withdrawal_releases_assignment_and_redacts_node_task_view(self):
        scope, _token = self.add_workspace(1)
        identity, _ = self.add_node(1)
        task_id = self.create_task(scope, "withdraw-task")
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        task = self.store.get_task(scope, task_id)
        proof = {"schema_version": "1", "withdrawal_key": "withdraw-1",
                 "origin_key": "withdraw-task", "attempt_id": task["attempt_id"],
                 "task_digest": task["task_digest"]}
        self.store.withdraw_before_start(scope, task_id, proof)
        safe_view = self.store.get_assigned_task(identity, assignment["assignment_id"], task_id)
        self.assertEqual(safe_view["state"], "canceled")
        self.assertNotIn("request", safe_view)
        with self.assertRaises(DomainError):
            self.store.assigned_input_content(identity, assignment["assignment_id"],
                                              task_id, "missing-input", "stale-claim")

    def test_capabilities_only_report_calling_workspace_usage(self):
        first, _ = self.add_workspace(1)
        second, _ = self.add_workspace(2)
        self.add_node(1)
        first_task = self.create_task(first, "cap-a")
        second_task = self.create_task(second, "cap-b")
        self.store.leave_shared_pool(second, self.pool)
        with self.store._transaction() as con:
            con.execute("INSERT INTO inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("in-a", first.source_id, first.workspace_id, "input-a", "a"*64, "{}",
                 "ready", self.now + 3600, 777, "input-a.blob", "{}"))
            con.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("up-b", second_task, con.execute("SELECT attempt_id FROM tasks WHERE id=?", (second_task,)).fetchone()[0],
                 "primary", "b"*64, "{}", "committed", 333, "upload-b.blob", "{}"))
        first_cap = self.store.capabilities(first)
        second_cap = self.store.capabilities(second)
        self.assertEqual(first_cap["queue"]["active_tasks"], 1)
        self.assertEqual(second_cap["queue"]["active_tasks"], 1)
        self.assertEqual(first_cap["storage"]["used_bytes"], 777)
        self.assertEqual(second_cap["storage"]["used_bytes"], 333)

    def test_manual_import_flow_completes_on_same_pooled_assignment(self):
        scope, _token = self.add_workspace(1)
        identity, node_token = self.add_node(1, ("midjourney-manual",))
        task_id = self.store.create_task(scope, task_payload("manual-task", self.now,
            profile_id="midjourney-manual"))["remote_task_id"]
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        claim = self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        prepared = self.store.manual_ready_shared(identity, assignment["assignment_id"], claim["claim_id"])
        self.assertEqual(prepared["state"], "awaiting_user")
        with self.assertRaises(DomainError):
            self.store.claim_shared_assignment(identity, assignment["assignment_id"], mode="execute")
        second_task = self.store.create_task(scope, task_payload("manual-task-2", self.now,
            profile_id="midjourney-manual"))["remote_task_id"]
        second_assignment = self.store.next_shared_assignment(identity)["assignment"]
        self.assertEqual(second_assignment["task_id"], second_task)
        self.assertEqual(self.store.shared_node_from_token(node_token), identity)

    def test_manual_assignment_deadline_expires_without_unknowning_execution(self):
        scope, _token = self.add_workspace(1)
        identity, _ = self.add_node(1, ("midjourney-manual",))
        task_id = self.store.create_task(scope, task_payload("manual-expiry", self.now,
            profile_id="midjourney-manual"))["remote_task_id"]
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        claim = self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        self.store.manual_ready_shared(identity, assignment["assignment_id"], claim["claim_id"])
        self.now += 3601
        self.store.next_shared_assignment(identity)
        with self.store._connection() as con:
            task = con.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
            row = con.execute("SELECT state FROM shared_assignments WHERE assignment_id=?",
                              (assignment["assignment_id"],)).fetchone()
        self.assertEqual(task["state"], "expired")
        self.assertEqual(row["state"], "expired")

    def test_revoked_node_cannot_claim_but_started_task_can_finish_same_attempt(self):
        scope, _token = self.add_workspace(1)
        identity, _ = self.add_node(1)
        task_id = self.create_task(scope, "revoke-task")
        assignment = self.store.next_shared_assignment(identity)["assignment"]
        claim = self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        grant = self.store.start_shared_execution(identity, assignment["assignment_id"], claim["claim_id"])
        self.store.set_shared_node_state(self.pool, identity.node_id, identity.executor_instance, "revoked")
        with self.assertRaises(DomainError):
            self.store.claim_shared_assignment(identity, assignment["assignment_id"])
        content = fixture_png(32, 32)
        upload = self.store.create_assigned_upload(identity, assignment["assignment_id"], task_id,
            claim["claim_id"], {"attempt_id": grant["attempt_id"], "slot_id": "primary",
                                 "size": len(content), "mime_type": "image/png"})
        self.store.put_assigned_upload_content(identity, assignment["assignment_id"],
            upload["upload_id"], claim["claim_id"], content)
        receipt = self.store.commit_assigned_upload(identity, assignment["assignment_id"],
            upload["upload_id"], claim["claim_id"])
        self.assertEqual(receipt["task_id"], task_id)
        self.assertEqual(self.store.summary_shared_pool()["slots_used"], 0)


class SharedPoolAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.store = BridgeStore(self.root / "ledger.db", self.root / "objects",
                                 clock=lambda: self.now, dispatch_mode="node_pull",
                                 enabled_profiles=("fake-image", "midjourney-manual"),
                                 max_queued_tasks=100, shared_pool_enabled=True)
        self.app = create_app(store=self.store, source_tokens={}, shared_pool_enabled=True,
                              control_token="operator-test", store_concurrency=16)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.close()

    async def req(self, method, path, token="operator-test", **kwargs):
        headers = {"Authorization": "Bearer " + token, **kwargs.pop("headers", {})}
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def test_console_auth_consent_pair_task_run_artifact_and_history_privacy(self):
        page = await self.client.get("/bridge/console")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Content-Security-Policy", page.headers)
        self.assertNotIn("operator-test", page.text)
        self.assertNotIn("localStorage", page.text)
        self.assertIn("--state-dir ./renderbridge-node", page.text)
        self.assertEqual((await self.req("GET", "/bridge/control/v2/summary", token="wrong")).status_code, 401)

        pool = (await self.req("POST", "/bridge/control/v2/pools", json={"name": "api fixture"})).json()
        workspace = SourceScope("api-source", "api-workspace")
        source_token = (await self.req("POST", "/bridge/control/v2/source-credentials",
            json={"source_id": workspace.source_id, "workspace_id": workspace.workspace_id})).json()["source_token"]
        workspace_invite = (await self.req("POST", f"/bridge/control/v2/pools/{pool['pool_id']}/invites",
            json={"kind": "workspace"})).json()["invite_code"]
        inspect = await self.req("POST", "/bridge/source/v2/pool-invites/inspect", token=source_token,
                                 json={"invite_code": workspace_invite})
        self.assertEqual(inspect.status_code, 200)
        self.assertIn("reference images", inspect.json()["sharing_disclosure"])
        refused = await self.req("POST", "/bridge/source/v2/pool-invites/accept", token=source_token,
                                 json={"invite_code": workspace_invite, "accept_shared_data": False})
        self.assertEqual(refused.status_code, 403)
        joined = await self.req("POST", "/bridge/source/v2/pool-invites/accept", token=source_token,
                                json={"invite_code": workspace_invite, "accept_shared_data": True})
        self.assertEqual(joined.status_code, 200)
        memberships = await self.req("GET", "/bridge/source/v2/pools", token=source_token)
        self.assertEqual(memberships.json()["pools"][0]["membership"], "active")

        node_invite = (await self.req("POST", f"/bridge/control/v2/pools/{pool['pool_id']}/invites",
            json={"kind": "node", "profiles": ["fake-image"]})).json()["invite_code"]
        enrolled = await self.client.post("/bridge/node/v2/enroll", json={
            "invite_code": node_invite, "node_id": "api-node", "executor_instance": "api-executor",
            "profiles": ["fake-image"], "accept_shared_data": True})
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        node_token = enrolled.json()["node_token"]
        self.assertEqual((await self.req("POST", "/bridge/node/v2/heartbeat", token=node_token,
                                         json={"state": "available", "profiles": ["fake-image"]})).status_code, 200)

        secret_prompt = "Synthetic prompt secret that must not appear in operator history"
        task_response = await self.req("POST", "/bridge/v1/tasks", token=source_token,
                                       json=task_payload("api-task", self.now, secret_prompt))
        self.assertEqual(task_response.status_code, 200, task_response.text)
        task_id = task_response.json()["remote_task_id"]
        identity = self.store.shared_node_from_token(node_token)
        client = PoolNodeHTTPClient("http://127.0.0.1", node_token,
            transport=SyncASGITransport(self.app))
        registry = ExecutorRegistry({"fake-image": FakeExecutor(self.root / "executor")})
        agent = NodeAgent(client, self.root / "agent", registry=registry)
        agent._worker_running = True
        try:
            offered = client.next_task()
            self.assertEqual(offered, task_id)
            result = agent.execute_task(task_id)
        finally:
            client.close()
        self.assertEqual(result["outcome"], "artifact_committed")
        read = await self.req("GET", "/bridge/v1/tasks/" + task_id, token=source_token)
        self.assertEqual(read.status_code, 200)
        receipt_id = read.json()["receipt_id"]
        receipt = (await self.req("GET", "/bridge/v1/receipts/" + receipt_id,
                                  token=source_token)).json()
        artifact_response = await self.req("GET", "/bridge/v1/artifacts/" + receipt["artifact_id"]
                                           + "/content", token=source_token)
        self.assertEqual(artifact_response.status_code, 200)
        history = await self.req("GET", "/bridge/control/v2/tasks", params={"limit": 20})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"][0]["task_id"], task_id)
        detail = (await self.req("GET", "/bridge/control/v2/tasks/" + task_id)).json()
        self.assertEqual(detail["status"], "artifacts_ready")
        serialized = json.dumps({"history": history.json(), "detail": detail}, ensure_ascii=False)
        self.assertNotIn(secret_prompt, serialized)
        self.assertNotIn(source_token, serialized)
        self.assertNotIn(node_token, serialized)
        self.assertEqual(identity.node_id, "api-node")
        self.assertEqual((await self.req("POST", f"/bridge/control/v2/pools/{pool['pool_id']}"
            f"/nodes/{identity.node_id}/{identity.executor_instance}/drain")).status_code, 200)
        self.assertEqual((await self.req("POST", f"/bridge/source/v2/pools/{pool['pool_id']}/leave",
            token=source_token, json={})).status_code, 200)
        memberships = await self.req("GET", "/bridge/source/v2/pools", token=source_token)
        self.assertEqual(memberships.json()["pools"][0]["membership"], "revoked")

    async def test_operator_history_rejects_oversized_cursor_and_filters(self):
        cursor = base64.urlsafe_b64encode(b"1800000000|" + b"a" * 600).decode("ascii").rstrip("=")
        oversized_cursor = await self.req("GET", "/bridge/control/v2/tasks",
                                          params={"cursor": cursor})
        oversized_filter = await self.req("GET", "/bridge/control/v2/tasks",
                                          params={"workspace_id": "w" * 129})
        self.assertEqual(oversized_cursor.status_code, 422)
        self.assertEqual(oversized_filter.status_code, 422)

    async def test_pooled_manual_profile_can_export_and_import_original(self):
        from render_bridge.executors.manual import MidjourneyManualExecutor
        scope = SourceScope("manual-source", "manual-workspace")
        source_token = (await self.req("POST", "/bridge/control/v2/source-credentials",
            json={"source_id": scope.source_id, "workspace_id": scope.workspace_id})).json()["source_token"]
        pool = (await self.req("POST", "/bridge/control/v2/pools", json={"name": "manual"})).json()
        invite = (await self.req("POST", f"/bridge/control/v2/pools/{pool['pool_id']}/invites",
            json={"kind": "workspace"})).json()["invite_code"]
        await self.req("POST", "/bridge/source/v2/pool-invites/accept", token=source_token,
            json={"invite_code": invite, "accept_shared_data": True})
        node_invite = (await self.req("POST", f"/bridge/control/v2/pools/{pool['pool_id']}/invites",
            json={"kind": "node", "profiles": ["midjourney-manual"]})).json()["invite_code"]
        enrolled = await self.client.post("/bridge/node/v2/enroll", json={
            "invite_code": node_invite, "node_id": "manual-node", "executor_instance": "manual-executor",
            "profiles": ["midjourney-manual"], "accept_shared_data": True})
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        node_token = enrolled.json()["node_token"]
        report = [{"profile_id": "midjourney-manual", "profile_revision": "1", "report_revision": 1,
                   "health": "available", "reason_code": "ready",
                   "evidence_kind": "operator_attested_preflight"}]
        heartbeat = await self.client.post("/bridge/node/v2/heartbeat",
            headers={"Authorization": "Bearer " + node_token},
            json={"state": "available", "profiles": ["midjourney-manual"], "profile_health": report})
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        task = await self.req("POST", "/bridge/v1/tasks", token=source_token,
            json=task_payload("manual-api", self.now, profile_id="midjourney-manual"))
        self.assertEqual(task.status_code, 200, task.text)
        task_id = task.json()["remote_task_id"]
        client = PoolNodeHTTPClient("http://127.0.0.1", node_token,
            transport=SyncASGITransport(self.app))
        registry = ExecutorRegistry({"midjourney-manual": MidjourneyManualExecutor(self.root / "executor")})
        agent = NodeAgent(client, self.root / "manual-agent", registry=registry)
        agent._worker_running = True
        try:
            self.assertEqual(client.next_task(), task_id)
            prepared = agent.execute_task(task_id)
            self.assertEqual(prepared["outcome"], "awaiting_user")
            self.assertTrue(Path(prepared["prompt_file"]).is_file())
            self.assertEqual(client.task(task_id)["state"], "awaiting_user")
            original = self.root / "manual-original.png"
            original.write_bytes(fixture_png(32, 32))
            result = agent.manual_import(task_id, original,
                confirmed_request_sha256=prepared["request_sha256"], confirmed_single_original=True)
        finally:
            client.close()
        self.assertEqual(result["outcome"], "artifact_committed")
        remote = await self.req("GET", "/bridge/v1/tasks/" + task_id, token=source_token)
        self.assertEqual(remote.json()["state"], "artifacts_ready")

    async def test_shared_runtime_configuration_is_explicit_and_bounded(self):
        config = RuntimeConfig(Path("unused"), "unused", "unused", "unused", "unused",
                               dispatch_mode="node_pull", shared_pool_enabled=True,
                               store_concurrency=16)
        self.assertEqual(config.credentials({"BRIDGE_CONTROL_TOKEN": "control-test"}), ["control-test"])
        with self.assertRaises(ValueError):
            config.credentials({})
        with self.assertRaises(ValueError):
            RuntimeConfig(Path("unused"), "s", "w", "n", "e", dispatch_mode="hatchet",
                          shared_pool_enabled=True, store_concurrency=16).validate()
        with self.assertRaises(ValueError):
            RuntimeConfig(Path("unused"), "s", "w", "n", "e", dispatch_mode="node_pull",
                          shared_pool_enabled=True, store_concurrency=1).validate()


if __name__ == "__main__":
    unittest.main()

\r\n