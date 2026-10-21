"""Single configured-node authorization and bounded metadata audit storage."""
EVENTS = frozenset({
    'node_configured', 'node_revoked', 'node_reactivated', 'task_created',
    'start_granted', 'manual_ready', 'execution_unknown', 'task_attention',
    'cancel_requested', 'canceled', 'receipt_committed', 'delivery_accepted', 'delivery_rejected',
})
AUDIT_CAPACITY = 1000


class OperationsMixin:
    def _audit(self, con, scope, kind, *, task_id=None, attempt_id=None, receipt_id=None, code=None):
        from .readiness import ATTENTION_MESSAGES
        if kind not in EVENTS or (code is not None and code not in ATTENTION_MESSAGES):
            raise ValueError('Invalid internal audit event')
        source, workspace = self._scope(scope)
        con.execute('INSERT INTO audit_events(source,workspace,occurred,kind,task_id,attempt_id,receipt_id,code) VALUES(?,?,?,?,?,?,?,?)',
                    (source, workspace, self.clock(), kind, task_id, attempt_id, receipt_id, code))
        con.execute('DELETE FROM audit_events WHERE id NOT IN (SELECT id FROM audit_events ORDER BY id DESC LIMIT ?)',
                    (AUDIT_CAPACITY,))

    def audit_events(self, scope, limit=50):
        from .domain import _integer, _utc
        _integer(limit, 1, 100)
        source, workspace = self._scope(scope)
        with self._connection() as con:
            rows = con.execute('SELECT id,occurred,kind,task_id,attempt_id,receipt_id,code FROM audit_events WHERE source=? AND workspace=? ORDER BY id DESC LIMIT ?',
                               (source, workspace, limit)).fetchall()
        return {'schema_version': '1', 'capacity': AUDIT_CAPACITY,
                'events': [{'event_id': row['id'], 'occurred_at': _utc(row['occurred']),
                            **{key: row[key] for key in ('kind', 'task_id', 'attempt_id', 'receipt_id', 'code')}} for row in rows]}

    def provision_node(self, scope, node_id, executor_instance):
        source, workspace = self._scope(scope)
        with self._transaction() as con:
            changed = con.execute('INSERT OR IGNORE INTO node_registry VALUES(?,?,?,?,1)',
                                 (source, workspace, node_id, executor_instance)).rowcount
            if changed:
                self._audit(con, scope, 'node_configured')

    def _check_node_active(self, con, scope, node_id, executor_instance, *, required=False):
        from .domain import _fail
        source, workspace = self._scope(scope)
        row = con.execute('SELECT active FROM node_registry WHERE source=? AND workspace=? AND node=? AND executor=?',
                          (source, workspace, node_id, executor_instance)).fetchone()
        # Bare domain fixtures need no configured Node API. Installed Node routes
        # always provision and require their exact configured registry binding.
        if row is None and required:
            _fail('node_not_registered', 403)
        if row is not None and not row['active']:
            _fail('node_revoked', 403)

    def authorize_active_node(self, scope, node_id, executor_instance):
        with self._connection() as con:
            self._check_node_active(con, scope, node_id, executor_instance, required=True)

    def configured_node_status(self, scope, node_id, executor_instance):
        source, workspace = self._scope(scope)
        with self._connection() as con:
            row = con.execute('SELECT active FROM node_registry WHERE source=? AND workspace=? AND node=? AND executor=?',
                              (source, workspace, node_id, executor_instance)).fetchone()
        return {'authorization': 'unknown' if row is None else 'active' if row['active'] else 'revoked'}

    def set_node_active(self, scope, node_id, executor_instance, active):
        from .domain import _fail
        if type(active) is not bool:
            raise ValueError('Node activity must be boolean')
        source, workspace = self._scope(scope)
        with self._transaction() as con:
            row = con.execute('SELECT active FROM node_registry WHERE source=? AND workspace=? AND node=? AND executor=?',
                              (source, workspace, node_id, executor_instance)).fetchone()
            if row is None:
                _fail('node_not_registered', 404)
            if bool(row['active']) != active:
                con.execute('UPDATE node_registry SET active=? WHERE source=? AND workspace=? AND node=? AND executor=?',
                            (int(active), source, workspace, node_id, executor_instance))
                self._audit(con, scope, 'node_reactivated' if active else 'node_revoked')
        return {'authorization': 'active' if active else 'revoked'}


