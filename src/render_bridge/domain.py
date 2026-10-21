"""Bounded single-workspace development ledger. This is not a task engine.

Node methods are absent from the source API and only exposed by configured node routes.
All mutable decisions are serialized by SQLite transactions. Files are private,
server-named, immutable after validation, and small enough for bounded reads.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from .profiles import get_profile, enabled_profile_ids, MAX_PROFILE_OUTPUT_BYTES
from .image_options import validate_image_request, requested_output_mime

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_IMAGE_PIXELS = 1_048_576
MAX_STORAGE_BYTES = 16 * MAX_INPUT_BYTES
PROFILE_ID = "fake-image"
PROFILE_REVISION = "1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ORIGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MIMES = {"image/png": "PNG", "image/jpeg": "JPEG"}
_TERMINAL = {"canceled", "failed", "expired", "artifacts_ready"}


class DomainError(Exception):
    def __init__(self, code: str, status: int = 400, message: str | None = None):
        self.code = code
        self.status = status
        super().__init__(message or code)


@dataclass(frozen=True)
class SourceScope:
    source_id: str
    workspace_id: str


def _fail(code, status=400):
    raise DomainError(code, status)


def _object(value, required, optional=()):
    if not isinstance(value, dict):
        _fail("invalid_request")
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        _fail("invalid_fields")


def _text(value, maximum=128, *, multiline=False):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        _fail("invalid_string")
    if any(ord(c) < 32 and not (multiline and c in "\n\r\t") for c in value):
        _fail("invalid_string")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail("invalid_string")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail("invalid_integer")
    return value


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _fail("invalid_hash")
    return value


def _origin(value):
    if not isinstance(value, str) or not _ORIGIN.fullmatch(value):
        _fail("invalid_origin_key")
    return value


def _canonical(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False)
        result.encode("utf-8")
        return result
    except (ValueError, TypeError, UnicodeError):
        _fail("invalid_json")


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class BridgeStore:
    """SQLite-backed isolated fixture store; separate instances may share a DB.

    POSIX writes synchronize the file and its containing directory. Windows
    supports this contract harness's file fsync and same-directory replacement,
    but Python cannot fsync a directory there. Windows power-loss durability is
    therefore NOT verified or promised by this experimental store.
    """

    def __init__(self, db_path, artifact_root, *, clock=None, enabled_profiles=("fake-image",), accepting_new_tasks=True):
        self.enabled_profiles = enabled_profile_ids(enabled_profiles)
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("Use a file-backed database for persistence tests")
        if type(accepting_new_tasks) is not bool:
            raise ValueError("accepting_new_tasks must be boolean")
        self.accepting_new_tasks = accepting_new_tasks
        raw_clock = clock or time.time
        def timestamp_clock():
            value = raw_clock()
            return value.timestamp() if isinstance(value, datetime) else value
        self.clock = timestamp_clock
        root = Path(artifact_root)
        if root.is_symlink():
            raise ValueError("artifact_root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()
        os.chmod(self.root, 0o700)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS node_heartbeats (
                    source TEXT NOT NULL, workspace TEXT NOT NULL, node TEXT NOT NULL,
                    executor TEXT NOT NULL, seen REAL NOT NULL, state TEXT NOT NULL,
                    profiles TEXT NOT NULL, PRIMARY KEY(source,workspace,node,executor));
                CREATE TABLE IF NOT EXISTS task_attention (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id), code TEXT NOT NULL);

                CREATE TABLE IF NOT EXISTS inputs (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, workspace TEXT NOT NULL,
                    input_key TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, expires REAL NOT NULL, size INTEGER NOT NULL,
                    file_name TEXT, dimensions TEXT,
                    UNIQUE(source, workspace, input_key));
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, workspace TEXT NOT NULL,
                    origin_key TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, deadline REAL NOT NULL,
                    UNIQUE(source, workspace, origin_key));
                CREATE TABLE IF NOT EXISTS input_pins (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    input_id TEXT NOT NULL REFERENCES inputs(id),
                    PRIMARY KEY(task_id, input_id));
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                    start_grant_id TEXT UNIQUE, original_node TEXT, original_executor TEXT,
                    started REAL);
                CREATE TABLE IF NOT EXISTS claims (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id), id TEXT NOT NULL UNIQUE,
                    epoch INTEGER NOT NULL, node TEXT NOT NULL, executor TEXT NOT NULL,
                    mode TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    attempt_id TEXT NOT NULL REFERENCES attempts(id), slot_id TEXT NOT NULL,
                    digest TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    size INTEGER NOT NULL, file_name TEXT, dimensions TEXT,
                    UNIQUE(task_id, attempt_id, slot_id));
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                    upload_id TEXT NOT NULL UNIQUE REFERENCES uploads(id),
                    artifact_id TEXT NOT NULL UNIQUE, manifest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_outbox (
                    dispatch_key TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                    engine_run_id TEXT, acknowledged REAL);
                CREATE TABLE IF NOT EXISTS retention_tasks (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    eligible_since REAL NOT NULL, reclaimed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS artifact_deletions (
                    file_name TEXT PRIMARY KEY, source TEXT NOT NULL, workspace TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS acknowledgements (
                    receipt_id TEXT PRIMARY KEY REFERENCES receipts(id),
                    source TEXT NOT NULL, workspace TEXT NOT NULL,
                    client_receipt_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(source, workspace, client_receipt_id));
            """)
            con.execute("""INSERT OR IGNORE INTO dispatch_outbox(dispatch_key,task_id)
                SELECT 'dispatch_' || id, id FROM tasks WHERE state='queued'""")
        os.chmod(self.db_path, 0o600)

    def close(self):
        """Connections are operation-local; retained for fixture lifecycle symmetry."""

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    @contextmanager
    def _read_transaction(self):
        """Keep compound response reads on one WAL snapshot."""
        with self._connection() as con:
            con.execute("BEGIN")
            try:
                yield con
            finally:
                con.rollback()

    @staticmethod
    def _scope(scope):
        if not isinstance(scope, SourceScope):
            _fail("invalid_scope", 403)
        _text(scope.source_id)
        _text(scope.workspace_id)
        return scope.source_id, scope.workspace_id

    def _scoped(self, con, table, object_id, scope):
        source, workspace = self._scope(scope)
        _text(object_id)
        row = con.execute(f"SELECT * FROM {table} WHERE id=? AND source=? AND workspace=?",
                          (object_id, source, workspace)).fetchone()
        if row is None:
            _fail("not_found", 404)
        return row

    def _receipt_row(self, con, receipt_id, scope):
        row = con.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if row is None:
            _fail("not_found", 404)
        self._scoped(con, "tasks", row["task_id"], scope)
        return row

    def _upload_row(self, con, upload_id, scope):
        row = con.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if row is None:
            _fail("not_found", 404)
        return row, self._scoped(con, "tasks", row["task_id"], scope)

    def _check_claim(self, con, task, claim_id):
        if (self._profile(task).execution_mode == "manual" and task["deadline"] <= self.clock()
                and task["state"] not in _TERMINAL):
            _fail("task_expired", 409)
        claim = con.execute("SELECT * FROM claims WHERE task_id=?", (task["id"],)).fetchone()
        if not claim or claim["id"] != claim_id:
            _fail("stale_claim", 409)
        if claim["expires"] <= self.clock():
            _fail("claim_expired", 409)
        return claim

    def report_heartbeat(self, scope, node_id, executor_instance, state, profiles):
        source, workspace = self._scope(scope)
        if state not in {"available", "busy", "attention"}:
            _fail("invalid_node_status")
        if not isinstance(profiles, list) or len(profiles) > 3 or len(set(profiles)) != len(profiles):
            _fail("invalid_node_profiles")
        for profile in profiles:
            if profile not in {"fake-image", "codex-image", "midjourney-manual"}:
                _fail("invalid_node_profiles")
        with self._transaction() as con:
            con.execute("INSERT OR REPLACE INTO node_heartbeats VALUES(?,?,?,?,?,?,?)",
                        (source, workspace, node_id, executor_instance, self.clock(), state, _canonical(profiles)))
        return {"accepted": True, "heartbeat_timeout_seconds": 90}

    def node_status(self, scope):
        source, workspace = self._scope(scope)
        with self._connection() as con:
            row = con.execute("SELECT * FROM node_heartbeats WHERE source=? AND workspace=? ORDER BY seen DESC LIMIT 1",
                              (source, workspace)).fetchone()
        return {"state": ("unknown" if row is None else "offline" if self.clock() - row["seen"] >= 90 else row["state"]),
                "last_seen_at": _utc(row["seen"]) if row else None,
                "heartbeat_timeout_seconds": 90,
                "reported_profiles": json.loads(row["profiles"]) if row else [],
                "login_verified": False, "real_generation_verified": False}

    def report_attention(self, scope, task_id, attempt_id, node_id, executor_instance, code):
        from .readiness import ATTENTION_MESSAGES
        if code not in ATTENTION_MESSAGES:
            _fail("invalid_attention_code")
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            if task["attempt_id"] != attempt_id:
                _fail("attempt_mismatch", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if attempt["original_node"] is not None and (attempt["original_node"] != node_id or attempt["original_executor"] != executor_instance):
                _fail("node_binding_mismatch", 403)
            if task["state"] in _TERMINAL or task["cancel_requested"] or (not attempt["start_grant_id"] and task["deadline"] <= self.clock()):
                return self._task_view(con, task)
            started = bool(attempt["start_grant_id"]) and self._profile(task).execution_mode == "automatic"
            con.execute("INSERT OR REPLACE INTO task_attention VALUES(?,?)", (task_id, code))
            con.execute("UPDATE tasks SET state=? WHERE id=?", ("execution_unknown" if started else "awaiting_user", task_id))
            return self._task_view(con, self._scoped(con, "tasks", task_id, scope))

    def capabilities(self, scope):
        self._scope(scope)
        node = self.node_status(scope)
        return {
            "schema_version": "1",
            "admission": {"accepting_new_tasks": self.accepting_new_tasks,
                          "mode": "enabled" if self.accepting_new_tasks else "draining"},
            "node_status": node,
            "profiles": [{
                "execution_profile_id": profile.id,
                "execution_profile_revision": profile.revision,
                "operation": "image.generate", "execution_mode": profile.execution_mode,
                "requires_human": profile.execution_mode == "manual",
                "validation": "isolated_fake_only" if profile.id == "fake-image" else "local_validation_pending",
                "production_verified": False,
                "availability": (node["state"] if node["state"] in {"unknown", "offline", "busy"}
                                 else "attention" if profile.id not in node["reported_profiles"] or profile.id == "codex-image"
                                 else node["state"]),
                "parameter_contract": profile.parameter_contract(),
                "image_options_contract": profile.image_options_contract(),
                "limits": {"max_output_bytes": profile.max_output_bytes,
                           "max_image_pixels": profile.max_pixels,
                           "max_dimension": profile.max_dimension},
            } for profile in map(get_profile, self.enabled_profiles)],
            "limits": {"max_input_bytes": MAX_INPUT_BYTES,
                       "max_output_bytes": max(get_profile(p).max_output_bytes for p in self.enabled_profiles),
                       "max_image_pixels": max(get_profile(p).max_pixels for p in self.enabled_profiles), "max_references": 8,
                       "max_queued_tasks": 10, "max_manual_pending": 3},
        }

    @staticmethod
    def _media_metadata(payload, *, output=False, profile=None):
        _hash(payload["original_sha256"])
        _integer(payload["size"], 1, (profile.max_output_bytes if profile else MAX_OUTPUT_BYTES) if output else MAX_INPUT_BYTES)
        if not isinstance(payload["mime_type"], str) or payload["mime_type"] not in _MIMES:
            _fail("unsupported_media_type", 415)

    def _verify_media(self, content, payload, *, profile=None):
        if not isinstance(content, bytes):
            _fail("invalid_content")
        if len(content) > (profile.max_output_bytes if profile else MAX_INPUT_BYTES):
            _fail("file_too_large", 413)
        if len(content) != payload["size"]:
            _fail("size_mismatch", 422)
        if hashlib.sha256(content).hexdigest() != payload["original_sha256"]:
            _fail("hash_mismatch", 422)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as img:
                    width, height = img.size
                    if (width < 1 or height < 1 or width * height > (profile.max_pixels if profile else MAX_IMAGE_PIXELS)
                            or (profile and max(width, height) > profile.max_dimension)):
                        _fail("image_too_large", 413)
                    if img.format != _MIMES[payload["mime_type"]]:
                        _fail("mime_mismatch", 422)
                    if getattr(img, "n_frames", 1) != 1:
                        _fail("animated_media_unsupported", 422)
                    img.verify()
                with Image.open(io.BytesIO(content)) as img:
                    img.load()
            return {"width": width, "height": height}
        except DomainError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
                Image.DecompressionBombError, Image.DecompressionBombWarning):
            _fail("invalid_media", 422)

    def _sync_artifact_directory(self):
        # Windows' CRT os.open cannot open directory handles. Do not turn this
        # specific platform limitation into a blanket ignore of I/O errors:
        # on POSIX, open/fsync/close failures must still abort the transaction.
        if sys.platform == "win32":
            return
        directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _store_bytes(self, con, object_id, content):
        file_name = object_id + ".blob"
        target = self.root / file_name
        try:
            existing = target.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            # Rename can survive a transaction rollback. Reuse only this exact
            # immutable object after validating its inode type, size, and bytes.
            if not stat.S_ISREG(existing.st_mode):
                _fail("storage_integrity", 503)
            self._read_bytes(file_name, {"size": len(content),
                                        "original_sha256": hashlib.sha256(content).hexdigest()})
            # A prior rename may have succeeded but directory fsync failed.
            # Retrying the same bytes must not bypass the durability barrier.
            self._sync_artifact_directory()
            return file_name
        used = con.execute("""SELECT
            COALESCE((SELECT SUM(size) FROM inputs WHERE file_name IS NOT NULL), 0) +
            COALESCE((SELECT SUM(size) FROM uploads WHERE file_name IS NOT NULL), 0)"""
                           ).fetchone()[0]
        # Include orphaned files from an interrupted transaction in the disk budget.
        disk_used = sum(info.st_size for path in self.root.iterdir()
                        if stat.S_ISREG((info := path.lstat()).st_mode))
        if max(used, disk_used) + len(content) > (MAX_STORAGE_BYTES if self.enabled_profiles == ("fake-image",) else 16 * MAX_PROFILE_OUTPUT_BYTES):
            _fail("storage_capacity", 503)
        fd, temp_name = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.root / file_name)
            self._sync_artifact_directory()
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return file_name

    def _read_bytes(self, file_name, payload):
        if not re.fullmatch(r"(?:in|up)_[0-9a-f]{32}\.blob", file_name or ""):
            _fail("storage_unavailable", 503)
        try:
            path = self.root / file_name
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                _fail("storage_unavailable", 503)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size != payload["size"]
                        or not os.path.samestat(before, info)):
                    _fail("storage_unavailable", 503)
                content = handle.read(MAX_PROFILE_OUTPUT_BYTES + 1)
        except OSError:
            _fail("storage_unavailable", 503)
        if len(content) != payload["size"] or hashlib.sha256(content).hexdigest() != payload["original_sha256"]:
            _fail("storage_integrity", 503)
        return content

    def _input_view(self, row):
        data = json.loads(row["payload"])
        return {"input_id": row["id"], **data, "state": row["state"],
                "expires_at": _utc(row["expires"]),
                "dimensions": json.loads(row["dimensions"]) if row["dimensions"] else None}

    def create_input(self, scope, payload):
        source, workspace = self._scope(scope)
        _object(payload, ("input_key", "original_sha256", "size", "mime_type"))
        _text(payload["input_key"])
        self._media_metadata(payload)
        digest = _digest(payload)
        with self._transaction() as con:
            old = con.execute("SELECT * FROM inputs WHERE source=? AND workspace=? AND input_key=?",
                              (source, workspace, payload["input_key"])).fetchone()
            if old:
                if old["digest"] != digest:
                    _fail("idempotency_conflict", 409)
                return self._input_view(old)
            if con.execute("SELECT COUNT(*) FROM inputs WHERE state!='expired'").fetchone()[0] >= 100:
                _fail("input_capacity", 429)
            input_id = _id("in")
            con.execute("INSERT INTO inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (input_id, source, workspace, payload["input_key"], digest,
                         _canonical(payload), "pending", self.clock() + 3600, payload["size"],
                         None, None))
            return self._input_view(self._scoped(con, "inputs", input_id, scope))

    def put_input_content(self, scope, input_id, content):
        with self._transaction() as con:
            row = self._scoped(con, "inputs", input_id, scope)
            payload = json.loads(row["payload"])
            dimensions = self._verify_media(content, payload)
            pinned = con.execute("SELECT 1 FROM input_pins WHERE input_id=?", (input_id,)).fetchone()
            if row["expires"] <= self.clock() and not pinned:
                _fail("input_expired", 410)
            if row["state"] == "ready":
                self._read_bytes(row["file_name"], payload)
                return self._input_view(row)
            file_name = self._store_bytes(con, input_id, content)
            con.execute("UPDATE inputs SET state='ready',file_name=?,dimensions=? WHERE id=?",
                        (file_name, _canonical(dimensions), input_id))
            return self._input_view(self._scoped(con, "inputs", input_id, scope))

    def get_input(self, scope, input_id):
        with self._read_transaction() as con:
            row = self._scoped(con, "inputs", input_id, scope)
            if row["state"] == "ready":
                self._read_bytes(row["file_name"], json.loads(row["payload"]))
            return self._input_view(row)

    def _expire_inputs(self, con, source, workspace, limit):
        rows = con.execute("""SELECT * FROM inputs WHERE source=? AND workspace=? AND expires<=?
            AND state!='expired'
            AND NOT EXISTS(SELECT 1 FROM input_pins WHERE input_id=inputs.id)
            ORDER BY expires, id LIMIT ?""", (source, workspace, self.clock(), limit)).fetchall()
        for row in rows:
            self._queue_deletion(con, row["file_name"], source, workspace)
            con.execute("UPDATE inputs SET state='expired',file_name=NULL WHERE id=?", (row["id"],))
        return len(rows)

    @staticmethod
    def _queue_deletion(con, file_name, source, workspace):
        if file_name:
            if not re.fullmatch(r"(?:in|up)_[0-9a-f]{32}\.blob", file_name):
                _fail("storage_integrity", 503)
            con.execute("INSERT OR IGNORE INTO artifact_deletions VALUES(?,?,?)",
                        (file_name, source, workspace))

    def _drain_deletions(self, source, workspace, limit):
        # Tombstones are committed before touching bytes. Retry safely after a crash
        # or failed unlink/fsync, without ever resurrecting a live artifact.
        with self._transaction() as con:
            rows = con.execute("""SELECT file_name FROM artifact_deletions
                WHERE source=? AND workspace=? ORDER BY file_name LIMIT ?""",
                               (source, workspace, limit)).fetchall()
            for row in rows:
                (self.root / row["file_name"]).unlink(missing_ok=True)
            if rows:
                self._sync_artifact_directory()
            for row in rows:
                con.execute("DELETE FROM artifact_deletions WHERE file_name=?", (row["file_name"],))
            return len(rows)

    def cleanup_expired_inputs(self, scope):
        """Explicit bounded input maintenance; task pins are never released here."""
        source, workspace = self._scope(scope)
        with self._transaction() as con:
            count = self._expire_inputs(con, source, workspace, 100)
        self._drain_deletions(source, workspace, 100)
        return count

    def maintain_retention(self, scope, *, retention_seconds=86400, batch_size=100):
        """Reclaim acknowledged media only after a durable eligibility grace period.

        Unknown/unacknowledged executions are protected regardless of age. Metadata
        and idempotency identities are intentionally retained, not bounded by this pass.
        """
        source, workspace = self._scope(scope)
        _integer(retention_seconds, 1, 365 * 86400)
        _integer(batch_size, 1, 100)
        now = self.clock()
        with self._transaction() as con:
            # Only acknowledged receipts or tasks that provably never started qualify.
            # A manual timeout/cancel after start also does not establish physical stop.
            con.execute("""INSERT OR IGNORE INTO retention_tasks(task_id, eligible_since)
                SELECT t.id, ? FROM tasks t JOIN attempts a ON a.task_id=t.id
                WHERE t.source=? AND t.workspace=?
                AND NOT EXISTS(SELECT 1 FROM retention_tasks m WHERE m.task_id=t.id)
                AND (EXISTS(SELECT 1 FROM receipts r JOIN acknowledgements k ON k.receipt_id=r.id
                            WHERE r.task_id=t.id)
                     OR (a.start_grant_id IS NULL AND
                         (t.state IN ('canceled','failed','expired') OR t.deadline<=?)))
                ORDER BY t.created, t.id LIMIT ?""", (now, source, workspace, now, batch_size))
            rows = con.execute("""SELECT t.id FROM retention_tasks m JOIN tasks t ON t.id=m.task_id
                WHERE t.source=? AND t.workspace=? AND m.reclaimed=0 AND m.eligible_since<=?
                ORDER BY m.eligible_since, t.id LIMIT ?""",
                               (source, workspace, now - retention_seconds, batch_size)).fetchall()
            for row in rows:
                upload = con.execute("""SELECT u.* FROM uploads u JOIN receipts r ON r.upload_id=u.id
                    JOIN acknowledgements k ON k.receipt_id=r.id WHERE u.task_id=?""", (row["id"],)).fetchone()
                if upload:
                    self._queue_deletion(con, upload["file_name"], source, workspace)
                    con.execute("UPDATE uploads SET state='expired',file_name=NULL WHERE id=?", (upload["id"],))
                con.execute("DELETE FROM input_pins WHERE task_id=?", (row["id"],))
                con.execute("UPDATE retention_tasks SET reclaimed=1 WHERE task_id=?", (row["id"],))
            inputs = self._expire_inputs(con, source, workspace, batch_size)
        deleted = self._drain_deletions(source, workspace, batch_size)
        return {"tasks_reclaimed": len(rows), "inputs_expired": inputs, "files_deleted": deleted}

    def _validate_task(self, payload):
        _object(payload, ("schema_version", "origin_key", "binding_id", "operation",
                         "execution_profile_id", "execution_profile_revision", "prompt",
                         "parameters", "references", "output_slot", "deadline", "routing"))
        if (payload["schema_version"] != "1" or payload["operation"] != "image.generate"
                or payload["execution_profile_id"] not in self.enabled_profiles
                or payload["execution_profile_revision"] != PROFILE_REVISION
                or payload["routing"] != "bridge_only"):
            _fail("unsupported_capability", 422)
        profile = get_profile(payload["execution_profile_id"])
        _origin(payload["origin_key"])
        _text(payload["binding_id"], 256)
        _text(payload["prompt"], 16000, multiline=True)
        _object(payload["parameters"], (), ("width", "height", "seed", "image_options"))
        try:
            validate_image_request(payload["parameters"], payload["references"], profile.id)
        except ValueError:
            _fail("unsupported_image_options_or_references", 422)
        if profile.id != "fake-image" and not {"width", "height"} <= payload["parameters"].keys():
            _fail("explicit_dimensions_required", 422)
        width = _integer(payload["parameters"].get("width", 32), 1, profile.max_dimension)
        height = _integer(payload["parameters"].get("height", 32), 1, profile.max_dimension)
        if width * height > profile.max_pixels:
            _fail("image_too_large", 413)
        if "seed" in payload["parameters"]:
            _integer(payload["parameters"]["seed"], 0, 2**32 - 1)
        refs = payload["references"]
        if not isinstance(refs, list) or len(refs) > 8:
            _fail("invalid_references")
        for ref in refs:
            _object(ref, ("input_ref", "role", "hash"))
            _text(ref["input_ref"])
            _hash(ref["hash"])
            if ref["role"] != "reference":
                _fail("unsupported_reference_role", 422)
        _object(payload["output_slot"], ("slot_id", "count"))
        if payload["output_slot"]["slot_id"] != "primary":
            _fail("unsupported_slot", 422)
        _integer(payload["output_slot"]["count"], 1, 1)
        try:
            value = payload["deadline"]
            if (not isinstance(value, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value)):
                raise ValueError
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            deadline = parsed.timestamp()
            if not math.isfinite(deadline):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            _fail("invalid_deadline")
        return deadline

    def create_task(self, scope, payload):
        source, workspace = self._scope(scope)
        deadline = self._validate_task(payload)
        digest = _digest(payload)
        with self._transaction() as con:
            old = con.execute("SELECT * FROM tasks WHERE source=? AND workspace=? AND origin_key=?",
                              (source, workspace, payload["origin_key"])).fetchone()
            if old:
                if old["digest"] != digest:
                    _fail("idempotency_conflict", 409)
                return self._task_view(con, old)
            if not self.accepting_new_tasks:
                _fail("bridge_draining", 503)
            if deadline <= self.clock() or deadline > self.clock() + 7 * 86400:
                _fail("invalid_deadline", 422)
            active = con.execute("""SELECT t.* FROM tasks t JOIN attempts a ON a.task_id=t.id
                WHERE t.state NOT IN ('canceled','failed','expired','artifacts_ready')
                AND (t.deadline>? OR a.start_grant_id IS NOT NULL)""", (self.clock(),)).fetchall()
            active_count = sum(self._profile(row).execution_mode != "manual" or row["deadline"] > self.clock() for row in active)
            if active_count >= 10:
                _fail("queue_capacity", 429)
            for ref in payload["references"]:
                row = self._scoped(con, "inputs", ref["input_ref"], scope)
                if row["state"] != "ready":
                    _fail("input_not_ready", 409)
                if row["expires"] <= self.clock():
                    _fail("input_expired", 410)
                if json.loads(row["payload"])["original_sha256"] != ref["hash"]:
                    _fail("input_hash_mismatch", 422)
                self._read_bytes(row["file_name"], json.loads(row["payload"]))
            task_id, attempt_id = _id("task"), _id("attempt")
            con.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (task_id, source, workspace, payload["origin_key"], digest,
                         _canonical(payload), "queued", attempt_id, 0, self.clock(), deadline))
            con.execute("INSERT INTO attempts(id,task_id) VALUES(?,?)", (attempt_id, task_id))
            con.execute("INSERT INTO dispatch_outbox(dispatch_key,task_id) VALUES(?,?)",
                        (_id("dispatch"), task_id))
            for ref in payload["references"]:
                con.execute("INSERT OR IGNORE INTO input_pins VALUES(?,?)", (task_id, ref["input_ref"]))
            return self._task_view(con, self._scoped(con, "tasks", task_id, scope))

    @staticmethod
    def _profile(task):
        return get_profile(json.loads(task["payload"]).get("execution_profile_id", "fake-image"))

    def _task_view(self, con, row):
        state = row["state"]
        if state not in _TERMINAL and not row["cancel_requested"]:
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (row["id"],)).fetchone()
            claim = con.execute("SELECT * FROM claims WHERE task_id=?", (row["id"],)).fetchone()
            if (not attempt["start_grant_id"] or self._profile(row).execution_mode == "manual") and row["deadline"] <= self.clock():
                state = "expired"
            elif (self._profile(row).execution_mode == "automatic"
                  and attempt["start_grant_id"] and claim and claim["expires"] <= self.clock()):
                # Reading never turns expiry into a second start permit.
                state = "execution_unknown"
        receipt = con.execute("SELECT * FROM receipts WHERE task_id=?", (row["id"],)).fetchone()
        ack = None
        if receipt:
            ack = con.execute("SELECT payload FROM acknowledgements WHERE receipt_id=?",
                              (receipt["id"],)).fetchone()
        from .readiness import ATTENTION_MESSAGES
        attention = con.execute("SELECT code FROM task_attention WHERE task_id=?", (row["id"],)).fetchone()
        detail = None
        if state in {"awaiting_user", "execution_unknown"}:
            code = attention["code"] if attention else ("import_original" if state == "awaiting_user" else "execution_requires_reconciliation")
            detail = {"code": code, "message": ATTENTION_MESSAGES[code]}
        return {
            "remote_task_id": row["id"], "origin_key": row["origin_key"],
            "task_digest": row["digest"], "state": state,
            "action_required": ("import_original" if state == "awaiting_user" and self._profile(row).execution_mode == "manual" else None),
            "action_required_detail": detail,
            "poll_after_ms": 1000, "attempt_id": row["attempt_id"],
            "request": json.loads(row["payload"]),
            "receipt_id": receipt["id"] if receipt else None,
            "delivery_ack": json.loads(ack["payload"])["decision"] if ack else "pending",
        }

    def get_task(self, scope, task_id):
        with self._read_transaction() as con:
            return self._task_view(con, self._scoped(con, "tasks", task_id, scope))

    def get_task_by_origin(self, scope, origin_key):
        source, workspace = self._scope(scope)
        _origin(origin_key)
        with self._read_transaction() as con:
            row = con.execute("SELECT * FROM tasks WHERE source=? AND workspace=? AND origin_key=?",
                              (source, workspace, origin_key)).fetchone()
            if not row:
                _fail("not_found", 404)
            return self._task_view(con, row)

    def cancel_task(self, scope, task_id):
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            if task["state"] not in _TERMINAL:
                started = con.execute("SELECT start_grant_id FROM attempts WHERE task_id=?",
                                      (task_id,)).fetchone()[0]
                if not started and task["deadline"] <= self.clock():
                    con.execute("UPDATE tasks SET state='expired' WHERE id=?", (task_id,))
                else:
                    state = "cancel_requested" if started and self._profile(task).execution_mode == "automatic" else "canceled"
                    con.execute("UPDATE tasks SET state=?,cancel_requested=1 WHERE id=?", (state, task_id))
            return self._task_view(con, self._scoped(con, "tasks", task_id, scope))

    def claim_task(self, scope, task_id, *, node_id, executor_instance, mode="execute", lease_seconds=60):
        """Acquire a ledger claim; the configured runtime separately owns Node HTTP."""
        _text(node_id)
        _text(executor_instance)
        if not isinstance(mode, str) or mode not in {"execute", "upload_only"}:
            _fail("invalid_claim_mode")
        _integer(lease_seconds, 1, 3600)
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if task["state"] == "expired":
                _fail("task_expired", 409)
            if task["state"] in _TERMINAL or task["cancel_requested"]:
                _fail("task_not_executable", 409)
            if (not attempt["start_grant_id"] or self._profile(task).execution_mode == "manual") and task["deadline"] <= self.clock():
                _fail("task_expired", 409)
            if attempt["start_grant_id"] and mode == "execute":
                _fail("execution_already_started", 409)
            if not attempt["start_grant_id"] and mode == "upload_only":
                _fail("execution_not_started", 409)
            old = con.execute("SELECT * FROM claims WHERE task_id=?", (task_id,)).fetchone()
            if old and old["expires"] > self.clock():
                if old["node"] != node_id or old["executor"] != executor_instance or old["mode"] != mode:
                    _fail("claim_busy", 409)
                return self._claim_view(old, task["attempt_id"])
            if attempt["start_grant_id"] and self._profile(task).execution_mode == "automatic":
                con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=?", (task_id,))
            epoch = old["epoch"] + 1 if old else 1
            claim_id = _id("claim")
            con.execute("INSERT OR REPLACE INTO claims VALUES(?,?,?,?,?,?,?)",
                        (task_id, claim_id, epoch, node_id, executor_instance, mode,
                         self.clock() + lease_seconds))
            return self._claim_view(con.execute("SELECT * FROM claims WHERE task_id=?", (task_id,)).fetchone(),
                                    task["attempt_id"])

    @staticmethod
    def _claim_view(row, attempt_id):
        return {"claim_id": row["id"], "epoch": row["epoch"], "mode": row["mode"],
                "expires_at": _utc(row["expires"]), "attempt_id": attempt_id}

    def start_execution(self, scope, task_id, claim_id):
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            if self._profile(task).execution_mode != "automatic":
                _fail("manual_profile_requires_manual_ready", 409)
            claim = self._check_claim(con, task, claim_id)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if attempt["start_grant_id"]:
                return {"start_grant_id": attempt["start_grant_id"],
                        "attempt_id": attempt["id"], "may_execute": False}
            if task["cancel_requested"] or task["state"] in _TERMINAL:
                _fail("task_not_executable", 409)
            if claim["mode"] != "execute":
                _fail("upload_only_claim", 409)
            if task["deadline"] <= self.clock():
                _fail("task_expired", 409)
            occupied = con.execute("""SELECT t.* FROM attempts a JOIN tasks t ON t.id=a.task_id
                WHERE a.start_grant_id IS NOT NULL AND t.id!=?
                AND t.state NOT IN ('artifacts_ready','canceled','failed','expired')""",
                                   (task_id,)).fetchall()
            if any(self._profile(row).execution_mode == "automatic" for row in occupied):
                _fail("physical_slot_busy", 409)
            grant = _id("grant")
            con.execute("""UPDATE attempts SET start_grant_id=?,original_node=?,original_executor=?,
                started=? WHERE id=?""",
                        (grant, claim["node"], claim["executor"], self.clock(), attempt["id"]))
            con.execute("DELETE FROM task_attention WHERE task_id=?", (task_id,))
            con.execute("UPDATE tasks SET state='running' WHERE id=?", (task_id,))
            return {"start_grant_id": grant, "attempt_id": attempt["id"], "may_execute": True}

    def begin_manual(self, scope, task_id, claim_id):
        """Grant same-attempt upload eligibility, never an automatic execution permit."""
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            if self._profile(task).execution_mode != "manual":
                _fail("automatic_profile_requires_start", 409)
            claim = self._check_claim(con, task, claim_id)
            if task["cancel_requested"] or task["state"] in _TERMINAL:
                _fail("task_not_executable", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if not attempt["start_grant_id"]:
                if claim["mode"] != "execute":
                    _fail("upload_only_claim", 409)
                if task["deadline"] <= self.clock():
                    _fail("task_expired", 409)
                pending = con.execute("""SELECT t.* FROM tasks t JOIN attempts a ON a.task_id=t.id
                    WHERE a.start_grant_id IS NOT NULL
                    AND t.state NOT IN ('artifacts_ready','canceled','failed','expired')""").fetchall()
                if sum(self._profile(row).execution_mode == "manual" and row["deadline"] > self.clock() for row in pending) >= 3:
                    _fail("manual_capacity", 429)
                con.execute("""UPDATE attempts SET start_grant_id=?,original_node=?,original_executor=?,
                    started=? WHERE id=?""", (_id("manual"), claim["node"], claim["executor"],
                                               self.clock(), attempt["id"]))
                con.execute("UPDATE tasks SET state='awaiting_user' WHERE id=?", (task_id,))
            return {"attempt_id": attempt["id"], "state": "awaiting_user", "may_execute": False}

    def mark_execution_unknown(self, scope, task_id, claim_id):
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            if self._profile(task).execution_mode != "automatic":
                _fail("manual_profile_requires_manual_ready", 409)
            self._check_claim(con, task, claim_id)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if not attempt["start_grant_id"]:
                _fail("execution_not_started", 409)
            if task["state"] not in _TERMINAL and not task["cancel_requested"]:
                con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=?", (task_id,))
            return self._task_view(con, self._scoped(con, "tasks", task_id, scope))

    def create_upload(self, scope, task_id, claim_id, payload):
        _object(payload, ("attempt_id", "slot_id", "original_sha256", "size", "mime_type"))
        with self._transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            self._media_metadata(payload, output=True, profile=self._profile(task))
            self._check_claim(con, task, claim_id)
            if payload["attempt_id"] != task["attempt_id"] or payload["slot_id"] != "primary":
                _fail("upload_binding_mismatch", 409)
            attempt = con.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
            if not attempt["start_grant_id"]:
                _fail("execution_not_started", 409)
            old = con.execute("SELECT * FROM uploads WHERE task_id=?", (task_id,)).fetchone()
            digest = _digest(payload)
            if old:
                if old["digest"] != digest:
                    _fail("idempotency_conflict", 409)
                return self._upload_view(old)
            if task["cancel_requested"] or task["state"] in _TERMINAL:
                _fail("task_not_accepting", 409)
            upload_id = _id("up")
            con.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (upload_id, task_id, task["attempt_id"], "primary", digest,
                         _canonical(payload), "pending", payload["size"], None, None))
            con.execute("UPDATE tasks SET state='uploading' WHERE id=?", (task_id,))
            return self._upload_view(con.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone())

    @staticmethod
    def _upload_view(row):
        return {"upload_id": row["id"], "state": row["state"], **json.loads(row["payload"])}

    def put_upload_content(self, scope, upload_id, claim_id, content):
        with self._transaction() as con:
            row, task = self._upload_row(con, upload_id, scope)
            self._check_claim(con, task, claim_id)
            payload = json.loads(row["payload"])
            dimensions = self._verify_media(content, payload, profile=self._profile(task))
            request = json.loads(task["payload"])
            parameters = request["parameters"]
            expected_mime = requested_output_mime(parameters)
            # _verify_media has already matched the MIME against the decoded format.
            if expected_mime and payload["mime_type"] != expected_mime:
                _fail("output_format_mismatch", 422)
            if dimensions != {"width": parameters.get("width", 32),
                              "height": parameters.get("height", 32)}:
                _fail("output_dimensions_mismatch", 422)
            if row["state"] in {"ready", "committed"}:
                self._read_bytes(row["file_name"], payload)
                return self._upload_view(row)
            if task["cancel_requested"] or task["state"] in _TERMINAL:
                _fail("task_not_accepting", 409)
            file_name = self._store_bytes(con, upload_id, content)
            con.execute("UPDATE uploads SET state='ready',file_name=?,dimensions=? WHERE id=?",
                        (file_name, _canonical(dimensions), upload_id))
            return self._upload_view(con.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone())

    def commit_upload(self, scope, upload_id, claim_id):
        with self._transaction() as con:
            upload, task = self._upload_row(con, upload_id, scope)
            self._check_claim(con, task, claim_id)
            old = con.execute("SELECT * FROM receipts WHERE upload_id=?", (upload_id,)).fetchone()
            if old:
                return self._receipt_view(con, old)
            if task["cancel_requested"] or task["state"] in _TERMINAL:
                _fail("task_not_accepting", 409)
            if upload["state"] != "ready":
                _fail("upload_not_ready", 409)
            payload = json.loads(upload["payload"])
            self._read_bytes(upload["file_name"], payload)
            receipt_id, artifact_id = _id("receipt"), _id("artifact")
            manifest = {
                "receipt_id": receipt_id, "task_id": task["id"],
                "attempt_id": task["attempt_id"], "slot_id": upload["slot_id"],
                "execution_profile_id": self._profile(task).id, "execution_profile_revision": self._profile(task).revision,
                "artifact_id": artifact_id, "original_sha256": payload["original_sha256"],
                "size": payload["size"], "mime_type": payload["mime_type"],
                "dimensions": json.loads(upload["dimensions"]), "accepted_at": _utc(self.clock()),
            }
            con.execute("INSERT INTO receipts VALUES(?,?,?,?,?)",
                        (receipt_id, task["id"], upload_id, artifact_id, _canonical(manifest)))
            con.execute("UPDATE uploads SET state='committed' WHERE id=?", (upload_id,))
            con.execute("UPDATE tasks SET state='artifacts_ready' WHERE id=?", (task["id"],))
            return self._receipt_view(con, con.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone())

    @staticmethod
    def _receipt_view(con, row):
        ack = con.execute("SELECT payload FROM acknowledgements WHERE receipt_id=?", (row["id"],)).fetchone()
        return {**json.loads(row["manifest"]),
                "delivery_ack": json.loads(ack["payload"])["decision"] if ack else "pending",
                "acknowledgement": json.loads(ack["payload"]) if ack else None}

    def get_receipt(self, scope, receipt_id):
        with self._read_transaction() as con:
            return self._receipt_view(con, self._receipt_row(con, receipt_id, scope))

    def get_artifact_content(self, scope, artifact_id):
        with self._read_transaction() as con:
            receipt = con.execute("SELECT * FROM receipts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if not receipt:
                _fail("not_found", 404)
            self._scoped(con, "tasks", receipt["task_id"], scope)
            upload = con.execute("SELECT * FROM uploads WHERE id=?", (receipt["upload_id"],)).fetchone()
            if upload["state"] == "expired":
                _fail("artifact_expired", 410)
            payload = json.loads(upload["payload"])
            return self._read_bytes(upload["file_name"], payload), payload["mime_type"]

    def ack_receipt(self, scope, receipt_id, payload):
        source, workspace = self._scope(scope)
        _object(payload, ("client_receipt_id", "decision", "reason_code", "original_hash"),
                ("normalized_hash", "native_registration_ref"))
        _text(payload["client_receipt_id"])
        _text(payload["reason_code"])
        _hash(payload["original_hash"])
        if not isinstance(payload["decision"], str) or payload["decision"] not in {"accepted", "rejected"}:
            _fail("invalid_decision")
        if "normalized_hash" in payload:
            _hash(payload["normalized_hash"])
        if "native_registration_ref" in payload:
            _text(payload["native_registration_ref"], 256)
        digest = _digest(payload)
        with self._transaction() as con:
            row = self._receipt_row(con, receipt_id, scope)
            if json.loads(row["manifest"])["original_sha256"] != payload["original_hash"]:
                _fail("ack_hash_mismatch", 422)
            old = con.execute("""SELECT * FROM acknowledgements
                WHERE receipt_id=? OR (source=? AND workspace=? AND client_receipt_id=?)""",
                              (receipt_id, source, workspace, payload["client_receipt_id"])).fetchone()
            if old:
                if old["receipt_id"] != receipt_id or old["digest"] != digest:
                    _fail("idempotency_conflict", 409)
            else:
                con.execute("INSERT INTO acknowledgements VALUES(?,?,?,?,?,?)",
                            (receipt_id, source, workspace, payload["client_receipt_id"],
                             digest, _canonical(payload)))
            return self._receipt_view(con, row)


    def pending_dispatches(self, scope, limit=10):
        """Read handoff records, not an execution/claim queue."""
        source, workspace = self._scope(scope)
        _integer(limit, 1, 10)
        with self._read_transaction() as con:
            rows = con.execute("""SELECT d.dispatch_key, d.task_id FROM dispatch_outbox d
                JOIN tasks t ON t.id=d.task_id JOIN attempts a ON a.task_id=t.id
                WHERE t.source=? AND t.workspace=? AND d.engine_run_id IS NULL
                AND t.state='queued' AND t.cancel_requested=0 AND t.deadline>?
                AND a.start_grant_id IS NULL ORDER BY t.created LIMIT ?""",
                (source, workspace, self.clock(), limit)).fetchall()
            return {"items": [dict(row) for row in rows]}

    def ack_dispatch(self, scope, dispatch_key, engine_run_id):
        _text(dispatch_key)
        _text(engine_run_id, 256)
        with self._transaction() as con:
            row = con.execute("SELECT * FROM dispatch_outbox WHERE dispatch_key=?", (dispatch_key,)).fetchone()
            if not row:
                _fail("not_found", 404)
            self._scoped(con, "tasks", row["task_id"], scope)
            # A lost submit response may yield more than one engine run. The first
            # acknowledged handoff is canonical; execution remains start-latched.
            if row["engine_run_id"] is None:
                con.execute("UPDATE dispatch_outbox SET engine_run_id=?,acknowledged=? WHERE dispatch_key=?",
                            (engine_run_id, self.clock(), dispatch_key))
            return {"dispatch_key": dispatch_key, "task_id": row["task_id"],
                    "engine_run_id": row["engine_run_id"] or engine_run_id, "acknowledged": True}

    def authorize_node_claim(self, scope, task_id, claim_id, node_id, executor_instance):
        with self._read_transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            claim = self._check_claim(con, task, claim_id)
            if claim["node"] != node_id or claim["executor"] != executor_instance:
                _fail("claim_identity_mismatch", 403)
            return self._claim_view(claim, task["attempt_id"])

    def authorize_node_upload(self, scope, upload_id, claim_id, node_id, executor_instance):
        with self._read_transaction() as con:
            row, task = self._upload_row(con, upload_id, scope)
            claim = self._check_claim(con, task, claim_id)
            if claim["node"] != node_id or claim["executor"] != executor_instance:
                _fail("claim_identity_mismatch", 403)
            return self._upload_view(row)

    def get_node_input_content(self, scope, task_id, input_id, claim_id, node_id, executor_instance):
        with self._read_transaction() as con:
            task = self._scoped(con, "tasks", task_id, scope)
            claim = self._check_claim(con, task, claim_id)
            if claim["node"] != node_id or claim["executor"] != executor_instance:
                _fail("claim_identity_mismatch", 403)
            pin = con.execute("SELECT 1 FROM input_pins WHERE task_id=? AND input_id=?", (task_id, input_id)).fetchone()
            if not pin:
                _fail("not_found", 404)
            row = self._scoped(con, "inputs", input_id, scope)
            payload = json.loads(row["payload"])
            return self._read_bytes(row["file_name"], payload), payload["mime_type"]
