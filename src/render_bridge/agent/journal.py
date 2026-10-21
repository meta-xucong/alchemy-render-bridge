"""Agent-owned local evidence, not a dispatch queue."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3


class AgentBusy(RuntimeError):
    pass


class InstanceLock:
    """OS-owned lock: released on process exit; never remove the lock file."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        if self.handle.seek(0, 2) == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.handle.close()
            self.handle = None
            raise AgentBusy('Another Agent instance owns this state directory') from None
        return self

    def __exit__(self, *_exc):
        if self.handle:
            try:
                self.handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


class AgentJournal:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'agent.sqlite'
        with self.connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS health_faults (profile TEXT PRIMARY KEY, reason TEXT NOT NULL, evidence_id TEXT NOT NULL, generation INTEGER NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS health_revalidation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL, began REAL NOT NULL, expires REAL NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS health_revisions (profile TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            con.execute('''CREATE TABLE IF NOT EXISTS attempts (
                task_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL,
                state TEXT NOT NULL, record TEXT NOT NULL)''')

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con:
                yield con
        finally:
            con.close()

    def get(self, task_id):
        with self.connection() as con:
            row = con.execute('SELECT record FROM attempts WHERE task_id=?', (task_id,)).fetchone()
            return json.loads(row['record']) if row else None

    def save(self, record):
        with self.connection() as con:
            old = con.execute('SELECT attempt_id FROM attempts WHERE task_id=?', (record['task_id'],)).fetchone()
            if old and old['attempt_id'] != record['attempt_id']:
                raise ValueError('Task attempt identity changed')
            con.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)',
                        (record['task_id'], record['attempt_id'], record['state'],
                         json.dumps(record, ensure_ascii=False, allow_nan=False)))

    def occupied_by_other(self, task_id):
        with self.connection() as con:
            return con.execute("SELECT 1 FROM attempts WHERE task_id!=? AND state IN ('start_requested','execution_unknown') LIMIT 1",
                               (task_id,)).fetchone() is not None

    def next_health_revision(self, profile):
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO health_revisions VALUES(?,1) ON CONFLICT(profile) DO UPDATE SET revision=revision+1', (profile,))
            return con.execute('SELECT revision FROM health_revisions WHERE profile=?', (profile,)).fetchone()[0]

    def revalidation_active(self, generation, now):
        """A manual config increment is consumed once, never extended by restart."""
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT * FROM health_revalidation WHERE singleton=1').fetchone()
            if generation > (old['generation'] if old else 0):
                con.execute('INSERT OR REPLACE INTO health_revalidation VALUES(1,?,?,?)',
                            (generation, now, now+120))
                return True
            return bool(old and generation == old['generation'] and old['began'] <= now < old['expires'])

    def record_health_fault(self, profile, reason, evidence_id, generation):
        with self.connection() as con:
            con.execute('INSERT INTO health_faults VALUES(?,?,?,?) ON CONFLICT(profile) DO UPDATE SET reason=excluded.reason,evidence_id=excluded.evidence_id,generation=excluded.generation WHERE health_faults.evidence_id!=excluded.evidence_id',
                        (profile, reason, evidence_id, generation))

    def health_fault(self, profile):
        with self.connection() as con:
            row = con.execute('SELECT * FROM health_faults WHERE profile=?', (profile,)).fetchone()
            return dict(row) if row else None

    def clear_health_fault(self, profile):
        with self.connection() as con:
            con.execute('DELETE FROM health_faults WHERE profile=?', (profile,))
