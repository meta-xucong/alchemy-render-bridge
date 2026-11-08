"""Bounded durable Harness observations, never execution authority or a second ledger."""
from __future__ import annotations
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

SCHEMA = 'native-events-v1'
MAX_TEXT_BYTES = 262144
MAX_EVENT_WIRE_BYTES = 6 * MAX_TEXT_BYTES + 4096
MAX_PAGE_BYTES = MAX_EVENT_WIRE_BYTES
MAX_NATIVE_EVENTS = 512
MAX_TASK_EVENT_BYTES = 16 * 1024 * 1024
MAX_EVENT_STORAGE_BYTES = 64 * 1024 * 1024
MAX_SYSTEM_EVENTS = 64
ID = Annotated[StrictStr, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')]

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class TextSnapshot(Strict):
    type: Literal['text_snapshot']
    text: StrictStr
    @model_validator(mode='after')
    def bounded(self):
        if len(self.text.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('Snapshot text limit')
        return self

class NativeStatus(Strict):
    type: Literal['native_status']
    state: Literal['running','requires_action','idle','unknown']
    stop_reason: Literal['end_turn','refusal','cancelled','_codebuddy.ai/error'] | None = None

class EventSubmission(Strict):
    claim_id: ID
    start_grant_id: ID
    event_seq: Annotated[StrictInt, Field(ge=1, le=MAX_NATIVE_EVENTS)]
    event: Annotated[TextSnapshot | NativeStatus, Field(discriminator='type')]
    @model_validator(mode='after')
    def bounded(self):
        from .harness import canonical
        if len(canonical(self.model_dump(exclude_none=True)).encode('utf-8')) > MAX_EVENT_WIRE_BYTES:
            raise ValueError('Event wire limit')
        return self

DDL = '''
CREATE TABLE IF NOT EXISTS harness_event_state (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), next_seq INTEGER NOT NULL DEFAULT 1,
 next_node_seq INTEGER NOT NULL DEFAULT 1, native_count INTEGER NOT NULL DEFAULT 0,
 native_bytes INTEGER NOT NULL DEFAULT 0, system_count INTEGER NOT NULL DEFAULT 0,
 last_status_digest TEXT, terminal_seq INTEGER, expired INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS harness_events (
 task_id TEXT NOT NULL REFERENCES tasks(id), seq INTEGER NOT NULL, type TEXT NOT NULL,
 data TEXT NOT NULL, producer_seq INTEGER, event_digest TEXT NOT NULL,
 PRIMARY KEY(task_id,seq), UNIQUE(task_id,producer_seq));
'''

class HarnessEventsMixin:
    def _init_harness_events(self, con, task_id):
        con.execute('INSERT INTO harness_event_state(task_id) VALUES(?)', (task_id,))
        self._append_harness_status(con, task_id)

    def _append_harness_status(self, con, task_id, *, code=None, terminal=False):
        from .harness import canonical, digest
        from .domain import _fail
        state = con.execute('SELECT * FROM harness_event_state WHERE task_id=?',(task_id,)).fetchone()
        # Old ledgers have no native history. Never fabricate a history on reads.
        if state is None or state['expired'] or state['terminal_seq'] is not None:
            return
        meta = con.execute('SELECT * FROM harness_tasks WHERE task_id=?',(task_id,)).fetchone()
        data = {'outcome':meta['outcome'], 'liveness':meta['liveness']}
        if code is not None: data['code'] = code
        elif terminal and meta['result']:
            value=json.loads(meta['result'])
            if 'code' in value:data['code']=value['code']
        kind = 'task_terminal' if terminal else 'task_status'
        event_hash = digest({'type':kind,'data':data})
        if event_hash == state['last_status_digest']:return
        if not terminal and state['system_count'] >= MAX_SYSTEM_EVENTS:
            _fail('harness_event_capacity',507)
        seq=state['next_seq']
        con.execute('INSERT INTO harness_events VALUES(?,?,?,?,NULL,?)',(task_id,seq,kind,canonical(data),event_hash))
        con.execute('UPDATE harness_event_state SET next_seq=?,system_count=system_count+1,last_status_digest=?,terminal_seq=? WHERE task_id=?',
                    (seq+1,event_hash,seq if terminal else None,task_id))

    def _check_harness_event_reservation(self, con):
        from .domain import _fail
        used=con.execute('SELECT COALESCE(SUM(native_bytes),0) FROM harness_event_state').fetchone()[0]
        if used+MAX_TASK_EVENT_BYTES > MAX_EVENT_STORAGE_BYTES:
            _fail('harness_event_capacity',507)

    def report_harness_event(self, scope, task_id, node, executor, payload):
        from .harness import canonical, digest
        from .domain import _fail
        try:
            value=EventSubmission.model_validate(payload)
            event=value.event.model_dump(exclude_none=True)
            encoded=canonical(event);event_hash=digest(event)
        except (ValidationError,ValueError,TypeError,UnicodeError):
            _fail('invalid_harness_event',422)
        with self._transaction() as con:
            self._authorize_harness_receipt(con,scope,task_id,node,executor,value.claim_id,value.start_grant_id)
            state=con.execute('SELECT * FROM harness_event_state WHERE task_id=?',(task_id,)).fetchone()
            if state is None:_fail('event_history_unavailable',409)
            if state['expired']:_fail('event_history_expired',410)
            previous=con.execute('SELECT event_digest FROM harness_events WHERE task_id=? AND producer_seq=?',(task_id,value.event_seq)).fetchone()
            if previous:
                if previous['event_digest']!=event_hash:_fail('harness_event_conflict',409)
            else:
                if state['terminal_seq'] is not None:_fail('harness_terminal_conflict',409)
                if value.event_seq!=state['next_node_seq']:_fail('harness_event_sequence_gap',409)
                size=len(encoded.encode('utf-8'))
                used=con.execute('SELECT COALESCE(SUM(native_bytes),0) FROM harness_event_state').fetchone()[0]
                if (state['native_count']>=MAX_NATIVE_EVENTS or state['native_bytes']+size>MAX_TASK_EVENT_BYTES
                        or used+size>MAX_EVENT_STORAGE_BYTES):_fail('harness_event_capacity',507)
                data={key:item for key,item in event.items() if key!='type'}
                con.execute('INSERT INTO harness_events VALUES(?,?,?,?,?,?)',
                            (task_id,state['next_seq'],event['type'],canonical(data),value.event_seq,event_hash))
                con.execute('UPDATE harness_event_state SET next_seq=next_seq+1,next_node_seq=next_node_seq+1,native_count=native_count+1,native_bytes=native_bytes+? WHERE task_id=?',(size,task_id))
            return {'remote_task_id':task_id,'claim_id':value.claim_id,'start_grant_id':value.start_grant_id,
                    'event_seq':value.event_seq,'accepted':True}

    def harness_events(self, scope, task_id, *, after=0, limit=8):
        from .harness import canonical, is_harness
        from .domain import _fail
        if type(after) is not int or not 0<=after<=2**63-1 or type(limit) is not int or not 1<=limit<=8:
            _fail('invalid_event_cursor',422)
        # Observing lease/deadline expiry checkpoints facts, never grants work.
        with self._transaction() as con:
            self._harness_scope(scope)
            task=self._scoped(con,'tasks',task_id,scope)
            if not is_harness(task):_fail('harness_task_required',409)
            self._harness_owner(scope,json.loads(task['payload']))
            state=con.execute('SELECT * FROM harness_event_state WHERE task_id=?',(task_id,)).fetchone()
            if state is None:_fail('event_history_unavailable',409)
            if state['expired']:_fail('event_history_expired',410)
            self._expire_harness_before_start(con,scope,task)
            meta=con.execute('SELECT outcome,liveness FROM harness_tasks WHERE task_id=?',(task_id,)).fetchone()
            attempt=con.execute('SELECT start_grant_id FROM attempts WHERE task_id=?',(task_id,)).fetchone()
            claim=con.execute('SELECT expires FROM claims WHERE task_id=?',(task_id,)).fetchone()
            if (state['terminal_seq'] is None and meta['outcome']=='pending' and attempt['start_grant_id']
                    and claim is not None and claim['expires']<=self.clock() and meta['liveness']!='unknown'):
                con.execute("UPDATE harness_tasks SET liveness='unknown' WHERE task_id=?",(task_id,))
                con.execute("UPDATE tasks SET state='execution_unknown' WHERE id=?",(task_id,))
                self._append_harness_status(con,task_id,code='EXECUTION_UNCERTAIN')
            state=con.execute('SELECT * FROM harness_event_state WHERE task_id=?',(task_id,)).fetchone()
            latest=state['next_seq']-1
            if after>latest:_fail('event_cursor_ahead',409)
            page={'schema_version':SCHEMA,'remote_task_id':task_id,'events':[],'next_cursor':after,
                  'has_more':after<latest,'terminal_seq':state['terminal_seq']}
            rows=con.execute('SELECT seq,type,data FROM harness_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?',
                             (task_id,after,limit)).fetchall()
            if not rows and after < latest:_fail('event_history_incomplete',409)
            for row in rows:
                if row['type']=='task_terminal' and page['events']:break
                event={'seq':row['seq'],'type':row['type'],'data':json.loads(row['data'])}
                if event['seq']!=page['next_cursor']+1:_fail('event_history_incomplete',409)
                candidate=dict(page,events=page['events']+[event],next_cursor=event['seq'],has_more=event['seq']<latest)
                if len(canonical(candidate).encode('utf-8'))>MAX_PAGE_BYTES:
                    if not page['events']:_fail('harness_event_too_large',500)
                    break
                page=candidate
            return page

    def _expire_harness_events(self, con, task_id):
        con.execute('DELETE FROM harness_events WHERE task_id=?',(task_id,))
        con.execute('UPDATE harness_event_state SET expired=1,native_bytes=0 WHERE task_id=?',(task_id,))
