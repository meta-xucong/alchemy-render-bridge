"""Explicit acknowledgement-aware cleanup of registered Agent-owned media copies."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import time
from pathlib import Path

from .journal import InstanceLock

_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


def _owned_file(root, path):
    """Reject traversal and symlink ancestors; never follow a path out of state."""
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    relative = path.relative_to(root)
    if '..' in relative.parts:
        raise ValueError('Invalid managed path')
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Symlink in managed path')
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Managed media must be a regular file')
    return path


def _registered_files(journal, record):
    root = journal.root
    tid, aid = record['task_id'], record['attempt_id']
    if not _KEY.fullmatch(tid) or not _KEY.fullmatch(aid):
        raise ValueError('Invalid attempt identity')
    attempt = root / 'attempts' / tid / aid
    executor = root / 'executors' / tid / aid
    files = []
    reference_hashes = {}
    if record.get('artifact'):
        artifact = _owned_file(root, record['artifact'])
        if artifact != attempt / 'result.original':
            raise ValueError('Unexpected registered artifact path')
        files.append(artifact)
    for index, reference in enumerate(record.get('request', {}).get('references', [])):
        path = _owned_file(root, reference['path'])
        if path != attempt / f'input-{index:02d}.original':
            raise ValueError('Unexpected registered reference path')
        files.append(path)
        reference_hashes[path] = reference['hash']
    original = _owned_file(root, executor / 'artifact.original')
    manifest = _owned_file(root, executor / 'artifact.json')
    if manifest.exists():
        data = json.loads(manifest.read_text())
        if data['sha256'] != record.get('sha256') or data['request_sha256'] != record.get('request_sha256'):
            raise ValueError('Executor manifest does not match completed attempt')
        files.append(original)
        registry = _owned_file(root, executor / 'retention-files.json')
        if registry.exists():
            value = json.loads(registry.read_text())
            path = _owned_file(root, executor / value['original'])
            if not path.is_relative_to(executor) or value['sha256'] != record.get('sha256'):
                raise ValueError('Registered generated original does not match attempt')
            files.append(path)
    # Verify original bytes before scheduling deletion. Never discard changed files.
    for path in files:
        if path.exists():
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Managed media exceeds bounds')
            expected = reference_hashes.get(path, record.get('sha256'))
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Managed media changed')
    return sorted({str(path.relative_to(root)) for path in files}), executor, manifest.exists()


def maintain_agent(agent, *, retention_seconds=86400, batch_size=20, clock=time.time):
    for value, high in ((retention_seconds, 365 * 86400), (batch_size, 100)):
        if type(value) is not int or not 1 <= value <= high:
            raise ValueError('Invalid maintenance bound')
    journal = agent.journal
    now = clock()
    result = {'outcome': 'maintenance_complete', 'checked': 0, 'reclaimed': 0, 'skipped': 0}
    with InstanceLock(journal.root / 'agent.lock'):
        with journal.connection() as con:
            rows = con.execute("""SELECT record FROM attempts WHERE state='complete'
                AND COALESCE(json_extract(record, '$.media_reclaimed'), 0)=0
                ORDER BY COALESCE(json_extract(record, '$.retention_checked'), 0), task_id LIMIT ?""",
                               (batch_size,)).fetchall()
        for row in rows:
            record = json.loads(row['record'])
            result['checked'] += 1
            record['retention_checked'] = now
            try:
                task = agent.client.task(record['task_id'])
                if (task.get('remote_task_id') != record['task_id'] or task.get('attempt_id') != record['attempt_id']
                        or task.get('receipt_id') != record.get('receipt_id') or not record.get('receipt_id')
                        or task.get('delivery_ack') not in ('accepted', 'rejected')):
                    raise ValueError('Matching source acknowledgement required')
                record.setdefault('retention_eligible_since', now)
                if now - record['retention_eligible_since'] < retention_seconds:
                    journal.save(record)
                    continue
                files, executor, has_manifest = _registered_files(journal, record)
                # Intent and completed tombstone precede every unlink. Interrupted
                # passes retry the same registered files, never regenerate.
                record['cleanup_files'] = files
                journal.save(record)
                if has_manifest:
                    marker = _owned_file(journal.root, executor / 'retired.json')
                    if not marker.exists():
                        agent._save_bytes(marker, b'{"reason":"source_acknowledged"}')
                for relative in files:
                    _owned_file(journal.root, relative).unlink(missing_ok=True)
                record['media_reclaimed'] = True
                journal.save(record)
                result['reclaimed'] += 1
            except Exception:
                # No exception text/paths/credentials in the maintenance summary.
                journal.save(record)
                result['skipped'] += 1
    return result
