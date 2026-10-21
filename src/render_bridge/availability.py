"""Persisted profile facts, separate from liveness, capacity and acceptance gates."""
import json

REASONS = frozenset({'ready', 'tool_missing', 'version_unsupported', 'auth_unavailable',
                     'quota_exhausted', 'resource_unavailable', 'probe_failed', 'unverified', 'stale'})
EVIDENCE = frozenset({'local_probe', 'provider_error', 'operator_validation', 'operator_attested_preflight', 'account_status', 'none'})
HEALTH_DDL = '''
CREATE TABLE IF NOT EXISTS health_revision (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO health_revision VALUES(1,0);
CREATE TABLE IF NOT EXISTS profile_health (
 source TEXT NOT NULL, workspace TEXT NOT NULL, node TEXT NOT NULL, executor TEXT NOT NULL,
 profile TEXT NOT NULL, report_revision INTEGER NOT NULL, seen REAL NOT NULL,
 health TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL,
 failures INTEGER NOT NULL, blocker TEXT, revalidation_generation INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source,workspace,node,executor,profile));
'''


class AvailabilityMixin:
    def _record_health(self, con, scope, node, executor, profiles, reports):
        from .domain import _fail, _integer
        from .profiles import get_profile
        source, workspace = self._scope(scope)
        if reports is not None and (not isinstance(reports, list) or len(reports) > 3):
            _fail('invalid_profile_health')
        supplied = set()
        for item in reports or []:
            fields = {'profile_id', 'profile_revision', 'report_revision', 'health', 'reason_code', 'evidence_kind'}
            if not isinstance(item, dict) or set(item) - fields - {'revalidation_generation'} or fields - set(item):
                _fail('invalid_profile_health')
            profile = item['profile_id']
            if not isinstance(profile, str) or profile not in profiles or profile in supplied:
                _fail('invalid_profile_health')
            supplied.add(profile)
            if item['profile_revision'] != get_profile(profile).revision:
                _fail('invalid_profile_health')
            _integer(item['report_revision'], 1, 2**63-1)
            health, reason, evidence = (item[k] for k in ('health', 'reason_code', 'evidence_kind'))
            if (health not in {'available', 'unavailable', 'unknown'} or reason not in REASONS or evidence not in EVIDENCE
                    or (health == 'available' and (reason != 'ready' or evidence == 'none'))
                    or (health == 'unavailable' and reason in {'ready', 'unverified', 'stale'})
                    or (health == 'unknown' and reason not in {'unverified', 'stale', 'probe_failed'})):
                _fail('invalid_profile_health')
            key = (source, workspace, node, executor, profile)
            old = con.execute('SELECT * FROM profile_health WHERE source=? AND workspace=? AND node=? AND executor=? AND profile=?', key).fetchone()
            if old and item['report_revision'] <= old['report_revision']:
                continue  # Crucially does not refresh freshness or recovery stability.
            generation = item.get('revalidation_generation', 0)
            _integer(generation, 0, 2**63-1)
            consumed_generation = old['revalidation_generation'] if old else 0
            now = self.clock(); failures = 0
            blocker = old['blocker'] if old else None
            if health == 'unavailable' and reason in {'auth_unavailable', 'quota_exhausted'}:
                blocker = reason
            if health == 'unavailable' and reason == 'probe_failed':
                failures = (old['failures'] if old else 0) + 1
                if failures < 2:
                    health = 'unknown'
            if health == 'available' and blocker:
                # Baseline operator acceptance is NOT a fresh confirmation that an
                # auth/quota failure resolved. Only explicit revalidation clears it.
                if evidence == 'account_status' or (evidence == 'operator_validation' and generation > consumed_generation):
                    blocker = None
                else:
                    health, reason, evidence = 'unavailable', blocker, 'provider_error'
            con.execute('INSERT OR REPLACE INTO profile_health VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (*key, item['report_revision'], now, health, reason, evidence, failures, blocker, max(generation, consumed_generation)))
            con.execute('UPDATE health_revision SET revision=revision+1 WHERE singleton=1')
        # Legacy heartbeat is not a positive health observation. Preserve the last
        # revision fence while invalidating an omitted profile's prior health.
        rows = con.execute('SELECT * FROM profile_health WHERE source=? AND workspace=? AND node=? AND executor=?',
                           (source, workspace, node, executor)).fetchall()
        for old in rows:
            if old['profile'] not in supplied and old['health'] != 'unknown':
                con.execute("UPDATE profile_health SET health='unknown',reason='unverified',evidence='none' WHERE source=? AND workspace=? AND node=? AND executor=? AND profile=?",
                            (source, workspace, node, executor, old['profile']))
                con.execute('UPDATE health_revision SET revision=revision+1 WHERE singleton=1')

    def routing_availability(self, scope):
        from .domain import _utc
        from .profiles import get_profile
        source, workspace = self._scope(scope)
        with self._read_transaction() as con:
            revision = con.execute('SELECT revision FROM health_revision WHERE singleton=1').fetchone()[0]
            occupied = con.execute("""SELECT t.payload FROM tasks t JOIN attempts a ON a.task_id=t.id
                WHERE t.source=? AND t.workspace=? AND a.start_grant_id IS NOT NULL
                AND t.state NOT IN ('artifacts_ready','canceled','failed','expired')""", (source, workspace)).fetchall()
            busy = {json.loads(row['payload'])['execution_profile_id'] for row in occupied}
            items = []
            for profile in map(get_profile, self.enabled_profiles):
                rows = con.execute('''SELECT h.* FROM profile_health h JOIN node_registry n
                    ON n.source=h.source AND n.workspace=h.workspace AND n.node=h.node AND n.executor=h.executor
                    WHERE h.source=? AND h.workspace=? AND h.profile=? AND n.active=1 ORDER BY h.seen DESC''',
                    (source, workspace, profile.id)).fetchall()
                # Single trusted-node design: multiple active reporters is ambiguous,
                # not an invitation to choose a different tool or executor.
                row = rows[0] if len(rows) == 1 else None
                fresh = row is not None and 0 <= self.clock()-row['seen'] < 90
                items.append({'profile_id': profile.id, 'profile_revision': profile.revision,
                    'health': row['health'] if fresh else 'unknown',
                    'capacity': 'busy' if profile.id in busy or (profile.execution_mode == 'automatic' and any(get_profile(p).execution_mode == 'automatic' for p in busy)) else 'idle',
                    'reason_code': row['reason'] if fresh else 'stale' if row else 'unverified',
                    'evidence_kind': row['evidence'] if row else 'none',
                    'observed_at': _utc(row['seen']) if row else None,
                    'valid_until': _utc(row['seen']+90) if row else None})
            return {'schema_version': '1', 'revision': revision, 'profiles': items}
