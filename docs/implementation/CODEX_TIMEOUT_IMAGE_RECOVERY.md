# Codex timeout image recovery

## Objective

When Codex has saved the one requested image but the Bridge app-server turn
does not reach a normal terminal event before the Bridge timeout, recover that
same image into the same Bridge task and attempt when the local Codex rollout
proves the exact interrupted execution. Do not start another turn or image.

This is a Bridge-side recovery. Alchemy continues querying the same Bridge task
through its existing API and keeps its existing review, billing, and result
projection behavior.

## Incident and owning layer

The observed V2 task reached Codex. Its local rollout records one completed
`image_gen.generation` item with a saved path, followed by
`turn_aborted(reason="interrupted")`. The Bridge executor waited 120 seconds
for `turn/completed`, then closed the Codex process and returned
`execution_unknown`. Its diagnostic retained event counts but no image-item
event, and its attempt directory had no artifact.

The Bridge executor owns app-server completion capture and timeout behavior.
The Bridge Agent owns task/attempt identity, the start grant, recovery, and
artifact upload. Alchemy's 120-second query loop is a projection of the
unresolved Bridge task; it is not the generation or recovery authority.

The diagnostic proves that the live event collector captured no lifecycle
event. It does not preserve enough safe fields to identify why the completed
image item was absent from that stream. This change therefore does not guess
at another app-server event shape. It uses the already supported, identity-
bound rollout parser after the executor has stopped and returned unknown.

### Follow-up: V2 artifact delivery remains pending

A later frontend V2 run reached a local Agent journal state of
`artifact_ready` with `codex_aborted_turn_review_only` provenance. The Bridge
task remained `execution_unknown` without a receipt, and the V2 job surfaced as
retryable `bridge_wait`. The available local record had no persisted
`upload_only` claim or upload ID. This establishes that generation recovery
produced a locally persisted artifact but Alchemy had not received a committed
Bridge receipt. No safe log record preserved the HTTP error from claiming the
upload lease, so `claim_busy` is the code-supported failure hypothesis rather
than a directly observed response.

The cross-layer correction has two required parts. First, for every Codex
`artifact_ready` replay, the Agent selects `upload_only` from the current
Bridge task state (`execution_unknown`), not only from rollout provenance.
Second, Bridge allows that same original node and executor to promote an active
`execute` lease to `upload_only` after the attempt is durably unknown. The
promotion rotates the claim ID and epoch in one transaction. Rollout provenance
continues to govern its proof checks and never authorizes a new Codex execution.
V2 keeps polling the same frozen Bridge task; its route, billing, and fallback
behavior do not change.

## Authoritative evidence and recovery rule

The Bridge task and Agent journal remain authoritative for the task, attempt,
frozen request, profile revision, and start grant. The local Codex rollout is
supporting terminal evidence only. A saved image path or file by itself is not
proof of task completion.

Automatic recovery is attempted only after the Codex executor returns
`execution_unknown` with `codex_execution_requires_reconciliation`, and its
bounded diagnostic metadata says `stage=turn_wait` and
`failure_class=TimeoutError`. Other transport, protocol, or parsing exceptions
remain unknown and do not trigger automatic rollout discovery. Recovery runs
after the app-server process has been closed and after the Agent has recorded
the same task as `execution_unknown` at Bridge. `execute_task()` must release its
`InstanceLock` before entering the public reconciliation path, which acquires
that lock itself. Before searching local rollout files, the Agent re-reads the
Bridge task and confirms the same task and attempt are still
`execution_unknown` with no receipt. The reconciler repeats these checks while
holding the lock before parsing or writing recovery evidence. If Bridge cannot
confirm the unknown state, automatic recovery stops without reading a rollout
or uploading.

The Agent finds the rollout by the persisted Codex thread ID under the
configured `CODEX_HOME/sessions` tree. It continues only when exactly one
rollout matches that thread. The reconciler opens the rollout once, reads from
that file descriptor, and verifies the opened file identity and resolved path
remain inside `CODEX_HOME/sessions` before and after the read. It also pins the
home and sessions directory identities for the read; a root/path swap or file
mutation during the read fails closed. The existing reconciler must then prove:

1. The rollout session, turn, task, attempt, frozen request, Bridge task
   digest, profile revision, and original start grant match the persisted
   records.
2. The rollout contains exactly one completed image-generation item for that
   turn, with its event-bound original under that thread's
   `generated_images/<thread_id>/` directory.
3. The same turn ends with `turn_aborted(reason="interrupted")`, and there is
   no conflicting terminal or image evidence.
4. The original passes the existing image and artifact checks.

