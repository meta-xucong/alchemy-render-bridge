# Cancel completion for a locally finished Codex artifact

## Observed mismatch

An automatic Codex task can receive `cancel_requested` after execution has
started. The Agent correctly keeps the Bridge slot occupied while execution may
still be running. If the local journal already has the same attempt in
`artifact_ready`, execution has returned and the artifact is durable, but the
cancel flag prevents upload. Without an acknowledgement, the task remains
`cancel_requested` and keeps occupying the slot.

## Contract change

Add the node-only endpoint
`POST /bridge/node/v1/tasks/{task_id}/cancel-completed` with
`{"attempt_id":"..."}`. A trusted Agent calls it only for a journal record
whose state is `artifact_ready`; it never uploads those bytes or starts Codex
again. The Bridge atomically checks the active node identity, Codex profile,
attempt binding, start grant, `cancel_requested` state, and absence of uploads
and receipts. It then records an idempotent cancellation confirmation, changes
the task to `canceled`, and writes a bounded audit event.

Requests from running, queued, unknown, wrong-attempt, or already-uploaded work
are rejected. A task without a durable local artifact stays latched. Existing
clients are unaffected; the endpoint is additive and does not change Alchemy's
source API, generation routing, or billing behavior.

## Validation

Synthetic API and Agent tests cover exact binding, idempotent replay, rejection
of active or uploaded attempts, release of the Bridge active-task count, and
the Agent rule that a missing artifact never calls the endpoint. Tests do not
invoke Codex or create a real Alchemy task.
