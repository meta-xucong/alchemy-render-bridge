"""Opt-in synthetic Harness HTTP contract on the existing Bridge task ledger.

No scheduler or model executor lives here. Harness rows are typed extension data
for tasks/attempts/claims, all mutated in the same Bridge SQLite transaction.
Real Harness profiles, billing and native supervisor stop proofs are not enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator

CONTRACT = 'harness.invoke.v1'
PROFILE = 'harness-invoke'
MAX_REQUEST_BYTES = 65536
MAX_RESULT_BYTES = 262144  # Aggregate decoded UTF-8 output text, not JSON framing.
MAX_RESULT_WIRE_BYTES = 6 * MAX_RESULT_BYTES + 4096
ID = Annotated[StrictStr, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')]
DIGEST = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]


def canonical(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    data.encode('utf-8')
    return data


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class HarnessBinding(Strict):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    binding_id: ID
    revision: Annotated[StrictInt, Field(ge=1)]
    model_alias: ID
    native_model: ID

    def wire(self):
        return self.model_dump()


class Identity(Strict):
    source: ID
    tenant: ID
    principal: ID
    operation: ID


class Message(Strict):
    role: Literal['user', 'assistant']
    content: Annotated[StrictStr, Field(min_length=1, max_length=MAX_REQUEST_BYTES)]

    @model_validator(mode='after')
    def nonblank(self):
        if not self.content.strip():
            raise ValueError('Blank message')
        return self


class ClientRequest(Strict):
    model: ID
    messages: Annotated[list[Message], Field(min_length=1, max_length=64)]
    stream: StrictBool
    n: Annotated[StrictInt, Field(ge=1, le=1)]

    @model_validator(mode='after')
    def supported(self):
        if self.stream or self.messages[-1].role != 'user':
            raise ValueError('Unsupported request')
        return self


class InvokeRequest(Strict):
    binding: HarnessBinding
    messages: Annotated[list[Message], Field(min_length=1, max_length=64)]
    client_request: ClientRequest


class HarnessCreate(Strict):
    schema_version: Literal['harness.invoke.v1']
    origin_key: Annotated[StrictStr, Field(pattern=r'^h_[0-9a-f]{64}$')]
    identity: Identity
    request_digest: DIGEST
    ledger_instance: ID
    request: InvokeRequest

    @model_validator(mode='after')
    def frozen(self):
        if self.origin_key != 'h_' + digest(self.identity.model_dump()):
            raise ValueError('Identity digest mismatch')
        request = self.request
        if (self.request_digest != digest(request.client_request.model_dump())
                or request.messages != request.client_request.messages
                or request.binding.model_alias != request.client_request.model):
            raise ValueError('Frozen request mismatch')
        if len(canonical(self.model_dump()).encode()) > MAX_REQUEST_BYTES:
            raise ValueError('Request too large')
        return self


class HarnessHeartbeat(Strict):
    state: Literal['available', 'busy', 'attention']
    binding_digests: Annotated[list[DIGEST], Field(max_length=32)]

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.binding_digests)) != len(self.binding_digests):
            raise ValueError('Duplicate binding')
        return self


class Observation(Strict):
    claim_id: ID
    start_grant_id: ID
    outcome: Literal['pending']
    # A status report is NOT proof that an old launch consumer cannot spawn.
    liveness: Literal['may_be_running', 'running', 'waiting_approval', 'unknown']
    code: Literal['EXECUTION_UNCERTAIN', 'APPROVAL_PENDING', 'OBSERVATION_ERROR']


class Block(Strict):
    type: Literal['text', 'refusal']
    text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_RESULT_BYTES)]

    @model_validator(mode='after')
    def nonblank(self):
        if not self.text.strip():
            raise ValueError('Blank result')
        return self


class NativeCompletion(Strict):
    confirmed: StrictBool

    @model_validator(mode='after')
    def confirmed_native(self):
        if not self.confirmed:
            raise ValueError('Native completion is not confirmed')
        return self


class ModelEvidence(Strict):
    selected: ID
    reported: ID


class UnknownUsage(Strict):
    state: Literal['unknown']


class HarnessResult(Strict):
    outcome: Literal['succeeded', 'failed', 'cancelled']
    liveness: Literal['unknown', 'stopped_confirmed']
    output_blocks: Annotated[list[Block], Field(max_length=32)]
    native_completion: NativeCompletion
    model_evidence: ModelEvidence
    usage: UnknownUsage
    code: Literal['NATIVE_FAILED', 'MODEL_MISMATCH', 'NATIVE_EMPTY_RESULT'] | None = None

    @model_validator(mode='after')
    def valid_terminal(self):
        if self.outcome == 'succeeded' and (not self.output_blocks or self.code is not None):
            raise ValueError('Success requires genuine output')
        if self.outcome != 'failed' and self.code is not None:
            raise ValueError('Error code on nonfailure')
        if sum(len(block.text.encode('utf-8')) for block in self.output_blocks) > MAX_RESULT_BYTES:
            raise ValueError('Result text too large')
        if len(canonical(self.model_dump(exclude_none=True)).encode('utf-8')) > MAX_RESULT_WIRE_BYTES:
            raise ValueError('Result wire too large')
        return self


class ResultSubmission(Strict):
    claim_id: ID
    start_grant_id: ID
    result: HarnessResult


@dataclass(frozen=True)
class HarnessConfig:
    source_id: str
    workspace_id: str
    ledger_instance: str
    node_id: str
    executor_instance: str
    allowed_owners: tuple[tuple[str, str], ...]
    bindings: tuple[HarnessBinding, ...]
    accepting_new: bool = False
    start_timeout_seconds: int = 3600
    max_records: int = 10000
    max_result_storage_bytes: int = 16 * 1024 * 1024
    execution_mode: str = "synthetic"

    def __post_init__(self):
        from pydantic import TypeAdapter
        if self.execution_mode not in {"synthetic", "workbuddy_local_trial", "harness_local_trial"}:
            raise ValueError("Unknown harness execution mode")
        trial = self.execution_mode in {"workbuddy_local_trial", "harness_local_trial"}
        prefixes = ("workbuddy-", "codex-") if self.execution_mode == "harness_local_trial" else ("workbuddy-" if trial else "synthetic-")
        if trial and len(self.allowed_owners) != 1:
            raise ValueError("Local trial requires exactly one owner")
        for value in (self.source_id, self.workspace_id, self.ledger_instance, self.node_id, self.executor_instance):
            TypeAdapter(ID).validate_python(value)
        if type(self.accepting_new) is not bool:
            raise ValueError('accepting_new must be boolean')
        if type(self.allowed_owners) is not tuple or not self.allowed_owners:
            raise ValueError('Explicit immutable owners required')
        for owner in self.allowed_owners:
            if type(owner) is not tuple or len(owner) != 2:
                raise ValueError('Invalid owner')
            for value in owner:
                TypeAdapter(ID).validate_python(value)
        if len(set(self.allowed_owners)) != len(self.allowed_owners):
            raise ValueError('Duplicate owners')
        if type(self.bindings) is not tuple or len(self.bindings) > 32:
            raise ValueError('Immutable bounded bindings required')
        for binding in self.bindings:
            if (type(binding) is not HarnessBinding or not binding.model_alias.startswith(prefixes)
                    or (binding.native_model.startswith('fake-') if trial else not binding.native_model.startswith('fake-'))):
                raise ValueError('Binding does not match execution mode')
        if len({digest(b.wire()) for b in self.bindings}) != len(self.bindings):
            raise ValueError('Duplicate bindings')
        for value, maximum in ((self.start_timeout_seconds, 86400), (self.max_records, 100000),
                               (self.max_result_storage_bytes, 1024 * 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid harness resource bound')

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Harness config must be object')
        value = dict(value)
        value['allowed_owners'] = tuple(tuple(owner) for owner in value['allowed_owners'])
        value['bindings'] = tuple(HarnessBinding.model_validate(b) for b in value['bindings'])
        return cls(**value)


DDL = '''
CREATE TABLE IF NOT EXISTS harness_tasks (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), outcome TEXT NOT NULL,
 liveness TEXT NOT NULL, result TEXT, result_digest TEXT, result_expired INTEGER NOT NULL DEFAULT 0,
 completed_at REAL);
CREATE TABLE IF NOT EXISTS harness_instance (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), ledger_instance TEXT NOT NULL,
 source TEXT NOT NULL, workspace TEXT NOT NULL, node TEXT NOT NULL, executor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS harness_heartbeats (
 source TEXT NOT NULL, workspace TEXT NOT NULL, node TEXT NOT NULL, executor TEXT NOT NULL,
 seen REAL NOT NULL, state TEXT NOT NULL, bindings TEXT NOT NULL,
 PRIMARY KEY(source,workspace,node,executor));
'''


def is_harness(task):
    return json.loads(task['payload']).get('schema_version') == CONTRACT


from .harness_events import HarnessEventsMixin, DDL as EVENT_DDL
DDL += EVENT_DDL


class HarnessMixin(HarnessEventsMixin):
    def _harness_scope(self, scope):
        from .domain import _fail
        source, workspace = self._scope(scope)
        config = self.harness
        if config is None:
            _fail('harness_disabled', 503)
        if (source, workspace) != (config.source_id, config.workspace_id):
            _fail('harness_forbidden', 403)
        return config

    def _harness_owner(self, scope, envelope):
        from .domain import _fail
        config = self._harness_scope(scope)
        identity = envelope['identity']
        if (identity['source'] != config.source_id or envelope['ledger_instance'] != config.ledger_instance
                or (identity['tenant'], identity['principal']) not in config.allowed_owners):
            _fail('harness_forbidden', 403)
        return config

    def _harness_node(self, con, scope, node, executor):
        from .domain import _fail
        config = self._harness_scope(scope)
        self._check_node_active(con, scope, node, executor, required=True)
        if (node, executor) != (config.node_id, config.executor_instance):
            _fail('harness_node_mismatch', 403)
        return config

    def _harness_view(self, con, row):
        from .domain import SourceScope
        envelope = json.loads(row['payload'])
        self._harness_owner(SourceScope(row['source'], row['workspace']), envelope)
        meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?', (row['id'],)).fetchone()
        if meta is None:
            raise RuntimeError('Harness extension evidence missing')
        attempt = con.execute('SELECT * FROM attempts WHERE task_id=?', (row['id'],)).fetchone()
        claim = con.execute('SELECT * FROM claims WHERE task_id=?', (row['id'],)).fetchone()
        outcome, liveness = meta['outcome'], meta['liveness']
        result = json.loads(meta['result']) if meta['result'] is not None else None
        if not attempt['start_grant_id'] and row['deadline'] <= self.clock() and outcome == 'pending':
            outcome = 'expired'
            result = {'outcome': 'failed', 'code': 'TASK_EXPIRED'}
        if (attempt['start_grant_id'] and claim and claim['expires'] <= self.clock()
                and liveness != 'stopped_confirmed'):
            liveness = 'unknown'
        return dict(envelope, remote_task_id=row['id'], attempt_id=row['attempt_id'],
                    created=int(row['created']), outcome=outcome, liveness=liveness,
                    result=result, result_expired=bool(meta['result_expired']),
                    claim_id=claim['id'] if claim else None, epoch=claim['epoch'] if claim else 0,
                    start_grant_id=attempt['start_grant_id'])

    def _expire_harness_before_start(self, con, scope, row):
        """Observed pre-start expiry is a durable tombstone, never clock-reversible."""
        self._harness_owner(scope, json.loads(row['payload']))
        meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?', (row['id'],)).fetchone()
        attempt = con.execute('SELECT * FROM attempts WHERE task_id=?', (row['id'],)).fetchone()
        if (meta['outcome'] == 'pending' and not attempt['start_grant_id']
                and row['deadline'] <= self.clock()):
            value = {'outcome': 'failed', 'code': 'TASK_EXPIRED'}
            con.execute("UPDATE tasks SET state='expired' WHERE id=?", (row['id'],))
            con.execute("UPDATE harness_tasks SET outcome='expired',result=?,result_digest=?,completed_at=? WHERE task_id=?",
                        (canonical(value), digest(value), self.clock(), row['id']))
            self._append_harness_status(con, row['id'], terminal=True)

    def create_harness_task(self, scope, payload):
        from .domain import DomainError, _id
        def _fail(code, status=400):
            error = DomainError(code, status)
            error.accepted = True if code == 'idempotency_conflict' else False
            raise error
        try:
            parsed = HarnessCreate.model_validate(payload)
            payload = parsed.model_dump()
        except (ValidationError, ValueError, TypeError, UnicodeError):
            _fail('invalid_harness_request', 422)
        config = self._harness_owner(scope, payload)
        source, workspace = self._scope(scope)
        with self._transaction() as con:
            old = con.execute('SELECT * FROM tasks WHERE source=? AND workspace=? AND origin_key=?',
                              (source, workspace, payload['origin_key'])).fetchone()
            if old:
                if not is_harness(old) or old['digest'] != payload['request_digest']:
                    _fail('idempotency_conflict', 409)
                # Frozen binding, not the latest live binding, is authoritative.
                if json.loads(old['payload']) != payload:
                    _fail('idempotency_conflict', 409)
                self._expire_harness_before_start(con, scope, old)
                return self._harness_view(con, old)
            if config.execution_mode in {'workbuddy_local_trial', 'harness_local_trial'} and (len(payload['request']['messages']) != 1 or payload['request']['messages'][0]['role'] != 'user'):
                _fail('local_trial_single_user_message_required', 422)
            if not config.accepting_new or not self.accepting_new_tasks:
                _fail('bridge_draining', 503)
            fingerprint = digest(payload['request']['binding'])
            if fingerprint not in {digest(binding.wire()) for binding in config.bindings}:
                _fail('harness_binding_unavailable', 422)
            heartbeat = con.execute('SELECT * FROM harness_heartbeats WHERE source=? AND workspace=? AND node=? AND executor=?',
                                   (source, workspace, config.node_id, config.executor_instance)).fetchone()
            self._harness_node(con, scope, config.node_id, config.executor_instance)
            if (heartbeat is None or not 0 <= self.clock() - heartbeat['seen'] < 90
                    or heartbeat['state'] != 'available' or fingerprint not in json.loads(heartbeat['bindings'])):
                _fail('node_unavailable', 503)
            # No second queue: any existing active task in the shared ledger blocks
            # harness admission. Existing image queue limits remain unchanged.
            if self._active_task_count(con) > 0:
                _fail('queue_capacity', 429)
            count = con.execute('SELECT count(*) FROM harness_tasks').fetchone()[0]
            used = con.execute('SELECT COALESCE(SUM(length(CAST(result AS BLOB))),0) FROM harness_tasks').fetchone()[0]
            if count >= config.max_records or used + MAX_RESULT_WIRE_BYTES > config.max_result_storage_bytes:
                _fail('harness_storage_capacity', 507)
            self._check_harness_event_reservation(con)
            task_id, attempt_id, now = _id('task'), _id('attempt'), self.clock()
            con.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (task_id, source, workspace, payload['origin_key'], payload['request_digest'],
                         canonical(payload), 'harness_queued', attempt_id, 0, now, now + config.start_timeout_seconds))
            con.execute('INSERT INTO attempts(id,task_id) VALUES(?,?)', (attempt_id, task_id))
            con.execute('INSERT INTO harness_tasks(task_id,outcome,liveness) VALUES(?,?,?)',
                        (task_id, 'pending', 'not_started'))
            self._init_harness_events(con, task_id)
            self._audit(con, scope, 'task_created', task_id=task_id, attempt_id=attempt_id)
            return self._harness_view(con, self._scoped(con, 'tasks', task_id, scope))

    def report_harness_heartbeat(self, scope, node, executor, payload):
        from .domain import _fail
        try:
            value = HarnessHeartbeat.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            _fail('invalid_harness_heartbeat', 422)
        with self._transaction() as con:
            config = self._harness_node(con, scope, node, executor)
            allowed = {digest(binding.wire()) for binding in config.bindings}
            if not set(value.binding_digests) <= allowed:
                _fail('harness_binding_unavailable', 422)
            source, workspace = self._scope(scope)
            con.execute('INSERT OR REPLACE INTO harness_heartbeats VALUES(?,?,?,?,?,?,?)',
                        (source, workspace, node, executor, self.clock(), value.state, canonical(value.binding_digests)))
            # Separate freshness domains: never refresh or replace image capabilities.
            return {'accepted': True, 'heartbeat_timeout_seconds': 90, 'harness_contract': CONTRACT}

    def _harness_presence(self, con, source, workspace, node, executor):
        config = self.harness
        if config is None or (source, workspace, node, executor) != (
                config.source_id, config.workspace_id, config.node_id, config.executor_instance):
            return None
        row = con.execute('SELECT * FROM harness_heartbeats WHERE source=? AND workspace=? AND node=? AND executor=?',
                          (source, workspace, node, executor)).fetchone()
        if row is None or not 0 <= self.clock() - row['seen'] < 90:
            return None
        allowed = {digest(binding.wire()) for binding in config.bindings}
        if not allowed.intersection(json.loads(row['bindings'])):
            return None
        return row

    def _authorize_harness_receipt(self, con, scope, task_id, node, executor, claim_id, grant):
        from .domain import _fail
        self._harness_node(con, scope, node, executor)
        task = self._scoped(con, 'tasks', task_id, scope)
        if not is_harness(task):
            _fail('harness_task_required', 409)
        self._harness_owner(scope, json.loads(task['payload']))
        claim = con.execute('SELECT * FROM claims WHERE task_id=?', (task_id,)).fetchone()
        attempt = con.execute('SELECT * FROM attempts WHERE task_id=?', (task_id,)).fetchone()
        # Receipt recovery uses the immutable original consumer and grant. Expiry
        # is not permission to reassign it; post-start claim rotation is forbidden.
        if (claim is None or claim['id'] != claim_id or claim['node'] != node or claim['executor'] != executor
                or not attempt['start_grant_id'] or attempt['start_grant_id'] != grant
                or attempt['original_node'] != node or attempt['original_executor'] != executor):
            _fail('harness_receipt_identity_mismatch', 409)
        return task

    def report_harness_observation(self, scope, task_id, node, executor, payload):
        from .domain import _fail
        try:
            value = Observation.model_validate(payload)
        except (ValidationError, ValueError, TypeError):
            _fail('invalid_harness_observation', 422)
        with self._transaction() as con:
            task = self._authorize_harness_receipt(con, scope, task_id, node, executor, value.claim_id, value.start_grant_id)
            meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?', (task_id,)).fetchone()
            if meta['outcome'] != 'pending':
                _fail('harness_terminal_conflict', 409)
            con.execute('UPDATE harness_tasks SET liveness=? WHERE task_id=?', (value.liveness, task_id))
            con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=?", (task_id,))
            self._append_harness_status(con, task_id, code=value.code)
            return self._harness_view(con, self._scoped(con, 'tasks', task_id, scope))

    def report_harness_result(self, scope, task_id, node, executor, payload):
        from .domain import _fail
        try:
            value = ResultSubmission.model_validate(payload)
            result = value.result.model_dump(exclude_none=True)
            encoded = canonical(result)
        except (ValidationError, ValueError, TypeError, UnicodeError):
            _fail('invalid_harness_result', 422)
        with self._transaction() as con:
            task = self._authorize_harness_receipt(con, scope, task_id, node, executor, value.claim_id, value.start_grant_id)
            meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?', (task_id,)).fetchone()
            result_digest = digest(result)
            if meta['result_digest']:
                if meta['result_digest'] != result_digest:
                    _fail('harness_terminal_conflict', 409)
                return self._harness_view(con, task)
            if meta['outcome'] != 'pending':
                _fail('harness_terminal_conflict', 409)
            expected = json.loads(task['payload'])['request']['binding']['native_model']
            if value.result.outcome == 'succeeded' and (value.result.model_evidence.selected != expected
                                                       or value.result.model_evidence.reported != expected):
                _fail('harness_model_mismatch', 409)
            config = self._harness_scope(scope)
            used = con.execute('SELECT COALESCE(SUM(length(CAST(result AS BLOB))),0) FROM harness_tasks').fetchone()[0]
            if used + len(encoded.encode()) > config.max_result_storage_bytes:
                _fail('harness_storage_capacity', 507)
            con.execute('UPDATE harness_tasks SET outcome=?,liveness=?,result=?,result_digest=?,completed_at=? WHERE task_id=?',
                        (value.result.outcome, value.result.liveness, encoded, result_digest, self.clock(), task_id))
            state = 'harness_terminal' if value.result.liveness == 'stopped_confirmed' else 'execution_unknown'
            con.execute('UPDATE tasks SET state=? WHERE id=?', (state, task_id))
            self._append_harness_status(con, task_id, terminal=True)
            self._audit(con, scope, 'harness_result_committed', task_id=task_id, attempt_id=task['attempt_id'])
            return self._harness_view(con, self._scoped(con, 'tasks', task_id, scope))

    def expire_harness_results(self, scope, *, retention_seconds=86400, batch_size=100):
        """Explicit maintenance, terminal stopped results only; tombstones remain."""
        from .domain import _integer
        self._harness_scope(scope)
        _integer(retention_seconds, 1, 365 * 86400)
        _integer(batch_size, 1, 100)
        source, workspace = self._scope(scope)
        with self._transaction() as con:
            rows = con.execute('''SELECT h.task_id FROM harness_tasks h JOIN tasks t ON t.id=h.task_id
                WHERE t.source=? AND t.workspace=? AND h.result IS NOT NULL
                AND h.outcome!='pending' AND h.liveness='stopped_confirmed' AND h.completed_at<=?
                ORDER BY h.completed_at LIMIT ?''', (source, workspace, self.clock()-retention_seconds, batch_size)).fetchall()
            for row in rows:
                con.execute('UPDATE harness_tasks SET result=NULL,result_expired=1 WHERE task_id=?', (row['task_id'],))
                self._expire_harness_events(con, row['task_id'])
            return {'results_expired': len(rows)}

    def _cancel_harness(self, con, scope, task):
        self._harness_owner(scope, json.loads(task['payload']))
        meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?', (task['id'],)).fetchone()
        attempt = con.execute('SELECT * FROM attempts WHERE task_id=?', (task['id'],)).fetchone()
        if meta['outcome'] != 'pending':
            return self._harness_view(con, task)
        if attempt['start_grant_id']:
            # Cancellation request alone never confirms stop or releases the slot.
            con.execute("UPDATE tasks SET cancel_requested=1,state='cancel_requested' WHERE id=?", (task['id'],))
            con.execute("UPDATE harness_tasks SET liveness='unknown' WHERE task_id=?", (task['id'],))
        else:
            result = {'outcome': 'cancelled', 'liveness': 'not_started', 'output_blocks': []}
            con.execute("UPDATE tasks SET cancel_requested=1,state='harness_terminal' WHERE id=?", (task['id'],))
            con.execute("UPDATE harness_tasks SET outcome='cancelled',liveness='not_started',result=?,result_digest=?,completed_at=? WHERE task_id=?",
                        (canonical(result), digest(result), self.clock(), task['id']))
        self._append_harness_status(con, task['id'], code='CANCEL_REQUESTED' if attempt['start_grant_id'] else None, terminal=not bool(attempt['start_grant_id']))
        self._audit(con, scope, 'cancel_requested' if attempt['start_grant_id'] else 'canceled',
                    task_id=task['id'], attempt_id=task['attempt_id'])
        return self._harness_view(con, self._scoped(con, 'tasks', task['id'], scope))
