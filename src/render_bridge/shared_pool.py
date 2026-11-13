"""Opt-in shared-pool identity, scheduling and assignment operations.

The shared pool is an additive control plane over the existing task ledger.
All cross-workspace execution is pinned to a persisted assignment and node.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import base64


ACTIVE_ASSIGNMENT_STATES = ("assigned", "claimed", "running", "execution_unknown", "awaiting_user")
ACTIVE_ASSIGNMENT_SQL = "'assigned','claimed','running','execution_unknown','awaiting_user'"
SLOT_HOLDING_STATES = ("assigned", "claimed", "running", "execution_unknown")
SLOT_HOLDING_SQL = "'assigned','claimed','running','execution_unknown'"
POOL_AUDIT_CAPACITY = 10_000


@dataclass(frozen=True)
class SharedNodeIdentity:
    pool_id: str
    node_id: str
    executor_instance: str


class SharedPoolMixin:
    def _init_shared_pool(self, con):
        con.executescript("""
            CREATE TABLE IF NOT EXISTS shared_schema_version (
                version INTEGER PRIMARY KEY, applied REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_pools (
                pool_id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL,
                policy_version TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_workspaces (
                pool_id TEXT NOT NULL, source TEXT NOT NULL, workspace TEXT NOT NULL,
                state TEXT NOT NULL, consented REAL NOT NULL, joined REAL NOT NULL,
                revoked REAL, PRIMARY KEY(pool_id,source,workspace));
            CREATE TABLE IF NOT EXISTS shared_invites (
                code_hash TEXT PRIMARY KEY, pool_id TEXT NOT NULL, kind TEXT NOT NULL,
                profiles TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                consumed REAL, consumed_by TEXT);
            CREATE TABLE IF NOT EXISTS shared_invite_metadata (
                code_hash TEXT PRIMARY KEY, provider_ref TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_source_credentials (
                token_hash TEXT PRIMARY KEY, source TEXT NOT NULL, workspace TEXT NOT NULL,
                created REAL NOT NULL, revoked REAL);
            CREATE TABLE IF NOT EXISTS shared_nodes (
                pool_id TEXT NOT NULL, node_id TEXT NOT NULL, executor TEXT NOT NULL,
                state TEXT NOT NULL, slots INTEGER NOT NULL, allowed_profiles TEXT NOT NULL,
                created REAL NOT NULL, revoked REAL,
                PRIMARY KEY(pool_id,node_id,executor));
            CREATE TABLE IF NOT EXISTS shared_node_metadata (
                pool_id TEXT NOT NULL, node_id TEXT NOT NULL, executor TEXT NOT NULL,
                provider_ref TEXT NOT NULL,
                PRIMARY KEY(pool_id,node_id,executor));
            CREATE TABLE IF NOT EXISTS shared_node_credentials (
                token_hash TEXT PRIMARY KEY, pool_id TEXT NOT NULL, node_id TEXT NOT NULL,
                executor TEXT NOT NULL, created REAL NOT NULL, revoked REAL);
            CREATE TABLE IF NOT EXISTS shared_node_status (
                pool_id TEXT NOT NULL, node_id TEXT NOT NULL, executor TEXT NOT NULL,
                seen REAL NOT NULL, state TEXT NOT NULL, profiles TEXT NOT NULL,
                PRIMARY KEY(pool_id,node_id,executor));
            CREATE TABLE IF NOT EXISTS shared_assignments (
                assignment_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                attempt_id TEXT NOT NULL, pool_id TEXT NOT NULL, node_id TEXT NOT NULL,
                executor TEXT NOT NULL, slot INTEGER NOT NULL, profile TEXT NOT NULL,
                state TEXT NOT NULL, assigned REAL NOT NULL, lease_expires REAL NOT NULL,
                started REAL, released REAL, release_reason TEXT, policy_version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_task_pools (
                task_id TEXT NOT NULL REFERENCES tasks(id), pool_id TEXT NOT NULL REFERENCES shared_pools(pool_id),
                admitted REAL NOT NULL, PRIMARY KEY(task_id,pool_id));
            CREATE TABLE IF NOT EXISTS shared_scheduler_cursor (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), source TEXT, workspace TEXT);
            INSERT OR IGNORE INTO shared_scheduler_cursor VALUES(1,NULL,NULL);
            CREATE TABLE IF NOT EXISTS shared_pool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pool_id TEXT NOT NULL,
                occurred REAL NOT NULL, kind TEXT NOT NULL, source TEXT, workspace TEXT,
                node_id TEXT, task_id TEXT, assignment_id TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS node_execution_ledger (
                attempt_key TEXT PRIMARY KEY, pool_id TEXT NOT NULL, node_id TEXT NOT NULL,
                executor TEXT NOT NULL, profile TEXT NOT NULL, state TEXT NOT NULL,
                started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
                artifact_count INTEGER NOT NULL DEFAULT 0, failure_code TEXT,
                delivery_ack TEXT, delivery_ack_at REAL);
            CREATE INDEX IF NOT EXISTS shared_workspaces_by_scope
                ON shared_workspaces(source,workspace,state,pool_id);
            CREATE INDEX IF NOT EXISTS shared_nodes_by_pool
                ON shared_nodes(pool_id,state,node_id,executor);
            CREATE INDEX IF NOT EXISTS shared_assignments_by_node
                ON shared_assignments(node_id,executor,state,lease_expires);
            CREATE INDEX IF NOT EXISTS shared_assignments_by_task
                ON shared_assignments(task_id,state,assigned);
            CREATE INDEX IF NOT EXISTS shared_task_pools_by_pool
                ON shared_task_pools(pool_id,task_id);
            CREATE UNIQUE INDEX IF NOT EXISTS shared_one_active_assignment_per_task
                ON shared_assignments(task_id)
                WHERE state IN ('assigned','claimed','running','execution_unknown','awaiting_user');
            CREATE UNIQUE INDEX IF NOT EXISTS shared_one_active_assignment_per_slot
                ON shared_assignments(pool_id,node_id,executor,slot)
                WHERE state IN ('assigned','claimed','running','execution_unknown');
            CREATE INDEX IF NOT EXISTS shared_events_by_pool
                ON shared_pool_events(pool_id,id DESC);
        """)
        con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(1,?)",
                    (self.clock(),))
        version = con.execute("SELECT MAX(version) FROM shared_schema_version").fetchone()[0]
        if version < 2:
            # `awaiting_user` manual assignments no longer occupy a physical
            # node slot; keep one active assignment per task but free that slot.
            con.execute("DROP INDEX IF EXISTS shared_one_active_assignment_per_slot")
            con.execute("CREATE UNIQUE INDEX shared_one_active_assignment_per_slot "
                        "ON shared_assignments(pool_id,node_id,executor,slot) "
                        "WHERE state IN ('assigned','claimed','running','execution_unknown')")
            con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(2,?)",
                        (self.clock(),))
        version = con.execute("SELECT MAX(version) FROM shared_schema_version").fetchone()[0]
        if version < 3:
            # Existing legacy tasks stay outside the shared scheduler until an
            # explicit post-consent task creation records its eligible pools.
            con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(3,?)",
                        (self.clock(),))
        if getattr(self, "shared_pool_enabled", False):
            con.execute("CREATE INDEX IF NOT EXISTS shared_task_queue_by_workspace "
                        "ON tasks(source,workspace,state,created,id)")
            version = con.execute("SELECT MAX(version) FROM shared_schema_version").fetchone()[0]
            if version < 4:
                con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(4,?)",
                            (self.clock(),))
            version = con.execute("SELECT MAX(version) FROM shared_schema_version").fetchone()[0]
            if version < 5:
                con.execute("CREATE INDEX IF NOT EXISTS node_execution_by_node_time "
                            "ON node_execution_ledger(pool_id,node_id,executor,started_at DESC)")
                con.execute("INSERT OR IGNORE INTO shared_schema_version(version,applied) VALUES(5,?)",
                            (self.clock(),))

    @staticmethod
    def _shared_hash(token):
        if not isinstance(token, str) or not token or len(token) > 4096:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _record_node_execution_start(self, con, attempt_id, identity, profile, state):
        """Persist one countable start; a digest keeps the raw attempt/task ID out of metrics."""
        key = self._shared_hash(attempt_id)
        now = self.clock()
        con.execute("INSERT OR IGNORE INTO node_execution_ledger "
                    "(attempt_key,pool_id,node_id,executor,profile,state,started_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (key, identity.pool_id, identity.node_id, identity.executor_instance,
                     profile, state, now, now))

    def _update_node_execution(self, con, attempt_id, *, state=None, completed=False,
                               artifact_count=None, failure_code=None):
        key = self._shared_hash(attempt_id)
        values = ["updated_at=?"]
        params = [self.clock()]
        if state is not None:
            values.append("state=?")
            params.append(state)
        if completed:
            values.append("completed_at=COALESCE(completed_at,?)")
            params.append(self.clock())
        if artifact_count is not None:
            values.append("artifact_count=?")
            params.append(artifact_count)
        if failure_code is not None:
            values.append("failure_code=?")
            params.append(failure_code)
        params.append(key)
        con.execute("UPDATE node_execution_ledger SET " + ",".join(values) +
                    " WHERE attempt_key=?", params)

    @staticmethod
    def _pool_scope(pool_id):
        from .domain import SourceScope
        return SourceScope("_shared_pool", pool_id)

    def _pool_event(self, con, pool_id, kind, *, source=None, workspace=None,
                    node_id=None, task_id=None, assignment_id=None, reason=None):
        allowed = {
            "pool_created", "workspace_joined", "workspace_left", "source_credential_created",
            "source_credential_revoked", "node_paired", "node_draining", "node_revoked",
            "node_reactivated", "assignment_created", "assignment_released",
            "execution_unknown", "assignment_artifact_ready",
        }
        if kind not in allowed:
            raise ValueError("Invalid shared-pool event")
        con.execute("INSERT INTO shared_pool_events(pool_id,occurred,kind,source,workspace,node_id,task_id,assignment_id,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pool_id, self.clock(), kind, source, workspace, node_id, task_id, assignment_id, reason))
        con.execute("DELETE FROM shared_pool_events WHERE id NOT IN "
                    "(SELECT id FROM shared_pool_events ORDER BY id DESC LIMIT ?)",
                    (POOL_AUDIT_CAPACITY,))

    def create_shared_pool(self, name):
        from .domain import _fail, _id, _text
        _text(name, 80)
        if not name.strip():
            _fail("invalid_string")
        pool_id = _id("pool")
        with self._transaction() as con:
            con.execute("INSERT INTO shared_pools VALUES(?,?,?,?,?)",
                        (pool_id, name.strip(), 1, "uniform-slot-v1", self.clock()))
            self._pool_event(con, pool_id, "pool_created")
        return {"pool_id": pool_id, "name": name.strip(), "state": "active"}

    def list_shared_pools(self):
        with self._read_transaction() as con:
            rows = con.execute("SELECT pool_id,name,active,policy_version,created FROM shared_pools ORDER BY created,pool_id").fetchall()
        from .domain import _utc
        return [{"pool_id": r["pool_id"], "name": r["name"],
                 "state": "active" if r["active"] else "inactive",
                 "policy_version": r["policy_version"], "created_at": _utc(r["created"])} for r in rows]

    def create_shared_source_credential(self, source, workspace):
        from .domain import _fail, _text
        from .domain import _id
        _text(source); _text(workspace)
        token = "brg_src_" + secrets.token_urlsafe(32)
        digest = self._shared_hash(token)
        with self._transaction() as con:
            con.execute("INSERT INTO shared_source_credentials VALUES(?,?,?,?,NULL)",
                        (digest, source, workspace, self.clock()))
        return {"source_id": source, "workspace_id": workspace, "source_token": token,
                "shown_once": True}

    def shared_source_scope(self, token):
        digest = self._shared_hash(token)
        if digest is None:
            return None
        with self._connection() as con:
            row = con.execute("SELECT source,workspace FROM shared_source_credentials WHERE token_hash=? AND revoked IS NULL",
                              (digest,)).fetchone()
        if row is None:
            return None
        from .domain import SourceScope
        return SourceScope(row["source"], row["workspace"])

    def revoke_shared_source_credentials(self, source, workspace):
        with self._transaction() as con:
            rows = con.execute("SELECT token_hash FROM shared_source_credentials WHERE source=? AND workspace=? AND revoked IS NULL",
                               (source, workspace)).fetchall()
            con.execute("UPDATE shared_source_credentials SET revoked=? WHERE source=? AND workspace=? AND revoked IS NULL",
                        (self.clock(), source, workspace))
            for pool in con.execute("SELECT pool_id FROM shared_workspaces WHERE source=? AND workspace=?",
                                    (source, workspace)).fetchall():
                self._pool_event(con, pool["pool_id"], "source_credential_revoked",
                                 source=source, workspace=workspace)
        return {"revoked_count": len(rows)}

    def create_shared_invite(self, pool_id, kind, *, ttl_seconds=3600, profiles=None,
                             provider_ref=None):
        from .domain import _fail, _text
        if kind not in {"workspace", "node"}:
            _fail("invalid_invite_kind")
        if provider_ref is not None and (kind != "node" or not isinstance(provider_ref, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", provider_ref)):
            _fail("invalid_provider_ref")
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 86400:
            _fail("invalid_invite_ttl")
        profiles = list(profiles or ("fake-image",)) if kind == "node" else []
        if kind == "node" and (not profiles or len(profiles) > 3
                or len(set(profiles)) != len(profiles)
                or any(p not in self.enabled_profiles for p in profiles)):
            _fail("invalid_node_profiles")
        code = "brg_inv_" + secrets.token_urlsafe(24)
        with self._transaction() as con:
            con.execute("DELETE FROM shared_invite_metadata WHERE code_hash IN "
                        "(SELECT code_hash FROM shared_invites WHERE consumed IS NOT NULL OR expires<=?)",
                        (self.clock(),))
            pool = con.execute("SELECT active FROM shared_pools WHERE pool_id=?", (pool_id,)).fetchone()
            if pool is None or not pool["active"]:
                _fail("not_found", 404)
            con.execute("INSERT INTO shared_invites VALUES(?,?,?,?,?,?,NULL,NULL)",
                        (self._shared_hash(code), pool_id, kind, json.dumps(profiles),
                         self.clock(), self.clock() + ttl_seconds))
            if provider_ref is not None:
                con.execute("INSERT INTO shared_invite_metadata VALUES(?,?)",
                            (self._shared_hash(code), provider_ref))
        return {"invite_code": code, "kind": kind, "expires_at": self.clock() + ttl_seconds,
                "shown_once": True}

    def inspect_shared_invite(self, scope, code):
        from .domain import _fail
        source, workspace = self._scope(scope)
        with self._read_transaction() as con:
            invite = con.execute("SELECT i.*,p.name,p.active FROM shared_invites i JOIN shared_pools p ON p.pool_id=i.pool_id WHERE i.code_hash=?",
                                 (self._shared_hash(code),)).fetchone()
            if invite is None or invite["kind"] != "workspace" or not invite["active"]:
                _fail("invite_invalid", 404)
            if invite["expires"] <= self.clock():
                _fail("invite_expired", 410)
            if invite["consumed"] is not None:
                member = con.execute("SELECT state FROM shared_workspaces WHERE pool_id=? AND source=? AND workspace=?",
                                     (invite["pool_id"], source, workspace)).fetchone()
                if member is None:
                    _fail("invite_consumed", 409)
            return {"pool_id": invite["pool_id"], "pool_name": invite["name"],
                    "sharing_disclosure": "Tasks accepted into this pool may send their prompt and reference images to a pool member's machine. Results return to the Bridge and remain visible only to the submitting workspace.",
                    "membership": "joined" if invite["consumed"] is not None else "not_joined"}

    def accept_shared_invite(self, scope, code):
        from .domain import _fail, _utc
        source, workspace = self._scope(scope)
        digest = self._shared_hash(code)
        with self._transaction() as con:
            invite = con.execute("SELECT i.*,p.name,p.active FROM shared_invites i JOIN shared_pools p ON p.pool_id=i.pool_id WHERE i.code_hash=?",
                                 (digest,)).fetchone()
            if invite is None or invite["kind"] != "workspace" or not invite["active"]:
                _fail("invite_invalid", 404)
            old = con.execute("SELECT * FROM shared_workspaces WHERE pool_id=? AND source=? AND workspace=?",
                              (invite["pool_id"], source, workspace)).fetchone()
            if invite["consumed"] is not None:
                if old is not None and invite["consumed_by"] == f"{source}/{workspace}":
                    return {"pool_id": invite["pool_id"], "membership": old["state"]}
                _fail("invite_consumed", 409)
            if invite["expires"] <= self.clock():
                _fail("invite_expired", 410)
            now = self.clock()
            con.execute("INSERT OR REPLACE INTO shared_workspaces VALUES(?,?,?,?,?,?,NULL)",
                        (invite["pool_id"], source, workspace, "active", now, now))
            con.execute("UPDATE shared_invites SET consumed=?,consumed_by=? WHERE code_hash=?",
                        (now, f"{source}/{workspace}", digest))
            self._pool_event(con, invite["pool_id"], "workspace_joined", source=source, workspace=workspace)
            return {"pool_id": invite["pool_id"], "pool_name": invite["name"],
                    "membership": "active", "consented_at": _utc(now)}

    def shared_workspace_pools(self, scope):
        from .domain import _utc
        source, workspace = self._scope(scope)
        with self._read_transaction() as con:
            rows = con.execute("SELECT m.pool_id,m.state,m.joined,p.name,p.active FROM shared_workspaces m "
                               "JOIN shared_pools p ON p.pool_id=m.pool_id "
                               "WHERE m.source=? AND m.workspace=? ORDER BY m.joined DESC,m.pool_id",
                               (source, workspace)).fetchall()
        return [{"pool_id": r["pool_id"], "pool_name": r["name"],
                 "membership": r["state"], "pool_state": "active" if r["active"] else "inactive",
                 "joined_at": _utc(r["joined"])} for r in rows]

    def leave_shared_pool(self, scope, pool_id):
        from .domain import _fail
        source, workspace = self._scope(scope)
        now = self.clock()
        with self._transaction() as con:
            row = con.execute("SELECT state FROM shared_workspaces WHERE pool_id=? AND source=? AND workspace=?",
                              (pool_id, source, workspace)).fetchone()
            if row is None:
                _fail("pool_membership_required", 403)
            con.execute("UPDATE shared_workspaces SET state='revoked',revoked=? WHERE pool_id=? AND source=? AND workspace=?",
                        (now, pool_id, source, workspace))
            assignments = con.execute(
                "SELECT a.assignment_id,a.task_id,a.pool_id,a.node_id,a.executor "
                "FROM shared_assignments a JOIN tasks t ON t.id=a.task_id "
                "JOIN attempts at ON at.task_id=t.id "
                "WHERE a.pool_id=? AND t.source=? AND t.workspace=? "
                "AND a.state IN (" + ACTIVE_ASSIGNMENT_SQL + ") AND at.start_grant_id IS NULL",
                (pool_id, source, workspace)).fetchall()
            for assignment in assignments:
                con.execute("UPDATE shared_assignments SET state='released',released=?,release_reason='workspace_left' WHERE assignment_id=?",
                            (now, assignment["assignment_id"]))
                con.execute("DELETE FROM claims WHERE task_id=?", (assignment["task_id"],))
                self._pool_event(con, pool_id, "assignment_released", source=source,
                                 workspace=workspace, node_id=assignment["node_id"],
                                 task_id=assignment["task_id"],
                                 assignment_id=assignment["assignment_id"], reason="workspace_left")
            self._pool_event(con, pool_id, "workspace_left", source=source, workspace=workspace)
        return {"membership": "revoked"}

    def enroll_shared_node(self, code, node_id, executor_instance, profiles, *, accept_shared_data):
        from .domain import _fail, _id, _text
        if type(accept_shared_data) is not bool or not accept_shared_data:
            _fail("sharing_consent_required", 403)
        _text(node_id); _text(executor_instance)
        if (not isinstance(profiles, list) or not profiles or len(profiles) > 3
                or len(set(profiles)) != len(profiles)
                or any(p not in self.enabled_profiles for p in profiles)):
            _fail("invalid_node_profiles")
        token = "brg_node_" + secrets.token_urlsafe(32)
        digest, code_digest = self._shared_hash(token), self._shared_hash(code)
        with self._transaction() as con:
            invite = con.execute("SELECT * FROM shared_invites WHERE code_hash=?", (code_digest,)).fetchone()
            if invite is None or invite["kind"] != "node":
                _fail("invite_invalid", 404)
            if invite["consumed"] is not None:
                _fail("invite_consumed", 409)
            if invite["expires"] <= self.clock():
                _fail("invite_expired", 410)
            allowed = json.loads(invite["profiles"])
            if not set(profiles) <= set(allowed):
                _fail("node_profile_not_invited", 403)
            if con.execute("SELECT 1 FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                           (invite["pool_id"], node_id, executor_instance)).fetchone():
                _fail("node_already_registered", 409)
            now = self.clock()
            con.execute("INSERT INTO shared_nodes VALUES(?,?,?,?,?,?,?,NULL)",
                        (invite["pool_id"], node_id, executor_instance, "active", 1,
                         json.dumps(profiles), now))
            metadata = con.execute("SELECT provider_ref FROM shared_invite_metadata WHERE code_hash=?",
                                   (code_digest,)).fetchone()
            if metadata is not None:
                con.execute("INSERT INTO shared_node_metadata VALUES(?,?,?,?)",
                            (invite["pool_id"], node_id, executor_instance, metadata["provider_ref"]))
                con.execute("DELETE FROM shared_invite_metadata WHERE code_hash=?", (code_digest,))
            con.execute("INSERT INTO shared_node_credentials VALUES(?,?,?,?,?,NULL)",
                        (digest, invite["pool_id"], node_id, executor_instance, now))
            pool_scope = self._pool_scope(invite["pool_id"])
            src, ws = self._scope(pool_scope)
            con.execute("INSERT OR IGNORE INTO node_registry VALUES(?,?,?,?,1)",
                        (src, ws, node_id, executor_instance))
            con.execute("UPDATE shared_invites SET consumed=?,consumed_by=? WHERE code_hash=?",
                        (now, f"{node_id}/{executor_instance}", code_digest))
            self._pool_event(con, invite["pool_id"], "node_paired", node_id=node_id)
        return {"node_id": node_id, "executor_instance": executor_instance,
                "pool_id": invite["pool_id"], "node_token": token, "shown_once": True}

    def shared_node_from_token(self, token):
        digest = self._shared_hash(token)
        if digest is None:
            return None
        with self._connection() as con:
            row = con.execute("SELECT pool_id,node_id,executor FROM shared_node_credentials WHERE token_hash=? AND revoked IS NULL",
                              (digest,)).fetchone()
        if row is None:
            return None
        return SharedNodeIdentity(row["pool_id"], row["node_id"], row["executor"])

    def report_shared_heartbeat(self, identity, state, profiles, profile_health=None):
        from .domain import _fail
        if state not in {"available", "busy", "attention"}:
            _fail("invalid_node_status")
        if (not isinstance(profiles, list) or len(profiles) > 3
                or len(set(profiles)) != len(profiles)
                or any(p not in {"fake-image", "codex-image", "midjourney-manual"} for p in profiles)):
            _fail("invalid_node_profiles")
        pool_scope = self._pool_scope(identity.pool_id)
        source, workspace = self._scope(pool_scope)
        with self._transaction() as con:
            node = con.execute("SELECT state,allowed_profiles FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                               (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
            if node is None or node["state"] != "active":
                _fail("node_revoked" if node else "node_not_registered", 403)
            if not set(profiles) <= set(json.loads(node["allowed_profiles"])):
                _fail("node_profile_not_invited", 403)
            self._check_node_active(con, pool_scope, identity.node_id, identity.executor_instance, required=True)
            self._record_health(con, pool_scope, identity.node_id, identity.executor_instance,
                                profiles, profile_health)
            con.execute("INSERT OR REPLACE INTO node_heartbeats VALUES(?,?,?,?,?,?,?)",
                        (source, workspace, identity.node_id, identity.executor_instance,
                         self.clock(), state, json.dumps(profiles)))
            con.execute("INSERT OR REPLACE INTO shared_node_status VALUES(?,?,?,?,?,?)",
                        (identity.pool_id, identity.node_id, identity.executor_instance,
                         self.clock(), state, json.dumps(profiles)))
        return {"accepted": True, "heartbeat_timeout_seconds": 90}

    def workspace_has_shared_pool(self, scope, con=None):
        source, workspace = self._scope(scope)
        if con is None:
            with self._connection() as opened:
                return opened.execute("SELECT 1 FROM shared_workspaces WHERE source=? AND workspace=? LIMIT 1",
                                      (source, workspace)).fetchone() is not None
        return con.execute("SELECT 1 FROM shared_workspaces WHERE source=? AND workspace=? LIMIT 1",
                           (source, workspace)).fetchone() is not None

    def _shared_profile_healthy(self, con, pool_id, node_id, executor, profile_id, now):
        status = con.execute("SELECT * FROM shared_node_status WHERE pool_id=? AND node_id=? AND executor=?",
                             (pool_id, node_id, executor)).fetchone()
        if (status is None or now - status["seen"] >= 90 or status["state"] not in {"available", "busy"}
                or profile_id not in json.loads(status["profiles"])):
            return False
        health = con.execute("SELECT * FROM profile_health WHERE source='_shared_pool' AND workspace=? AND node=? AND executor=? AND profile=?",
                             (pool_id, node_id, executor, profile_id)).fetchone()
        if health is None:
            # The built-in deterministic fake executor has no provider login or
            # account state. Real profiles still require a fresh positive probe.
            return profile_id == "fake-image"
        if now - health["seen"] >= 90:
            return False
        return health["health"] == "available"

    def _shared_pool_has_healthy_node(self, con, scope, profile_id, now):
        source, workspace = self._scope(scope)
        memberships = con.execute("SELECT m.pool_id FROM shared_workspaces m JOIN shared_pools p ON p.pool_id=m.pool_id "
                                  "WHERE m.source=? AND m.workspace=? AND m.state='active' AND p.active=1",
                                  (source, workspace)).fetchall()
        for membership in memberships:
            nodes = con.execute("SELECT * FROM shared_nodes WHERE pool_id=? AND state='active'",
                                (membership["pool_id"],)).fetchall()
            for node in nodes:
                if profile_id in json.loads(node["allowed_profiles"]) and self._shared_profile_healthy(
                        con, membership["pool_id"], node["node_id"], node["executor"], profile_id, now):
                    return True
        return False

    def _shared_free_nodes(self, con, source, workspace, profile_id, now, task_id=None):
        if task_id is None:
            memberships = con.execute("SELECT m.pool_id FROM shared_workspaces m JOIN shared_pools p ON p.pool_id=m.pool_id "
                                      "WHERE m.source=? AND m.workspace=? AND m.state='active' AND p.active=1",
                                      (source, workspace)).fetchall()
        else:
            memberships = con.execute("SELECT m.pool_id FROM shared_workspaces m JOIN shared_pools p ON p.pool_id=m.pool_id "
                                      "JOIN shared_task_pools stp ON stp.pool_id=m.pool_id "
                                      "WHERE m.source=? AND m.workspace=? AND m.state='active' AND p.active=1 "
                                      "AND stp.task_id=?",
                                      (source, workspace, task_id)).fetchall()
        return self._shared_free_nodes_for_pools(
            con, (membership["pool_id"] for membership in memberships), profile_id, now)

    def _shared_free_nodes_for_pools(self, con, pool_ids, profile_id, now):
        result = []
        for pool_id in pool_ids:
            nodes = con.execute("SELECT * FROM shared_nodes WHERE pool_id=? AND state='active'",
                                (pool_id,)).fetchall()
            for node in nodes:
                if profile_id not in json.loads(node["allowed_profiles"]):
                    continue
                status = con.execute("SELECT state FROM shared_node_status WHERE pool_id=? AND node_id=? AND executor=? AND seen>?",
                                     (pool_id, node["node_id"], node["executor"], now-90)).fetchone()
                if status is None or status["state"] != "available":
                    continue
                if not self._shared_profile_healthy(con, pool_id, node["node_id"], node["executor"], profile_id, now):
                    continue
                busy = con.execute(f"SELECT 1 FROM shared_assignments WHERE pool_id=? AND node_id=? AND executor=? AND state IN ({SLOT_HOLDING_SQL}) LIMIT 1",
                                   (pool_id, node["node_id"], node["executor"])).fetchone()
                if busy is None:
                    result.append((pool_id, node["node_id"], node["executor"], 0))
        return result

    def node_status(self, scope):
        if not getattr(self, "shared_pool_enabled", False) or not self.workspace_has_shared_pool(scope):
            return super().node_status(scope)
        source, workspace = self._scope(scope)
        from .domain import _utc
        now = self.clock()
        with self._read_transaction() as con:
            memberships = con.execute("SELECT pool_id FROM shared_workspaces WHERE source=? AND workspace=? AND state='active'",
                                       (source, workspace)).fetchall()
            live = []
            profiles = set()
            registered = False
            for membership in memberships:
                nodes = con.execute("SELECT n.*,s.seen,s.state AS heartbeat_state,s.profiles AS reported FROM shared_nodes n "
                                    "LEFT JOIN shared_node_status s ON s.pool_id=n.pool_id AND s.node_id=n.node_id AND s.executor=n.executor "
                                    "WHERE n.pool_id=? AND n.state='active'", (membership["pool_id"],)).fetchall()
                registered = registered or bool(nodes)
                for node in nodes:
                    if node["seen"] is not None and now-node["seen"] < 90:
                        live.append(node)
                        profiles.update(json.loads(node["reported"]))
            free = any(self._shared_free_nodes(con, source, workspace, p, now)
                       for p in self.enabled_profiles)
        if not live:
            state = "offline" if registered else "unknown"
        elif free:
            state = "available"
        else:
            state = "busy"
        return {"state": state, "authorization": "shared_pool" if registered else "unknown",
                "last_seen_at": _utc(max(r["seen"] for r in live)) if live else None,
                "heartbeat_timeout_seconds": 90, "reported_profiles": sorted(profiles),
                "login_verified": False, "real_generation_verified": False}

    def routing_availability(self, scope):
        if not getattr(self, "shared_pool_enabled", False) or not self.workspace_has_shared_pool(scope):
            return super().routing_availability(scope)
        from .domain import _utc
        from .profiles import get_profile
        source, workspace = self._scope(scope)
        now = self.clock()
        with self._read_transaction() as con:
            revision = con.execute("SELECT revision FROM health_revision WHERE singleton=1").fetchone()[0]
            items = []
            for profile in map(get_profile, self.enabled_profiles):
                healthy = self._shared_pool_has_healthy_node(con, scope, profile.id, now)
                free = bool(self._shared_free_nodes(con, source, workspace, profile.id, now))
                memberships = con.execute("SELECT pool_id FROM shared_workspaces WHERE source=? AND workspace=? AND state='active'",
                                          (source, workspace)).fetchall()
                observations = []
                for m in memberships:
                    observations.extend(con.execute("SELECT seen,state,profiles FROM shared_node_status WHERE pool_id=? AND seen>?",
                                                    (m["pool_id"], now-90)).fetchall())
                items.append({"profile_id": profile.id, "profile_revision": profile.revision,
                              "health": "available" if healthy else "unknown",
                              "capacity": "idle" if free else "busy",
                              "reason_code": "ready" if healthy else "unverified",
                              "evidence_kind": "local_probe" if healthy and profile.id != "fake-image" else "none",
                              "observed_at": _utc(max((r["seen"] for r in observations), default=0)) if observations else None,
                              "valid_until": _utc(max((r["seen"] for r in observations), default=0)+90) if observations else None})
        return {"schema_version": "1", "revision": revision, "profiles": items}

    def _release_expired_shared_assignments(self, con, now):
        rows = con.execute("SELECT a.*,t.source,t.workspace,t.state AS task_state,t.cancel_requested,t.deadline,t.payload,t.attempt_id AS task_attempt "
                           "FROM shared_assignments a JOIN tasks t ON t.id=a.task_id "
                           "WHERE a.state IN (" + ACTIVE_ASSIGNMENT_SQL + ")").fetchall()
        for row in rows:
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (row["task_id"],)).fetchone()
            claim = con.execute("SELECT * FROM claims WHERE task_id=?", (row["task_id"],)).fetchone()
            membership = con.execute("SELECT m.state,p.active,n.state AS node_state FROM shared_workspaces m "
                                     "JOIN shared_pools p ON p.pool_id=m.pool_id "
                                     "JOIN shared_nodes n ON n.pool_id=m.pool_id AND n.node_id=? AND n.executor=? "
                                     "WHERE m.pool_id=? AND m.source=? AND m.workspace=?",
                                     (row["node_id"], row["executor"], row["pool_id"],
                                      row["source"], row["workspace"])).fetchone()
            is_started = bool(attempt and attempt["start_grant_id"])
            is_manual = self._profile(row).execution_mode == "manual"
            is_execution_started = is_started and not is_manual
            is_expired = row["deadline"] <= now and not is_execution_started
            is_terminal = row["task_state"] in {"artifacts_ready", "failed", "canceled", "expired"} or is_expired
            if is_expired and row["task_state"] not in {"artifacts_ready", "failed", "canceled", "expired"}:
                con.execute("UPDATE tasks SET state='expired' WHERE id=?", (row["task_id"],))
            is_prestart_invalid = (not is_execution_started and (is_terminal or row["cancel_requested"]
                                     or membership is None or membership["state"] != "active"
                                     or not membership["active"] or membership["node_state"] != "active"))
            if is_prestart_invalid:
                reason = "task_terminal" if is_terminal or row["cancel_requested"] else "membership_or_node_inactive"
                assignment_state = "expired" if is_expired else "released"
                con.execute("UPDATE shared_assignments SET state=?,released=?,release_reason=? WHERE assignment_id=?",
                            (assignment_state, now, reason, row["assignment_id"]))
                if claim is not None:
                    con.execute("DELETE FROM claims WHERE task_id=?", (row["task_id"],))
                self._pool_event(con, row["pool_id"], "assignment_released", source=row["source"],
                                 workspace=row["workspace"], node_id=row["node_id"],
                                 task_id=row["task_id"], assignment_id=row["assignment_id"], reason=reason)
                continue
            if row["state"] == "awaiting_user":
                continue
            if row["lease_expires"] > now:
                continue
            if is_started:
                if claim is None or claim["expires"] <= now:
                    if not is_terminal:
                        con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=? AND cancel_requested=0",
                                    (row["task_id"],))
                        con.execute("UPDATE shared_assignments SET state='execution_unknown' WHERE assignment_id=?",
                                    (row["assignment_id"],))
                        self._pool_event(con, row["pool_id"], "execution_unknown", source=row["source"],
                                         workspace=row["workspace"], node_id=row["node_id"],
                                         task_id=row["task_id"], assignment_id=row["assignment_id"])
                continue
            if claim is not None and claim["expires"] > now:
                continue
            con.execute("UPDATE shared_assignments SET state='released',released=?,release_reason='lease_expired' WHERE assignment_id=?",
                        (now, row["assignment_id"]))
            self._pool_event(con, row["pool_id"], "assignment_released", source=row["source"],
                             workspace=row["workspace"], node_id=row["node_id"],
                             task_id=row["task_id"], assignment_id=row["assignment_id"],
                             reason="lease_expired")

    def summary_shared_pool(self):
        now = self.clock()
        with self._read_transaction() as con:
            pools = con.execute("SELECT COUNT(*) FROM shared_pools WHERE active=1").fetchone()[0]
            workspaces = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT m.source,m.workspace "
                                     "FROM shared_workspaces m JOIN shared_pools p ON p.pool_id=m.pool_id "
                                     "WHERE m.state='active' AND p.active=1)").fetchone()[0]
            nodes = con.execute("SELECT COUNT(*) FROM shared_nodes WHERE state='active'").fetchone()[0]
            online = con.execute("SELECT COUNT(*) FROM shared_node_status s JOIN shared_nodes n "
                                 "ON n.pool_id=s.pool_id AND n.node_id=s.node_id AND n.executor=s.executor "
                                 "WHERE n.state='active' AND s.seen>?", (now-90,)).fetchone()[0]
            queued = con.execute("SELECT COUNT(DISTINCT t.id) FROM tasks t "
                                 "JOIN shared_task_pools stp ON stp.task_id=t.id "
                                 "JOIN shared_pools p ON p.pool_id=stp.pool_id "
                                 "JOIN shared_workspaces m ON m.pool_id=stp.pool_id "
                                 "AND m.source=t.source AND m.workspace=t.workspace "
                                 "WHERE p.active=1 AND m.state='active' AND t.state='queued' AND t.deadline>?",
                                 (now,)).fetchone()[0]
            active = con.execute("SELECT COUNT(*) FROM shared_assignments WHERE state IN (" + ACTIVE_ASSIGNMENT_SQL + ")").fetchone()[0]
            slots_total = con.execute("SELECT COUNT(*) FROM shared_nodes").fetchone()[0]
            slots_used = con.execute("SELECT COUNT(*) FROM shared_assignments WHERE state IN (" + SLOT_HOLDING_SQL + ")").fetchone()[0]
            slots_free = con.execute("""SELECT COUNT(*) FROM shared_nodes n JOIN shared_node_status s
                ON s.pool_id=n.pool_id AND s.node_id=n.node_id AND s.executor=n.executor
                WHERE n.state='active' AND s.state='available' AND s.seen>?
                AND NOT EXISTS(SELECT 1 FROM shared_assignments a WHERE a.pool_id=n.pool_id
                  AND a.node_id=n.node_id AND a.executor=n.executor AND a.state IN (""" + SLOT_HOLDING_SQL + "))",
                                  (now-90,)).fetchone()[0]
        return {"pools_active": pools, "workspaces_joined": workspaces,
                "nodes_active": nodes, "nodes_online": online,
                "queued_tasks": queued, "active_assignments": active,
                "slots_total": slots_total, "slots_used": slots_used,
                "slots_free": slots_free,
                "heartbeat_timeout_seconds": 90}

    def list_shared_nodes(self):
        from .domain import _utc
        now = self.clock()
        with self._read_transaction() as con:
            rows = con.execute("""SELECT n.pool_id,p.name AS pool_name,n.node_id,n.executor,n.state,
                n.allowed_profiles,s.seen,s.state AS heartbeat_state,s.profiles,
                nm.provider_ref,
                a.assignment_id,a.task_id,a.state AS assignment_state,a.slot,
                t.workspace,t.state AS task_state,t.payload AS task_payload
                FROM shared_nodes n JOIN shared_pools p ON p.pool_id=n.pool_id
                LEFT JOIN shared_node_metadata nm ON nm.pool_id=n.pool_id AND nm.node_id=n.node_id
                  AND nm.executor=n.executor
                LEFT JOIN shared_node_status s ON s.pool_id=n.pool_id AND s.node_id=n.node_id AND s.executor=n.executor
                LEFT JOIN shared_assignments a ON a.pool_id=n.pool_id AND a.node_id=n.node_id AND a.executor=n.executor
                  AND a.state IN (""" + SLOT_HOLDING_SQL + ") "
                "LEFT JOIN tasks t ON t.id=a.task_id ORDER BY p.created,n.created,n.node_id").fetchall()
        return [{"pool_id": r["pool_id"], "pool_name": r["pool_name"],
                 "node_id": r["node_id"], "executor_instance": r["executor"],
                 "state": r["state"], "provider_ref": r["provider_ref"],
                 "liveness": ("offline" if r["seen"] is None or not 0 <= now-r["seen"] < 90
                            else "needs_attention" if r["heartbeat_state"] == "attention"
                            else "busy" if r["heartbeat_state"] == "busy" else "online"),
                 "last_seen_at": _utc(r["seen"]) if r["seen"] is not None else None,
                 "profiles": json.loads(r["profiles"] or r["allowed_profiles"]),
                 "slot_capacity": 1, "current_assignment": (
                     {"assignment_id": r["assignment_id"], "task_id": r["task_id"],
                      "workspace_id": r["workspace"], "task_state": r["task_state"],
                      "assignment_state": r["assignment_state"], "slot": r["slot"]}
                     if r["assignment_id"] else None)} for r in rows]

    @staticmethod
    def _node_execution_counts(con, pool_id, node_id, executor):
        row = con.execute("""SELECT COUNT(*) AS total_started,
            SUM(state='succeeded') AS succeeded, SUM(state='failed') AS failed,
            SUM(state IN ('running','uploading')) AS in_progress,
            SUM(state='awaiting_user') AS awaiting_user,
            SUM(state='execution_unknown') AS unknown,
            SUM(state='cancelled') AS canceled,
            SUM(delivery_ack='accepted') AS ack_accepted,
            SUM(delivery_ack='rejected') AS ack_rejected,
            SUM(state='succeeded' AND delivery_ack IS NULL) AS ack_pending
            FROM node_execution_ledger WHERE pool_id=? AND node_id=? AND executor=?""",
                            (pool_id, node_id, executor)).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    @staticmethod
    def _shared_tracking_since(con):
        from .domain import _utc
        row = con.execute("SELECT applied FROM shared_schema_version WHERE version=5").fetchone()
        return _utc(row["applied"]) if row is not None else None

    def _node_current_execution(self, con, pool_id, node_id, executor):
        from .domain import _utc
        row = con.execute("""SELECT a.attempt_id,a.profile,a.state AS assignment_state,
            a.assigned,a.started,t.state AS task_state FROM shared_assignments a
            LEFT JOIN tasks t ON t.id=a.task_id WHERE a.pool_id=? AND a.node_id=? AND a.executor=?
            AND a.state IN (""" + ACTIVE_ASSIGNMENT_SQL + ") ORDER BY a.assigned DESC LIMIT 1",
                          (pool_id, node_id, executor)).fetchone()
        if row is None:
            return None
        ledger = con.execute("SELECT state,started_at FROM node_execution_ledger WHERE attempt_key=?",
                             (self._shared_hash(row["attempt_id"]),)).fetchone()
        state = ledger["state"] if ledger is not None else row["task_state"] or row["assignment_state"]
        started_at = ledger["started_at"] if ledger is not None else row["started"]
        return {"state": state, "profile": row["profile"],
                "assigned_at": _utc(row["assigned"]),
                "started_at": _utc(started_at) if started_at is not None else None,
                "counted": ledger is not None}

    def shared_node_self_status(self, identity):
        from .domain import _fail, _utc
        now = self.clock()
        with self._read_transaction() as con:
            node = con.execute("""SELECT n.state,n.allowed_profiles,s.seen,s.state AS heartbeat_state,
                s.profiles,nm.provider_ref FROM shared_nodes n
                LEFT JOIN shared_node_status s ON s.pool_id=n.pool_id AND s.node_id=n.node_id AND s.executor=n.executor
                LEFT JOIN shared_node_metadata nm ON nm.pool_id=n.pool_id AND nm.node_id=n.node_id
                  AND nm.executor=n.executor
                WHERE n.pool_id=? AND n.node_id=? AND n.executor=?""",
                               (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
            if node is None:
                _fail("node_not_registered", 404)
            metrics = self._node_execution_counts(con, identity.pool_id, identity.node_id,
                                                  identity.executor_instance)
            tracking_since = self._shared_tracking_since(con)
            current = self._node_current_execution(con, identity.pool_id, identity.node_id,
                                                   identity.executor_instance)
            rows = con.execute("""SELECT profile,state,started_at,updated_at,completed_at,
                artifact_count,failure_code,delivery_ack,delivery_ack_at
                FROM node_execution_ledger WHERE pool_id=? AND node_id=? AND executor=?
                ORDER BY started_at DESC LIMIT 10""",
                               (identity.pool_id, identity.node_id, identity.executor_instance)).fetchall()
        heartbeat_age = max(0, int(now - node["seen"])) if node["seen"] is not None else None
        heartbeat_state = ("offline" if heartbeat_age is None or heartbeat_age >= 90
                           else node["heartbeat_state"])
        return {"node": {"pool_id": identity.pool_id, "node_id": identity.node_id,
                          "executor_instance": identity.executor_instance,
                          "provider_ref": node["provider_ref"], "state": node["state"],
                          "availability": heartbeat_state,
                          "heartbeat_age_seconds": heartbeat_age,
                          "last_seen_at": _utc(node["seen"]) if node["seen"] is not None else None,
                          "profiles": json.loads(node["profiles"] or node["allowed_profiles"])},
                "tracking_since": tracking_since, "current_execution": current, "metrics": metrics,
                "recent_executions": [{"profile": r["profile"], "state": r["state"],
                    "started_at": _utc(r["started_at"]), "updated_at": _utc(r["updated_at"]),
                    "completed_at": _utc(r["completed_at"]) if r["completed_at"] is not None else None,
                    "artifact_count": r["artifact_count"], "failure_code": r["failure_code"],
                    "delivery_ack": r["delivery_ack"],
                    "delivery_ack_at": _utc(r["delivery_ack_at"]) if r["delivery_ack_at"] is not None else None}
                    for r in rows]}

    def shared_node_reward_metrics(self, limit=500):
        from .domain import _utc
        now = self.clock()
        with self._read_transaction() as con:
            tracking_since = self._shared_tracking_since(con)
            nodes = con.execute("""SELECT n.pool_id,p.name AS pool_name,n.node_id,n.executor,n.state,
                n.allowed_profiles,s.seen,s.state AS heartbeat_state,s.profiles,nm.provider_ref
                FROM shared_nodes n JOIN shared_pools p ON p.pool_id=n.pool_id
                LEFT JOIN shared_node_status s ON s.pool_id=n.pool_id AND s.node_id=n.node_id AND s.executor=n.executor
                LEFT JOIN shared_node_metadata nm ON nm.pool_id=n.pool_id AND nm.node_id=n.node_id
                  AND nm.executor=n.executor
                ORDER BY p.created,n.created,n.node_id LIMIT ?""", (limit + 1,)).fetchall()
            truncated = len(nodes) > limit
            nodes = nodes[:limit]
            items = []
            for node in nodes:
                counts = self._node_execution_counts(con, node["pool_id"], node["node_id"], node["executor"])
                current = self._node_current_execution(con, node["pool_id"], node["node_id"], node["executor"])
                seen = node["seen"]
                age = max(0, int(now - seen)) if seen is not None else None
                liveness = ("offline" if age is None or age >= 90 else
                            "needs_attention" if node["heartbeat_state"] == "attention" else
                            "busy" if node["heartbeat_state"] == "busy" else "online")
                items.append({"pool_id": node["pool_id"], "pool_name": node["pool_name"],
                    "node_id": node["node_id"], "executor_instance": node["executor"],
                    "provider_ref": node["provider_ref"], "node_state": node["state"],
                    "liveness": liveness, "heartbeat_age_seconds": age,
                    "last_seen_at": _utc(seen) if seen is not None else None,
                    "profiles": json.loads(node["profiles"] or node["allowed_profiles"]),
                    "current_execution": current, "metrics": counts})
        return {"tracking": "since_schema_v5_activation", "tracking_since": tracking_since,
                "truncated": truncated, "nodes": items}

    def set_shared_node_state(self, pool_id, node_id, executor, state):
        from .domain import _fail
        if state not in {"active", "draining", "revoked"}:
            _fail("invalid_node_status")
        with self._transaction() as con:
            row = con.execute("SELECT state FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                              (pool_id, node_id, executor)).fetchone()
            if row is None:
                _fail("node_not_registered", 404)
            con.execute("UPDATE shared_nodes SET state=?,revoked=? WHERE pool_id=? AND node_id=? AND executor=?",
                        (state, self.clock() if state == "revoked" else None, pool_id, node_id, executor))
            active = int(state == "active")
            con.execute("UPDATE node_registry SET active=? WHERE source='_shared_pool' AND workspace=? AND node=? AND executor=?",
                        (active, pool_id, node_id, executor))
            kind = {"active": "node_reactivated", "draining": "node_draining", "revoked": "node_revoked"}[state]
            self._pool_event(con, pool_id, kind, node_id=node_id)
            self._release_expired_shared_assignments(con, self.clock())
        return {"node_id": node_id, "state": state}

    def set_shared_node_provider_ref(self, pool_id, node_id, executor, provider_ref):
        from .domain import _fail
        if provider_ref is not None and (not isinstance(provider_ref, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", provider_ref)):
            _fail("invalid_provider_ref")
        with self._transaction() as con:
            if con.execute("SELECT 1 FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                           (pool_id, node_id, executor)).fetchone() is None:
                _fail("node_not_registered", 404)
            if provider_ref is None:
                con.execute("DELETE FROM shared_node_metadata WHERE pool_id=? AND node_id=? AND executor=?",
                            (pool_id, node_id, executor))
            else:
                con.execute("INSERT OR REPLACE INTO shared_node_metadata VALUES(?,?,?,?)",
                            (pool_id, node_id, executor, provider_ref))
        return {"node_id": node_id, "provider_ref": provider_ref}

    @staticmethod
    def _history_cursor(cursor):
        if not cursor:
            return None
        if not isinstance(cursor, str) or len(cursor) > 512:
            from .domain import _fail
            _fail("invalid_cursor")
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
            created, task_id = raw.split("|", 1)
            return float(created), task_id
        except (ValueError, UnicodeError):
            from .domain import _fail
            _fail("invalid_cursor")

    @staticmethod
    def _history_cursor_encode(created, task_id):
        return base64.urlsafe_b64encode(f"{float(created)!r}|{task_id}".encode("ascii")).decode("ascii").rstrip("=")

    def shared_task_history(self, *, scope=None, cursor=None, limit=50, status=None,
                            node_id=None, workspace_id=None):
        from .domain import _fail, _integer, _utc
        _integer(limit, 1, 100)
        if any(value is not None and (not isinstance(value, str) or len(value) > 128)
               for value in (node_id, workspace_id)):
            _fail("invalid_filter")
        if status is not None and status not in {"queued", "running", "execution_unknown", "awaiting_user",
                                                "uploading", "artifacts_ready", "failed", "canceled", "expired"}:
            _fail("invalid_status_filter")
        mark = self._history_cursor(cursor)
        where = ["EXISTS (SELECT 1 FROM shared_task_pools stp WHERE stp.task_id=t.id)"]
        params = []
        if scope is not None:
            source, workspace = self._scope(scope)
            where.append("t.source=? AND t.workspace=?")
            params.extend((source, workspace))
        if status:
            where.append("t.state=?")
            params.append(status)
        if workspace_id:
            where.append("t.workspace=?")
            params.append(workspace_id)
        if node_id:
            where.append("sa.node_id=?")
            params.append(node_id)
        if mark:
            where.append("(t.created<? OR (t.created=? AND t.id<?))")
            params.extend((mark[0], mark[0], mark[1]))
        params.append(limit+1)
        with self._read_transaction() as con:
            rows = con.execute("""SELECT t.id,t.source,t.workspace,t.state,t.attempt_id,t.created,t.payload,
                sa.node_id,sa.executor,sa.slot,sa.assignment_id,sa.state AS assignment_state,ta.code AS reason_code
                FROM tasks t LEFT JOIN shared_assignments sa ON sa.assignment_id=(
                  SELECT ax.assignment_id FROM shared_assignments ax WHERE ax.task_id=t.id
                  ORDER BY ax.assigned DESC LIMIT 1)
                LEFT JOIN task_attention ta ON ta.task_id=t.id
                WHERE """ + " AND ".join(where) + " ORDER BY t.created DESC,t.id DESC LIMIT ?", params).fetchall()
        page, more = rows[:limit], len(rows) > limit
        items = []
        for row in page:
            request = json.loads(row["payload"])
            items.append({"task_id": row["id"], "workspace_id": row["workspace"],
                          "status": row["state"], "profile_id": request.get("execution_profile_id", "fake-image"),
                          "created_at": _utc(row["created"]), "node_id": row["node_id"],
                          "executor_instance": row["executor"], "slot": row["slot"],
                          "assignment_id": row["assignment_id"],
                          "assignment_state": row["assignment_state"],
                          "reason_code": row["reason_code"]})
        next_cursor = self._history_cursor_encode(page[-1]["created"], page[-1]["id"]) if more and page else None
        return {"items": items, "next_cursor": next_cursor}

    def shared_task_detail(self, task_id):
        from .domain import _fail, _utc
        with self._read_transaction() as con:
            row = con.execute("""SELECT t.id,t.workspace,t.state,t.attempt_id,t.created,t.payload,
                sa.pool_id,sa.node_id,sa.executor,sa.slot,sa.assignment_id,sa.state AS assignment_state,
                ta.code AS reason_code FROM tasks t LEFT JOIN shared_assignments sa ON sa.assignment_id=(
                  SELECT ax.assignment_id FROM shared_assignments ax WHERE ax.task_id=t.id
                  ORDER BY ax.assigned DESC LIMIT 1)
                LEFT JOIN task_attention ta ON ta.task_id=t.id WHERE t.id=? AND EXISTS(
                  SELECT 1 FROM shared_task_pools stp WHERE stp.task_id=t.id)""",
                               (task_id,)).fetchone()
            if row is None:
                _fail("not_found", 404)
            events = con.execute("SELECT occurred,kind,node_id,reason FROM shared_pool_events "
                                 "WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return {"task_id": row["id"], "workspace_id": row["workspace"],
                "status": row["state"], "profile_id": json.loads(row["payload"]).get("execution_profile_id", "fake-image"),
                "created_at": _utc(row["created"]), "node_id": row["node_id"],
                "executor_instance": row["executor"], "slot": row["slot"],
                "assignment_id": row["assignment_id"], "assignment_state": row["assignment_state"],
                "reason_code": row["reason_code"],
                "events": [{"occurred_at": _utc(e["occurred"]), "kind": e["kind"],
                            "node_id": e["node_id"], "reason_code": e["reason"]} for e in events]}

    def maintain_shared_retention(self, *, retention_seconds=86400, batch_size=100):
        from .domain import _integer, SourceScope
        _integer(retention_seconds, 1, 365 * 86400)
        _integer(batch_size, 1, 100)
        with self._transaction() as con:
            con.execute("DELETE FROM shared_invite_metadata WHERE code_hash IN "
                        "(SELECT code_hash FROM shared_invites WHERE consumed IS NOT NULL OR expires<=?)",
                        (self.clock(),))
        with self._read_transaction() as con:
            scopes = con.execute("SELECT DISTINCT t.source,t.workspace FROM tasks t JOIN shared_workspaces m "
                                 "ON m.source=t.source AND m.workspace=t.workspace "
                                 "ORDER BY t.source,t.workspace LIMIT ?", (batch_size,)).fetchall()
        totals = {"tasks_reclaimed": 0, "inputs_expired": 0, "files_deleted": 0,
                  "workspaces_processed": 0}
        for row in scopes:
            result = self.maintain_retention(SourceScope(row["source"], row["workspace"]),
                                             retention_seconds=retention_seconds,
                                             batch_size=batch_size)
            totals["workspaces_processed"] += 1
            for key in ("tasks_reclaimed", "inputs_expired", "files_deleted"):
                totals[key] += result[key]
        return totals

    def next_shared_assignment(self, identity):
        from .domain import _fail, _id
        now = self.clock()
        with self._transaction() as con:
            node = con.execute("SELECT state FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                               (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
            if node is None or node["state"] != "active":
                _fail("node_revoked" if node else "node_not_registered", 403)
            self._release_expired_shared_assignments(con, now)
            current = con.execute(f"SELECT * FROM shared_assignments WHERE pool_id=? AND node_id=? AND executor=? AND state IN ({SLOT_HOLDING_SQL}) ORDER BY assigned LIMIT 1",
                                  (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
            if current is not None:
                return self._shared_assignment_view(current) if current["state"] == "assigned" else {"assignment": None}

            workspace_rows = con.execute("SELECT DISTINCT t.source,t.workspace FROM tasks t "
                "JOIN attempts at ON at.task_id=t.id "
                "JOIN shared_task_pools stp ON stp.task_id=t.id "
                "JOIN shared_workspaces m ON m.pool_id=stp.pool_id AND m.source=t.source AND m.workspace=t.workspace "
                "JOIN shared_pools p ON p.pool_id=stp.pool_id "
                "WHERE t.state='queued' AND t.deadline>? AND t.cancel_requested=0 "
                "AND at.start_grant_id IS NULL AND m.state='active' AND p.active=1 "
                f"AND NOT EXISTS (SELECT 1 FROM shared_assignments ax WHERE ax.task_id=t.id AND ax.state IN ({ACTIVE_ASSIGNMENT_SQL})) "
                "ORDER BY t.source,t.workspace", (now,)).fetchall()
            workspace_keys = [(row["source"], row["workspace"]) for row in workspace_rows]
            if not workspace_keys:
                return {"assignment": None}
            cursor = con.execute("SELECT source,workspace FROM shared_scheduler_cursor WHERE singleton=1").fetchone()
            if cursor["source"] is not None:
                after = (cursor["source"], cursor["workspace"])
                split = next((index for index, key in enumerate(workspace_keys) if key > after), 0)
                workspace_keys = workspace_keys[split:] + workspace_keys[:split]
            free_slots_by_contract = {}
            for key in workspace_keys:
                tasks = con.execute("SELECT t.*,GROUP_CONCAT(DISTINCT stp.pool_id) AS admitted_pool_ids "
                    "FROM tasks t JOIN attempts at ON at.task_id=t.id "
                    "JOIN shared_task_pools stp ON stp.task_id=t.id "
                    "JOIN shared_workspaces m ON m.pool_id=stp.pool_id AND m.source=t.source AND m.workspace=t.workspace "
                    "JOIN shared_pools p ON p.pool_id=stp.pool_id "
                    "WHERE t.source=? AND t.workspace=? AND t.state='queued' AND t.deadline>? "
                    "AND t.cancel_requested=0 AND at.start_grant_id IS NULL "
                    "AND m.state='active' AND p.active=1 "
                    f"AND NOT EXISTS (SELECT 1 FROM shared_assignments ax WHERE ax.task_id=t.id AND ax.state IN ({ACTIVE_ASSIGNMENT_SQL})) "
                    "GROUP BY t.id ORDER BY t.created,t.id", (key[0], key[1], now))
                for task in tasks:
                    profile_id = self._profile(task).id
                    pool_ids = tuple(sorted((task["admitted_pool_ids"] or "").split(",")))
                    contract = (profile_id, pool_ids)
                    if contract not in free_slots_by_contract:
                        free_slots_by_contract[contract] = self._shared_free_nodes_for_pools(
                            con, pool_ids, profile_id, now)
                    slots = free_slots_by_contract[contract]
                    if not slots:
                        continue
                    tasks.close()
                    pool_id, node_id, executor, slot = self._pool_rng.choice(slots)
                    assignment_id = _id("assignment")
                    lease = now + 120
                    policy = "uniform-slot-v1"
                    con.execute("INSERT INTO shared_assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (assignment_id, task["id"], task["attempt_id"], pool_id, node_id, executor,
                                 slot, profile_id, "assigned", now, lease, None, None, None, policy))
                    con.execute("UPDATE shared_scheduler_cursor SET source=?,workspace=? WHERE singleton=1", key)
                    self._pool_event(con, pool_id, "assignment_created", source=task["source"],
                                     workspace=task["workspace"], node_id=node_id,
                                     task_id=task["id"], assignment_id=assignment_id)
                    row = con.execute("SELECT * FROM shared_assignments WHERE assignment_id=?",
                                      (assignment_id,)).fetchone()
                    if (pool_id, node_id, executor) == (identity.pool_id, identity.node_id,
                                                        identity.executor_instance):
                        return self._shared_assignment_view(row)
                    return {"assignment": None}
            return {"assignment": None}

    @staticmethod
    def _shared_assignment_view(row):
        from .domain import _utc
        return {"assignment": {"assignment_id": row["assignment_id"],
                "task_id": row["task_id"], "attempt_id": row["attempt_id"],
                "pool_id": row["pool_id"], "node_id": row["node_id"],
                "executor_instance": row["executor"], "slot": row["slot"],
                "profile_id": row["profile"], "state": row["state"],
                "expires_at": _utc(row["lease_expires"]),
                "policy_version": row["policy_version"]}}

    def _assigned_task(self, con, identity, assignment_id, *, task_id=None, allow_finished=True):
        from .domain import SourceScope, _fail
        row = con.execute("SELECT * FROM shared_assignments WHERE assignment_id=?",
                          (assignment_id,)).fetchone()
        if row is None or (task_id is not None and row["task_id"] != task_id):
            _fail("assignment_identity_mismatch", 403)
        if (row["pool_id"], row["node_id"], row["executor"]) != (
                identity.pool_id, identity.node_id, identity.executor_instance):
            _fail("assignment_identity_mismatch", 403)
        task = con.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
        if task is None or task["attempt_id"] != row["attempt_id"]:
            _fail("assignment_identity_mismatch", 403)
        node = con.execute("SELECT state FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                           (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
        if node is None:
            _fail("node_not_registered", 403)
        if row["state"] not in ACTIVE_ASSIGNMENT_STATES and not (
                allow_finished and (row["state"] == "released" or
                (row["state"] in {"artifacts_ready", "failed", "canceled", "expired"}
                 and task["state"] in {"artifacts_ready", "failed", "canceled", "expired"}))):
            _fail("assignment_expired", 409)
        attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (row["task_id"],)).fetchone()
        started = bool(attempt and attempt["start_grant_id"])
        metadata_only = (row["state"] == "released" or task["state"] in
                         {"artifacts_ready", "failed", "canceled", "expired"})
        if not started and not metadata_only:
            if task["cancel_requested"] or task["deadline"] <= self.clock():
                _fail("task_not_executable", 409)
            membership = con.execute(
                "SELECT m.state,p.active FROM shared_workspaces m "
                "JOIN shared_pools p ON p.pool_id=m.pool_id "
                "WHERE m.pool_id=? AND m.source=? AND m.workspace=?",
                (row["pool_id"], task["source"], task["workspace"])).fetchone()
            if membership is None or membership["state"] != "active" or not membership["active"]:
                _fail("pool_membership_required", 403)
        if node["state"] != "active" and not (
                attempt and attempt["start_grant_id"]
                and attempt["original_node"] == identity.node_id
                and attempt["original_executor"] == identity.executor_instance
                and row["state"] in {"running", "execution_unknown", "awaiting_user",
                                     "artifacts_ready", "released", "failed", "canceled", "expired"}):
            _fail("node_revoked", 403)
        return row, task, SourceScope(task["source"], task["workspace"])

    def assignment_for_task(self, identity, task_id):
        from .domain import _fail
        with self._read_transaction() as con:
            rows = con.execute("SELECT assignment_id FROM shared_assignments WHERE task_id=? AND pool_id=? AND node_id=? AND executor=? ORDER BY assigned DESC LIMIT 1",
                               (task_id, identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
            if rows is None:
                _fail("assignment_identity_mismatch", 403)
            self._assigned_task(con, identity, rows["assignment_id"], task_id=task_id)
            return rows["assignment_id"]

    @staticmethod
    def _node_task_view(con, row, task, task_view):
        # Once an assignment is released or terminal, the node only needs a
        # minimal reconciliation receipt. Never re-serve prompt/reference data.
        if row["state"] == "released" or task["state"] in {
                "artifacts_ready", "failed", "canceled", "expired"}:
            return {"remote_task_id": task["id"], "attempt_id": task["attempt_id"],
                    "state": task_view["state"], "receipt_id": task_view["receipt_id"],
                    "delivery_ack": task_view["delivery_ack"],
                    "assignment_id": row["assignment_id"],
                    "assignment_state": row["state"],
                    "release_reason": row["release_reason"]}
        task_view["assignment_id"] = row["assignment_id"]
        task_view["assignment_state"] = row["state"]
        return task_view

    def get_assigned_task(self, identity, assignment_id, task_id=None):
        with self._read_transaction() as con:
            row, task, _scope = self._assigned_task(con, identity, assignment_id, task_id=task_id)
            return self._node_task_view(con, row, task, self._task_view(con, task))

    def get_assigned_task_for_task(self, identity, task_id):
        from .domain import _fail
        with self._read_transaction() as con:
            row = con.execute("SELECT assignment_id FROM shared_assignments WHERE task_id=? AND pool_id=? "
                              "AND node_id=? AND executor=? ORDER BY assigned DESC LIMIT 1",
                              (task_id, identity.pool_id, identity.node_id,
                               identity.executor_instance)).fetchone()
            if row is None:
                _fail("assignment_identity_mismatch", 403)
            assignment, task, _scope = self._assigned_task(
                con, identity, row["assignment_id"], task_id=task_id)
            return self._node_task_view(con, assignment, task, self._task_view(con, task))

    def _shared_node_state(self, con, identity):
        from .domain import _fail
        row = con.execute("SELECT state FROM shared_nodes WHERE pool_id=? AND node_id=? AND executor=?",
                          (identity.pool_id, identity.node_id, identity.executor_instance)).fetchone()
        if row is None:
            _fail("node_not_registered", 403)
        return row["state"]

    def _shared_claim(self, con, identity, assignment, task, claim_id, *, allow_started_revoked=True):
        from .domain import _fail
        claim = con.execute("SELECT * FROM claims WHERE task_id=?", (task["id"],)).fetchone()
        if (claim is None or claim["id"] != claim_id or claim["node"] != identity.node_id
                or claim["executor"] != identity.executor_instance):
            _fail("stale_claim", 409)
        if claim["expires"] <= self.clock():
            _fail("claim_expired", 409)
        attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
        node_state = self._shared_node_state(con, identity)
        if node_state != "active" and not (
                allow_started_revoked and attempt and attempt["start_grant_id"]
                and attempt["original_node"] == identity.node_id
                and attempt["original_executor"] == identity.executor_instance):
            _fail("node_revoked" if node_state == "revoked" else "node_draining", 403)
        if self._profile(task).id == "codex-image" and task["state"] == "execution_unknown" and claim["mode"] != "upload_only":
            _fail("upload_only_claim_required", 409)
        return claim

    def _active_membership(self, con, scope, pool_id):
        from .domain import _fail
        source, workspace = self._scope(scope)
        row = con.execute("SELECT m.state,p.active FROM shared_workspaces m JOIN shared_pools p ON p.pool_id=m.pool_id "
                          "WHERE m.source=? AND m.workspace=? AND m.pool_id=?",
                          (source, workspace, pool_id)).fetchone()
        if row is None or row["state"] != "active" or not row["active"]:
            _fail("pool_membership_required", 403)

    def claim_shared_assignment(self, identity, assignment_id, *, mode="execute", lease_seconds=3600):
        from .domain import _fail, _id, _integer
        if mode not in {"execute", "upload_only"}:
            _fail("invalid_claim_mode")
        _integer(lease_seconds, 1, 3600)
        now = self.clock()
        with self._transaction() as con:
            row, task, scope = self._assigned_task(con, identity, assignment_id, allow_finished=False)
            if row["state"] not in {"assigned", "claimed", "running", "execution_unknown", "awaiting_user"}:
                _fail("assignment_expired", 409)
            if row["state"] == "awaiting_user" and mode != "upload_only":
                _fail("upload_only_claim_required", 409)
            node_state = self._shared_node_state(con, identity)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if not attempt["start_grant_id"]:
                if node_state != "active":
                    _fail("node_revoked" if node_state == "revoked" else "node_draining", 403)
                self._active_membership(con, scope, identity.pool_id)
            if mode == "upload_only" and not attempt["start_grant_id"]:
                _fail("execution_not_started", 409)
            if mode == "execute" and attempt["start_grant_id"]:
                _fail("execution_already_started", 409)
            if task["state"] == "expired":
                _fail("task_expired", 409)
            if task["state"] in {"canceled", "failed", "artifacts_ready"} or task["cancel_requested"]:
                _fail("task_not_executable", 409)
            if (not attempt["start_grant_id"] or self._profile(task).execution_mode == "manual") and task["deadline"] <= now:
                _fail("task_expired", 409)
            if (attempt["start_grant_id"] and mode == "upload_only"
                    and (attempt["original_node"] != identity.node_id
                         or attempt["original_executor"] != identity.executor_instance)):
                _fail("upload_only_identity_mismatch", 409)
            old = con.execute("SELECT * FROM claims WHERE task_id=?", (task["id"],)).fetchone()
            if old and old["expires"] > now:
                if (old["node"], old["executor"], old["mode"]) != (identity.node_id, identity.executor_instance, mode):
                    _fail("claim_busy", 409)
                return self._claim_view(old, task["attempt_id"])
            epoch = old["epoch"] + 1 if old else 1
            claim_id = _id("claim")
            expires = now + lease_seconds
            con.execute("INSERT OR REPLACE INTO claims VALUES(?,?,?,?,?,?,?)",
                        (task["id"], claim_id, epoch, identity.node_id,
                         identity.executor_instance, mode, expires))
            if row["state"] in {"assigned", "claimed"}:
                con.execute("UPDATE shared_assignments SET state='claimed',lease_expires=? WHERE assignment_id=?",
                            (expires, assignment_id))
            return self._claim_view(con.execute("SELECT * FROM claims WHERE task_id=?", (task["id"],)).fetchone(),
                                    task["attempt_id"])

    def start_shared_execution(self, identity, assignment_id, claim_id):
        from .domain import _fail, _id
        with self._transaction() as con:
            row, task, scope = self._assigned_task(con, identity, assignment_id, allow_finished=False)
            if self._profile(task).execution_mode != "automatic":
                _fail("manual_profile_requires_manual_ready", 409)
            claim = self._shared_claim(con, identity, row, task, claim_id, allow_started_revoked=False)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if attempt["start_grant_id"]:
                return {"start_grant_id": attempt["start_grant_id"],
                        "attempt_id": attempt["id"], "may_execute": False}
            self._active_membership(con, scope, identity.pool_id)
            if row["state"] not in {"assigned", "claimed"}:
                _fail("assignment_expired", 409)
            if task["cancel_requested"] or task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                _fail("task_not_executable", 409)
            if claim["mode"] != "execute":
                _fail("upload_only_claim", 409)
            if task["deadline"] <= self.clock():
                _fail("task_expired", 409)
            occupied = con.execute("""SELECT t.* FROM attempts a JOIN tasks t ON t.id=a.task_id
                WHERE a.start_grant_id IS NOT NULL AND t.id!=?
                AND NOT EXISTS (SELECT 1 FROM shared_assignments sa WHERE sa.task_id=t.id)
                AND t.state NOT IN ('artifacts_ready','canceled','failed','expired')""",
                                   (task["id"],)).fetchall()
            if any(self._profile(other).execution_mode == "automatic" for other in occupied):
                _fail("physical_slot_busy", 409)
            grant = _id("grant")
            now = self.clock()
            con.execute("UPDATE attempts SET start_grant_id=?,original_node=?,original_executor=?,started=? WHERE id=?",
                        (grant, identity.node_id, identity.executor_instance, now, attempt["id"]))
            con.execute("DELETE FROM task_attention WHERE task_id=?", (task["id"],))
            con.execute("UPDATE tasks SET state='running' WHERE id=?", (task["id"],))
            con.execute("UPDATE shared_assignments SET state='running',started=?,lease_expires=? WHERE assignment_id=?",
                        (now, claim["expires"], assignment_id))
            self._record_node_execution_start(con, attempt["id"], identity,
                                              self._profile(task).id, "running")
            self._audit(con, scope, "start_granted", task_id=task["id"], attempt_id=attempt["id"])
            return {"start_grant_id": grant, "attempt_id": attempt["id"], "may_execute": True}

    def manual_ready_shared(self, identity, assignment_id, claim_id):
        from .domain import _fail, _id
        with self._transaction() as con:
            row, task, scope = self._assigned_task(con, identity, assignment_id, allow_finished=False)
            if self._profile(task).execution_mode != "manual":
                _fail("automatic_profile_requires_start", 409)
            claim = self._shared_claim(con, identity, row, task, claim_id, allow_started_revoked=False)
            if task["cancel_requested"] or task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                _fail("task_not_executable", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if not attempt["start_grant_id"]:
                self._active_membership(con, scope, identity.pool_id)
                if claim["mode"] != "execute":
                    _fail("upload_only_claim", 409)
                if task["deadline"] <= self.clock():
                    _fail("task_expired", 409)
                pending = con.execute("""SELECT t.* FROM tasks t JOIN attempts a ON a.task_id=t.id
                    WHERE a.start_grant_id IS NOT NULL AND t.state NOT IN ('artifacts_ready','canceled','failed','expired')""").fetchall()
                if sum(self._profile(item).execution_mode == "manual" and item["deadline"] > self.clock()
                       for item in pending) >= 3:
                    _fail("manual_capacity", 429)
                now = self.clock()
                con.execute("UPDATE attempts SET start_grant_id=?,original_node=?,original_executor=?,started=? WHERE id=?",
                            (_id("manual"), identity.node_id, identity.executor_instance, now, attempt["id"]))
                con.execute("UPDATE tasks SET state='awaiting_user' WHERE id=?", (task["id"],))
                con.execute("UPDATE shared_assignments SET state='awaiting_user',started=?,lease_expires=? WHERE assignment_id=?",
                            (now, claim["expires"], assignment_id))
                self._record_node_execution_start(con, attempt["id"], identity,
                                                  self._profile(task).id, "awaiting_user")
                self._audit(con, scope, "manual_ready", task_id=task["id"], attempt_id=attempt["id"])
            con.execute("DELETE FROM task_attention WHERE task_id=?", (task["id"],))
            return {"attempt_id": attempt["id"], "state": "awaiting_user", "may_execute": False}

    def assigned_input_content(self, identity, assignment_id, task_id, input_id, claim_id):
        from .domain import _fail
        with self._read_transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id,
                                                          task_id=task_id, allow_finished=False)
            self._shared_claim(con, identity, assignment, task, claim_id)
            pin = con.execute("SELECT 1 FROM input_pins WHERE task_id=? AND input_id=?",
                              (task["id"], input_id)).fetchone()
            if pin is None:
                _fail("not_found", 404)
            row = self._scoped(con, "inputs", input_id, scope)
            return self._read_bytes(row["file_name"], json.loads(row["payload"])), json.loads(row["payload"])["mime_type"]

    def mark_shared_execution_unknown(self, identity, assignment_id, claim_id):
        from .domain import _fail
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id, allow_finished=False)
            if self._profile(task).execution_mode != "automatic":
                _fail("manual_profile_requires_manual_ready", 409)
            self._shared_claim(con, identity, assignment, task, claim_id)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if not attempt["start_grant_id"]:
                _fail("execution_not_started", 409)
            if task["state"] not in {"canceled", "failed", "expired", "artifacts_ready"}:
                if task["state"] != "execution_unknown":
                    self._audit(con, scope, "execution_unknown", task_id=task["id"], attempt_id=attempt["id"])
                if not task["cancel_requested"]:
                    con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=?", (task["id"],))
                con.execute("UPDATE shared_assignments SET state='execution_unknown' WHERE assignment_id=?",
                            (assignment["assignment_id"],))
                self._update_node_execution(con, attempt["id"], state="execution_unknown")
                self._pool_event(con, identity.pool_id, "execution_unknown", source=scope.source_id,
                                 workspace=scope.workspace_id, node_id=identity.node_id,
                                 task_id=task["id"], assignment_id=assignment["assignment_id"])
            return self._task_view(con, self._scoped(con, "tasks", task["id"], scope))

    def report_shared_attention(self, identity, assignment_id, attempt_id, code):
        from .domain import _fail
        from .readiness import ATTENTION_MESSAGES
        if code not in ATTENTION_MESSAGES:
            _fail("invalid_attention_code")
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id, allow_finished=False)
            if task["attempt_id"] != attempt_id:
                _fail("attempt_mismatch", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if attempt["original_node"] is not None and (
                    attempt["original_node"], attempt["original_executor"]) != (
                    identity.node_id, identity.executor_instance):
                _fail("node_binding_mismatch", 403)
            if task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                return self._task_view(con, task)
            started = bool(attempt["start_grant_id"]) and self._profile(task).execution_mode == "automatic"
            if task["cancel_requested"] and not started:
                return self._task_view(con, task)
            old = con.execute("SELECT code FROM task_attention WHERE task_id=?", (task["id"],)).fetchone()
            if old is None or old["code"] != code:
                self._audit(con, scope, "task_attention", task_id=task["id"], attempt_id=attempt_id, code=code)
            con.execute("INSERT OR REPLACE INTO task_attention VALUES(?,?)", (task["id"], code))
            if not task["cancel_requested"]:
                new_state = "execution_unknown" if started else "awaiting_user"
                con.execute("UPDATE tasks SET state=? WHERE id=?", (new_state, task["id"]))
                if not started:
                    con.execute("UPDATE shared_assignments SET state='awaiting_user',released=?,release_reason='attention' WHERE assignment_id=?",
                                (self.clock(), assignment["assignment_id"]))
                    self._update_node_execution(con, attempt["id"], state="awaiting_user")
                else:
                    con.execute("UPDATE shared_assignments SET state='execution_unknown' WHERE assignment_id=?",
                                (assignment["assignment_id"],))
                    self._update_node_execution(con, attempt["id"], state="execution_unknown")
            return self._task_view(con, self._scoped(con, "tasks", task["id"], scope))

    def create_assigned_upload(self, identity, assignment_id, task_id, claim_id, payload):
        from .domain import _fail, _object, _digest, _canonical, _id
        _object(payload, ("attempt_id", "slot_id", "size", "mime_type"))
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id, task_id=task_id)
            self._media_metadata(payload, output=True, profile=self._profile(task))
            claim = self._shared_claim(con, identity, assignment, task, claim_id)
            self._require_upload_claim_mode(task, claim)
            if payload["attempt_id"] != task["attempt_id"] or payload["slot_id"] != "primary":
                _fail("upload_binding_mismatch", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if not attempt["start_grant_id"]:
                _fail("execution_not_started", 409)
            old = con.execute("SELECT * FROM uploads WHERE task_id=?", (task["id"],)).fetchone()
            digest = _digest(payload)
            if old:
                if old["digest"] != digest:
                    _fail("idempotency_conflict", 409)
                return self._upload_view(old)
            if task["cancel_requested"] or task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                _fail("task_not_accepting", 409)
            upload_id = _id("up")
            con.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (upload_id, task["id"], task["attempt_id"], "primary", digest,
                         _canonical(payload), "pending", payload["size"], None, None))
            con.execute("UPDATE tasks SET state='uploading' WHERE id=?", (task["id"],))
            self._update_node_execution(con, task["attempt_id"], state="uploading")
            return self._upload_view(con.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone())

    def put_assigned_upload_content(self, identity, assignment_id, upload_id, claim_id, content):
        from .domain import _fail, _canonical
        from .image_options import requested_output_mime
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id)
            upload, upload_task = self._upload_row(con, upload_id, scope)
            if upload_task["id"] != task["id"]:
                _fail("assignment_identity_mismatch", 403)
            claim = self._shared_claim(con, identity, assignment, task, claim_id)
            self._require_upload_claim_mode(task, claim)
            payload = json.loads(upload["payload"])
            dimensions = self._verify_media(content, payload, profile=self._profile(task), output=True)
            request = json.loads(task["payload"])
            parameters = request["parameters"]
            expected_mime = requested_output_mime(parameters, self._profile(task).id)
            if expected_mime and payload["mime_type"] != expected_mime:
                _fail("output_format_mismatch", 422)
            if (self._profile(task).id != "codex-image" and dimensions != {
                    "width": parameters.get("width", 32),
                    "height": parameters.get("height", 32)}):
                _fail("output_dimensions_mismatch", 422)
            if upload["state"] in {"ready", "committed"}:
                self._read_bytes(upload["file_name"], payload)
                return self._upload_view(upload)
            if task["cancel_requested"] or task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                _fail("task_not_accepting", 409)
            file_name = self._store_bytes(con, upload_id, content)
            con.execute("UPDATE uploads SET state='ready',file_name=?,dimensions=? WHERE id=?",
                        (file_name, _canonical(dimensions), upload_id))
            return self._upload_view(con.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone())

    def authorize_assigned_upload(self, identity, assignment_id, upload_id, claim_id):
        from .domain import _fail
        with self._read_transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id)
            upload, upload_task = self._upload_row(con, upload_id, scope)
            if upload_task["id"] != task["id"]:
                _fail("assignment_identity_mismatch", 403)
            claim = self._shared_claim(con, identity, assignment, task, claim_id)
            self._require_upload_claim_mode(task, claim)
            payload = json.loads(upload["payload"])
            return {"size": payload["size"], "mime_type": payload["mime_type"]}

    def commit_assigned_upload(self, identity, assignment_id, upload_id, claim_id):
        from .domain import _fail, _id, _utc, _canonical
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id)
            upload, upload_task = self._upload_row(con, upload_id, scope)
            if upload_task["id"] != task["id"]:
                _fail("assignment_identity_mismatch", 403)
            claim = self._shared_claim(con, identity, assignment, task, claim_id)
            self._require_upload_claim_mode(task, claim)
            old = con.execute("SELECT * FROM receipts WHERE upload_id=?", (upload_id,)).fetchone()
            if old:
                return self._receipt_view(con, old)
            if task["cancel_requested"] or task["state"] in {"canceled", "failed", "expired", "artifacts_ready"}:
                _fail("task_not_accepting", 409)
            if upload["state"] != "ready":
                _fail("upload_not_ready", 409)
            payload = json.loads(upload["payload"])
            self._read_bytes(upload["file_name"], payload)
            receipt_id, artifact_id = _id("receipt"), _id("artifact")
            manifest = {"receipt_id": receipt_id, "task_id": task["id"],
                        "attempt_id": task["attempt_id"], "slot_id": upload["slot_id"],
                        "execution_profile_id": self._profile(task).id,
                        "execution_profile_revision": self._profile(task).revision,
                        "artifact_id": artifact_id, "size": payload["size"],
                        "mime_type": payload["mime_type"],
                        "dimensions": json.loads(upload["dimensions"]),
                        "accepted_at": _utc(self.clock())}
            con.execute("INSERT INTO receipts VALUES(?,?,?,?,?)",
                        (receipt_id, task["id"], upload_id, artifact_id, _canonical(manifest)))
            con.execute("UPDATE uploads SET state='committed' WHERE id=?", (upload_id,))
            con.execute("UPDATE tasks SET state='artifacts_ready' WHERE id=?", (task["id"],))
            self._update_node_execution(con, task["attempt_id"], state="succeeded",
                                        completed=True, artifact_count=1)
            self._audit(con, scope, "receipt_committed", task_id=task["id"],
                        attempt_id=task["attempt_id"], receipt_id=receipt_id)
            self._release_shared_task(con, task["id"], "artifacts_ready")
            return self._receipt_view(con, con.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone())

    def confirm_shared_terminal_failure(self, identity, assignment_id, payload):
        from .domain import _fail, _canonical, _id
        from .schemas import TerminalFailureRequest
        try:
            payload = TerminalFailureRequest.model_validate(payload).model_dump()
        except ValueError:
            _fail("invalid_terminal_failure")
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if (self._profile(task).id != "codex-image" or not attempt["start_grant_id"]
                    or payload["attempt_id"] != task["attempt_id"]
                    or (attempt["original_node"], attempt["original_executor"]) !=
                       (identity.node_id, identity.executor_instance)):
                _fail("terminal_failure_binding_mismatch", 409)
            old = con.execute("SELECT * FROM terminal_failures WHERE task_id=?", (task["id"],)).fetchone()
            canonical = _canonical(payload)
            if old:
                if old["payload"] != canonical:
                    _fail("terminal_failure_binding_mismatch", 409)
                return json.loads(old["confirmation"])
            if (task["state"] not in {"running", "execution_unknown", "cancel_requested"}
                    or con.execute("SELECT 1 FROM receipts WHERE task_id=?", (task["id"],)).fetchone()
                    or con.execute("SELECT 1 FROM uploads WHERE task_id=?", (task["id"],)).fetchone()):
                _fail("terminal_failure_not_safe", 409)
            confirmation = {**payload, "task_id": task["id"], "status": "failed",
                            "terminal_failure_ref": _id("failure")}
            con.execute("INSERT INTO terminal_failures VALUES(?,?,?)",
                        (task["id"], canonical, _canonical(confirmation)))
            con.execute("UPDATE tasks SET state='failed' WHERE id=?", (task["id"],))
            self._update_node_execution(con, task["attempt_id"], state="failed",
                                        completed=True, failure_code=payload["evidence"]["reason_code"])
            con.execute("DELETE FROM task_attention WHERE task_id=?", (task["id"],))
            self._audit(con, scope, "terminal_failure_confirmed", task_id=task["id"],
                        attempt_id=task["attempt_id"])
            self._release_shared_task(con, task["id"], "failed")
            return confirmation

    def confirm_shared_cancelled_completion(self, identity, assignment_id, payload):
        from .domain import _fail, _canonical
        from .schemas import CancelCompletedRequest
        try:
            payload = CancelCompletedRequest.model_validate(payload).model_dump()
        except ValueError:
            _fail("invalid_cancel_completion")
        with self._transaction() as con:
            assignment, task, scope = self._assigned_task(con, identity, assignment_id)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task["id"],)).fetchone()
            if (self._profile(task).id != "codex-image" or not attempt["start_grant_id"]
                    or payload["attempt_id"] != task["attempt_id"]
                    or (attempt["original_node"], attempt["original_executor"]) !=
                       (identity.node_id, identity.executor_instance)):
                _fail("cancel_completion_binding_mismatch", 409)
            old = con.execute("SELECT * FROM cancel_completions WHERE task_id=?", (task["id"],)).fetchone()
            if old:
                if old["attempt_id"] != payload["attempt_id"]:
                    _fail("cancel_completion_binding_mismatch", 409)
                return json.loads(old["confirmation"])
            if (task["state"] != "cancel_requested" or not task["cancel_requested"]
                    or con.execute("SELECT 1 FROM receipts WHERE task_id=?", (task["id"],)).fetchone()
                    or con.execute("SELECT 1 FROM uploads WHERE task_id=?", (task["id"],)).fetchone()):
                _fail("cancel_completion_not_safe", 409)
            confirmation = {"task_id": task["id"], "attempt_id": payload["attempt_id"], "status": "canceled"}
            con.execute("INSERT INTO cancel_completions VALUES(?,?,?)",
                        (task["id"], payload["attempt_id"], _canonical(confirmation)))
            con.execute("UPDATE tasks SET state='canceled' WHERE id=?", (task["id"],))
            self._update_node_execution(con, task["attempt_id"], state="cancelled", completed=True)
            self._audit(con, scope, "cancel_completed", task_id=task["id"],
                        attempt_id=payload["attempt_id"])
            self._release_shared_task(con, task["id"], "canceled")
            return confirmation

    def _release_shared_task(self, con, task_id, terminal_state):
        row = con.execute("SELECT * FROM shared_assignments WHERE task_id=? AND state IN (" + ACTIVE_ASSIGNMENT_SQL + ")",
                          (task_id,)).fetchone()
        if row is None:
            return
        con.execute("UPDATE shared_assignments SET state=?,released=?,release_reason=? WHERE assignment_id=?",
                    (terminal_state, self.clock(), terminal_state, row["assignment_id"]))
        con.execute("DELETE FROM claims WHERE task_id=?", (task_id,))
        self._pool_event(con, row["pool_id"], "assignment_artifact_ready" if terminal_state == "artifacts_ready"
                         else "assignment_released", node_id=row["node_id"], task_id=task_id,
                         assignment_id=row["assignment_id"], reason=terminal_state)
