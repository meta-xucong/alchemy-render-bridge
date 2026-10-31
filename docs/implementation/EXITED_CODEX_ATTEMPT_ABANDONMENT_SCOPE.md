# Exited Codex Attempt Abandonment Scope

Date: 2026-11-01

## Objective and observed mismatch

The local Codex CLI stream can end with `EOFError` while the executor is waiting
for a turn result. The runner records `execution_unknown` and reports attention.
Its existing interrupted-turn reconciler is only entered for `TimeoutError`,
so this observed EOF path remains unknown indefinitely. Alchemy V2 correctly
polls the same frozen Bridge task and does not create a replacement task. This
preserves duplicate-generation safety but leaves the UI and physical execution
slot stuck after the CLI subprocess has exited.

## Authority and correction model

- Bridge task and attempt state remain authoritative for execution and receipt.
- The Agent's persisted Codex diagnostic is the only evidence accepted for
  automatic abandon of a local exited subprocess.
- An abandon is a terminal failure of that exact attempt. It is not recovery,
  cancellation proof, retry permission, or a new generation request.
- V2's existing same-task query path consumes the Bridge terminal state and
  ends the associated queue task as failed; it must not submit a replacement.

## Bounded change

Add a node-authenticated operation for an operator to abandon one exact
`execution_unknown` Codex attempt. The Agent command must require a persisted
diagnostic matching the requested task and attempt, `stage=turn_wait`,
`failure_class=EOFError`, and a non-null subprocess exit code. The operation
must also confirm through the Bridge API that the same attempt remains
`execution_unknown` and has no committed receipt or upload. The Agent
independently verifies that its journal has no local artifact record. The
Bridge performs the authoritative state/receipt/upload check and
atomically records terminal `failed` with an audit event that identifies the
node, task, attempt, and fixed reason code. Repeating the same abandon is
idempotent; a different attempt or any incomplete/mismatched evidence is
rejected without state change.

The local launcher must run a one-shot exited-attempt reconciliation after the
Bridge API is ready and before it starts the `hatchet_sidecar pull-worker`.
That ordering ensures the worker cannot first replay recovery records through
the existing rollout-reconciliation path. The reconciler scans only local
journal metadata and corresponding allowlisted diagnostic metadata. It invokes
abandon only for records that meet every check. Non-matching or incomplete
local evidence leaves that record `execution_unknown`; the Agent skips it and
does not send it through rollout recovery. The physical-slot latch remains in
place, so the unknown attempt still prevents a new execution. A transient Bridge
error on an otherwise eligible attempt also leaves it unknown. This is a
startup-only operation, not a heartbeat, periodic poll, or per-task execution
hook.

Evidence responsibilities are split: Agent validates matching task/attempt
diagnostic, `turn_wait` + `EOFError`, a non-null subprocess exit code, and no
local journal artifact record. Bridge validates its own authoritative matching
`execution_unknown` task/attempt, original node/executor identity, and absence
of any upload or receipt in one transaction. Bridge never reads Agent state.

For this incident, the user has authorized abandonment of old tasks. An older
V2 queue item with no matching current Bridge record is canceled only through
Alchemy's existing public cancellation API; direct Alchemy database edits are
prohibited. That API cancellation is an explicitly authorized incident
cleanup, separate from the Bridge code change.

## Explicit exclusions

- Do not inspect or parse prompts, image bytes, Codex rollout files, or old
  generation output directories.
- Do not calculate or verify SHA, hashes, checksums, or fingerprints.
- Do not automatically retry, re-execute, upload, or choose another output.
  Do not edit an Alchemy database directly. The only authorized incident
  cleanup is cancellation of the identified old queue item through the existing
  Alchemy public API as specified above.
- Do not terminalize, retry, or release capacity for an
  `execution_unknown` attempt when exit evidence is missing, the subprocess may
  still be active, task/attempt/node identities do not match, or any
  artifact/upload/receipt exists. Such attempts remain latched and are skipped
  by startup rollout recovery.
- Do not modify the Alchemy V2 retry contract unless the existing integration
  regression demonstrates a concrete defect after Bridge reports `failed`.

## Verification and release boundary

Before reconnecting services or invoking a real generation, test the new API
and command for exact task/attempt binding, required EOF exit evidence,
artifact/receipt/upload rejection, idempotent terminalization, concurrent
state change rejection, preservation of no-retry behavior, and startup behavior
when unrelated unknown records lack matching EOF exit evidence. Then run the
focused V2 integration regression for a terminal Bridge failure. An independent
scope review and code audit must pass. Only then may the current exited task be
abandoned, local services reconnected, and one user-authorized real V2 image
generation run as acceptance. Record phase evidence separately from total
acceptance.

## Bridge implementation record

The Bridge exposes a node-authenticated abandon operation with a fixed reason
code. Its transaction checks Codex profile, exact attempt, persisted start
grant, original node/executor, `execution_unknown`, and absence of every upload
and receipt before recording `failed`. A task already `failed` is accepted only
as an idempotent replay when the exact persisted abandon confirmation exists.
The audit event records node, executor, task, attempt, and reason.