If every condition passes, reuse the existing interrupted-image reconciliation
contract, persist its proof on the original attempt, and use the existing
upload-only claim and artifact/receipt path. The result remains `review_only`;
an interrupted turn is never upgraded to normal turn success. If discovery is
ambiguous, evidence is incomplete, or any binding differs, preserve
`execution_unknown` and the occupied execution slot. Never invoke `execute`,
preflight, `thread/resume`, another turn, API fallback, or a new Alchemy job.

Once the same attempt is durably `execution_unknown`, its original node and
executor may atomically promote their still-live `execute` claim to a new
`upload_only` claim. This promotion advances the claim epoch, fencing the old
claim, and grants no execution permission. A different node/executor, another
profile, an unstarted task, or a task not durably marked `execution_unknown`
cannot use this transition. This lets the Agent upload the already validated
artifact immediately instead of waiting for the one-hour Node API claim lease
to expire. If the upload-only claim is still unavailable for another reason,
retain the validated artifact and proof on the same attempt and retry only that
claim and upload; do not rescan or execute Codex again.

The upload mode is selected from the Bridge task state for every Codex
`artifact_ready` replay: `execution_unknown` requires `upload_only`, regardless
of artifact provenance. Rollout provenance continues to control its existing
proof validation; it does not decide whether an unknown attempt is allowed to
upload. If a prior upload already recorded `claim_mode=upload_only`, replay
continues using that mode even while the Bridge task projects `uploading`.

The explicit `reconcile-codex-interrupted` command remains available for
operator-led recovery. Automatic recovery and the command must call the same
validator and persistence path.

## Contract revision

This document revises the explicit-trigger-only sentence in section 10 of
`08_自动回退代码落地与兼容契约.md`. The existing evidence schema and
`review_only` meaning do not change. The new automatic trigger is limited to
the post-timeout Agent path above; the explicit command remains supported for
operator-led recovery. The versioned HTTP API, Alchemy state machine, and
Alchemy-side review/billing authority remain unchanged.

## Smallest implementation

- Add exact-thread rollout discovery to the Codex executor, using the configured
  Codex home and rejecting zero or multiple matches.
- Read the rollout through one bounded file descriptor, checking its opened
  identity and resolved sessions-tree scope around the read, and checking that
  the Codex home/sessions directory identities remain stable.
- After a qualifying unknown result, have the Agent report unknown first, then
  call the existing interrupted-rollout reconciler with the original Bridge
  task/attempt binding. Share the locked reconciliation implementation with
  the explicit command rather than duplicating its checks.
- Allow only the original node/executor to promote its active execute claim to
  upload-only after same-attempt Codex evidence is persisted as
  `execution_unknown`; advance the claim epoch in the same transaction.
- When replaying any Codex `artifact_ready` record, choose upload-only from the
  authoritative Bridge task state and preserve an already acquired
  upload-only claim across retries. Keep rollout-proof validation tied only to
  rollout provenance.
- Keep every other unknown result on the current fail-closed path.
- Do not change Alchemy, the shared API schema, the live success predicate, or
  the 120-second timeout in this correction.

## Acceptance

Focused tests must prove that a qualifying timeout can commit only the image
from the unique matching interrupted rollout through the original attempt;
that a missing/ambiguous rollout, wrong thread or turn, extra image, conflicting
terminal event, outside-sessions or changed rollout, or mismatched task binding
remains unknown; that non-timeout unknown results do not invoke rollout recovery;
and that explicit recovery
still uses the same path. Tests must not invoke a real image provider or create
an Alchemy task.

The Bridge recovery contract and deterministic local tests are necessary but
not sufficient for end-to-end acceptance. Windows live-account acceptance is
recorded separately below and is limited to the exact recovered task.

## Windows local end-to-end acceptance (2026-11-01)

- Recovered the existing same-attempt artifact in Bridge without invoking
  Codex again. The original Bridge task moved from `execution_unknown` to
  `artifacts_ready`; the existing attempt retained its identity, acquired an
  epoch-fenced `upload_only` claim, and committed one receipt.
- The local V2 API became healthy on `127.0.0.1:8020`. Its existing queued run
  reached `completed`, with its existing generation job completed and one
  persisted output. No additional image-generation task was created.
- The persisted output's V2 download route returned HTTP 200 with
  `image/png`; the PNG signature was valid. The local frontend on
  `127.0.0.1:8017` returned HTTP 200.
- The real same-attempt recovery and V2 persistence/download path passed for
  this one task. This does not establish a guarantee for every future task or
  prove public/VPS deployment behavior.
- Post-acceptance rerun: `tests.test_domain` and
  `tests.test_upload_claim_recovery` passed, 41 tests total; `git diff --check`
  passed.
