# Durable native events v1 (development contract)

This is an opt-in extension of the existing Harness task ledger, not a new scheduler, a stop proof, or an execution retry mechanism. It does not enable accounts, providers, deployment, billing, or commercial transfer rights. Native text/status events are provisional observations from the authorized original Node; only the existing validated result receipt makes a task-result terminal.

## Node upload

`POST /bridge/node/v1/tasks/{task_id}/harness-event`, with the existing Node bearer credential:

```json
{"claim_id":"original-claim","start_grant_id":"original-grant","event_seq":1,"event":{"type":"text_snapshot","text":"authoritative root text so far"}}
```

Alternatively event is `{"type":"native_status","state":"running"}`. Allowed states are `running`, `requires_action`, `idle`, `unknown`; optional stop_reason is `end_turn`, `refusal`, `cancelled`, `_codebuddy.ai/error`. No arbitrary vendor objects or separate credential/tool-argument/reasoning/path fields are accepted. The text field is still model output and is not automatically redacted; this schema cannot guarantee that a model will never include sensitive content in its answer. Empty text is valid for a replacement snapshot. A snapshot may replace a longer earlier value; consumers must not turn it into a fabricated append delta.

The request uses the original claim/start grant and active original Node/executor. `event_seq` is the Node producer sequence from 1, independent of the Bridge event cursor. Same producer sequence and same normalized event is idempotently acknowledged; a changed event, gap, unknown receipt identity, or new event after task-terminal is rejected. ACK exactly includes `remote_task_id`, `claim_id`, `start_grant_id`, `event_seq`, `accepted:true`. A lost ACK permits only resubmission of that same observation, never another native generation.

## Source read

`GET /bridge/v1/tasks/{task_id}/harness-events?after=0&limit=8`, using the existing Source bearer credential and its frozen source/workspace/allowed-owner scope. Query keys are only after/limit, not repeated. after is a nonnegative decimal cursor; limit is 1–8.

```json
{"schema_version":"native-events-v1","remote_task_id":"task-id","events":[{"seq":1,"type":"task_status","data":{"outcome":"pending","liveness":"not_started"}}],"next_cursor":1,"has_more":false,"terminal_seq":null}
```

Bridge seq is contiguous and durable within the existing task. The source cursor must never exceed the latest sequence (409), never jumps or silently drops events, and does not create a task, claim or start grant. GET is execution-read-only, not strictly database-read-only: an observed pre-start deadline expiry or post-start claim expiry checkpoints a durable task status in the same transaction. It never grants, cancels, releases or starts work. A clock rollback cannot revive the persisted unknown/expired status. All events in a page are committed before they are visible. Creation and start-grant state, explicit uncertainty/cancellation observations, and a validated final result are committed with their system event in the same SQLite transaction.

Native uploads become `text_snapshot` or `native_status`, with their event fields (except type) in data. System status is `task_status` with outcome/liveness and an optional fixed code. A validated final result, durable pre-start expiry, or pre-start cancellation produces `task_terminal`, in its own page. Its data contains outcome/liveness and optional result code, not an invented OpenAI completion. Facade must retrieve and validate the same frozen task/result before projecting a completion. A native idle/end_turn notification by itself never emits task_terminal. Final result status and execution liveness remain separate: a result can be terminal while an unknown native process still keeps the slot quarantined.

`terminal_seq` is null until that durable event exists. Reconnect after the terminal seq returns an empty page with that same terminal_seq, so callers can close without repeating content. New events are rejected after terminal; exact existing Node uploads remain ACK-replayable. Disconnecting the observer does not cancel or reassign the task. A task created before event logging was installed returns 409 `event_history_unavailable`, never a reconstructed fake history or a new generation; its existing nonstream result recovery is unchanged.

## Limits and retention

- Native text snapshot: at most 262144 decoded UTF-8 bytes.
- Per-event wire and total response page: at most `6*262144+4096` bytes. Max 8 events per page; terminal is always alone so Facade can safely expand it to the bounded final completion.
- At most 512 native events and 16 MiB canonical native-event bytes per task. Global retained native-event bytes are capped at 64 MiB. Admission checks current retained usage plus 16 MiB, not a transferable reservation or promise against later capacity changes. Subsequent uploads still independently enforce the global budget and can explicitly fail 507. Limit failure is explicit 507; no successful ACK or hidden dropping.
- System state deduplicates adjacent identical observations and has its own bounded 64-entry allowance plus a reserved terminal entry, so native-event capacity cannot suppress a valid result receipt. These limits are not permission to claim a lost stream was complete.
- Explicit existing result-retention maintenance removes that task's event payloads in the same transaction as result removal and preserves cursor/terminal tombstones. Further event retrieval returns 410, not a new execution. Existing operation/identity tombstones remain.

The native publisher must keep ordered, bounded delivery. An unresolved delivery failure must be reported as uncertainty, not a complete successful stream. Native tools may have already had effects even if a stream fails. Source reads do not grant execution, and no replay is allowed to bypass the original ledger/idempotency checks.

Validation is synthetic tests only until separately recorded. This document does not establish real WorkBuddy login, model invocation, production TLS/network readiness or user acceptance.