Before pull-worker recovery replay, Agent startup scans journal fields through
SQLite metadata projections, then reads only the allowlisted diagnostic fields
from `executors/<task>/<attempt>/codex-execution-diagnostic.json`. It requires
`schema_version=1`, `turn_wait`, `EOFError`, a non-null integer process exit
code, exact journal task/attempt binding, a non-empty persisted start grant,
and no local artifact metadata. After these local gates, it calls only the
narrow abandon operation; Bridge atomically checks the task state, attempt,
profile, original node/executor, start grant, and receipt/upload absence. The
operation returns only task, attempt, status, and fixed reason code. A Bridge
commit followed by an Agent crash can replay only the same persisted
confirmation. Missing, mismatched, or non-EOF diagnostic evidence leaves that
attempt unknown and latched; startup does not pass it into rollout recovery.
An API error on a matching EOF candidate leaves it unknown and latched.

The initial API regression run preceded route wiring and returned `404`; it was
an implementation-stage failure. A later draft used a synthetic digest
calculation and was removed. A broader 25-test run is excluded because it
executed an unrelated existing synthetic SHA test. No old task data was read
for these checks. The first 10-node focused run (5.26 seconds) predates the
startup-skip correction and is intermediate evidence only. After the startup
skip and malformed-response guards were implemented, the following 14 exact
focused test nodes passed together in 1.911 seconds:

- `test_node_operations.NodeOperationsTests.test_abandon_exited_attempt_is_bound_idempotent_and_fail_closed`
- `test_node_operations.NodeOperationsTests.test_abandon_exited_attempt_rejects_active_or_uploaded_attempt`
- `test_node_operations.NodeOperationsTests.test_abandon_exited_attempt_rejects_existing_receipt`
- `test_node_operations.NodeOperationsTests.test_abandon_rechecks_state_after_concurrent_transition`
- `test_node_operations.NodeOperationsTests.test_abandon_rejects_failed_task_without_persisted_abandon_record`
- `test_terminal_failure.TerminalFailureTests.test_startup_abandons_only_bound_exited_eof_attempt`
- `test_terminal_failure.TerminalFailureTests.test_startup_bridge_error_keeps_unknown_attempt_latched`
- `test_terminal_failure.TerminalFailureTests.test_startup_malformed_bridge_confirmation_keeps_unknown_latched`
- `test_terminal_failure.TerminalFailureTests.test_startup_replays_commit_after_agent_crash_before_journal_update`
- `test_terminal_failure.TerminalFailureTests.test_startup_skips_empty_persisted_start_grant`
- `test_terminal_failure.TerminalFailureTests.test_startup_skips_journal_record_attempt_id_mismatch`
- `test_terminal_failure.TerminalFailureTests.test_startup_skips_missing_or_mismatched_diagnostic_and_keeps_latch`
- `test_terminal_failure.TerminalFailureTests.test_startup_skips_journal_artifact_without_bridge_call`
- `test_agent_dispatch.DispatchTests.test_pull_startup_reconciles_before_recovery_replay`

Startup tests confirm that unsupported unknown records, including missing or
mismatched diagnostics, empty grant, local artifact metadata, and transient
Bridge errors, remain latched and do not abort startup. Recovery replay invokes
only the terminal failure-report path; unknown execution and start-requested
records are never passed to rollout reconciliation. The attempt-ID mismatch
case mutates only synthetic local journal JSON and verifies no API call occurs.
An earlier implementation draft did compute a synthetic fixture digest; that
draft was replaced with a fixed literal. A broad test selection also
unintentionally ran one existing synthetic SHA test. Neither event accessed
old task data. No packages were installed.

## Controlled integrated acceptance

Date: 2026-11-01

- The final independent Bridge code audit passed. The exact 14 focused nodes
  also passed on the active launcher worktree (`codex/local-bridge-setup-20261101`)
  in 5.086 seconds. Alchemy's terminal-Bridge-failure queue regression passed
  on integrated `main` (1 test, 2.90 seconds).
- The Alchemy fix was fast-forwarded and pushed as `daa3889b084556416c8a30f417d4b46c10551fbf`.
  The Bridge fix is committed as `749875e94d5198db212efc137207df9426c69709`
  and cherry-picked into the local launcher branch as
  `108880e58a7a48a7b10d79f65a1401e399d50922`; it has not been pushed to
  Bridge `main`, whose checkout contains pre-existing user edits in overlapping
  files. The current local launcher uses the cherry-picked branch.
- Local Alchemy V1/V3/Lab health, V2 health, authenticated Bridge connection,
  and the `codex-image` node were verified. The node returned to `available`
  with zero active Bridge tasks after acceptance.
- One new V2 run (`run_82ec66429c50`) completed through provider `render_bridge`:
  queue task `task_71e5b7912150`, job `job_5d597c284d1e`, output
  `out_19cdd51ac847dcc498305a52329b3058`. The queue claim was released, the
  run/job were `completed`, and the 1024x1536 PNG download endpoint returned
  HTTP 200.
- One obsolete queued Bridge-owned V2 job was canceled through Alchemy's
  public cancellation API; no Alchemy database was edited directly. The queue
  had no pending items before the new acceptance run.
- **Scope exception:** the cancellation endpoint returned the full image job,
  including its frozen prompt, and the response was accidentally serialized in
  tool output. This exceeded the old-task inspection boundary. The prompt is
  not reproduced or used. The user was notified in the conversation
  immediately after the response; the companion error log is the Alchemy
  workspace's [`.learnings/ERRORS.md`](D:/AI/Alchemy%20Media%20Agent%20System/.learnings/ERRORS.md),
  entry `ERR-20261101-002`. Future cancellation results must be projected to
  an explicit metadata allowlist before output.

This passes the local Windows end-to-end acceptance for the current launcher
branch and one V2 real-generation task. Bridge `main` integration remains open;
the local startup entrypoint continues to use the tested launcher branch.
