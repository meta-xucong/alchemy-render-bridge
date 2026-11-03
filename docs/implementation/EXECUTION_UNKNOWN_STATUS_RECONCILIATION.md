# Execution-unknown status and reconciliation

## Correction model

Expected behavior: a task whose renderer may have started must retain its local
physical slot until same-attempt evidence resolves it. A cancellation request
does not prove that the renderer stopped. While that slot is held, the node must
stop accepting new work and local/server status must make reconciliation visible.

Observed mismatch: after Codex returned without a confirmed terminal image event,
the local journal retained `execution_unknown`. The heartbeat mapped that
unresolved journal entry to `busy`, although node-pull treats `busy` as admissible.
The status window looked only at the live `_active` flag, so it could display idle
while the journal still held the slot. The task API kept `cancel_requested` but
suppressed `action_required_detail`; `report_attention` ignored cancel-requested
tasks. Local attention reporting was one-shot, so a transient failure could hide
the reconciliation reason on the server.

The failure is in execution-state projection and report reconciliation. The local
attempt journal remains authoritative for physical slot occupancy; the Bridge
task/attempt ledger remains authoritative for public task state. Preserve
`cancel_requested` until an explicit terminal result is known, and expose the
reconciliation detail alongside it. Unknown execution remains fail-closed: no
rerender, slot release, or inferred cancellation completion.

## Minimal correction

1. Report an unresolved journal slot as node `attention`; reserve `busy` for the
   active task call. `attention` already closes node-pull admission.
2. Show an unresolved journal slot in the Windows status window as held / pending
   reconciliation, instead of idle.
3. Retry failed attention reports through the existing heartbeat reconciliation
   path. The retry is bound to the same task and attempt and never starts a renderer.
4. Accept post-start attention for cancel-requested tasks, persist the allowlisted
   reason, and retain public state `cancel_requested`.
5. Include `action_required_detail` on a started cancel-requested task. Keep the
   existing state and `action_required` fields unchanged.
6. At the configured Codex turn timeout (600 seconds by default), stop the local
   app-server session and expose a specific timeout error. Keep the attempt
   unknown and held because stopping the local process does not prove upstream
   cancellation. The existing one-hour execution claim expiry later changes the
   message to an overdue/lease-expired explanation; it still does not unlock.

Upstream HTTP status capture and the observed diagnostic timestamp discrepancy
are separate evidence gaps. This change does not infer a 401 or alter Codex
timeouts, subprocess output capture, retries, or provider behavior.

## Scope and validation

Bridge Agent, Codex timeout reporting, Windows status display, task-state
projection, and allowlisted messages only. No Alchemy business code, API schema version, generation entry
point, cancellation semantics, merge, push, or deployment. Static diff review
only; automated tests and real generation were not run.
