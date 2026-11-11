# Implementation Audit and Correction Model

## Intended behavior and observed gaps

Shared-pool tasks cross workspace and machine boundaries only for the persisted
assignment. A workspace leaving or withdrawing before execution must stop the
assigned node from reading its prompt or reference data. Once an automatic
execution start grant exists, the same attempt stays pinned to its original
node until a receipt or explicit terminal reconciliation. Source capabilities
must report tenant-local usage. Completed local copies must be reclaimable only
after a durable receipt acknowledgement and a configurable grace period.

The implementation audit found authorization gaps in pre-start reads, a
withdrawal path that left claims/assignments active, shared-mode capability
fields that could expose global queue/storage totals, a proxy body limit below
the actual profile output maximum, uncollected local prompt/reference data, a
manual profile without a pooled import path, incomplete console commands and
membership discovery, and lossy timestamp rounding in history cursors.

## Owning layers and authority

- Assignment identity and task attempt remain the execution authority.
- Workspace membership is an additional pre-start authorization gate; it does
  not revoke a started automatic attempt or permit reassignment after start.
- The Source API remains authoritative for a workspace's task and receipt.
  The operator console sees allowlisted metadata only.
- Global capacity enforcement remains global inside the write path. The
  workspace-facing capabilities projection is scoped to that workspace.
- Local Agent media is owned by the Agent state directory. Cleanup is allowed
  after source ACK (accepted or rejected), or after an authoritative terminal
  result proves no future execution; unknown/start-requested work is retained
  for same-attempt recovery.
- Legacy API v1 and legacy single-node runtime behavior remain unchanged when
  `shared_pool_enabled` is false.

## Minimal correction set

1. Release and delete claims for unstarted assignments in the same transaction
   that records workspace leave, source cancellation, or withdrawal.
2. Enforce active membership and nonterminal deadline state on every pre-start
   task/input read. Allow only a redacted, metadata-only reconciliation view for
   released or terminal assignments; never include request, prompt, or refs.
3. Keep started automatic attempts pinned. Treat manual preparation as a
   bounded `awaiting_user` workflow; its task deadline expires it instead of
   converting it into an unknown upstream execution.
4. Scope shared-mode queue and storage projections by exact SourceScope while
   retaining global atomic capacity checks.
5. Raise the example node-v2 proxy upload limit above the 16 MiB application
   maximum to leave transport headroom.
6. Run acknowledgement-aware Agent maintenance from the foreground pool Agent.
   After the configured grace period, remove registered inputs, outputs and
   manual exports and scrub request-bearing journal fields. Keep unresolved
   execution evidence for recovery.
7. Expose assignment-bound manual import and log only local file paths plus a
   request hash; require an explicit single-original confirmation.
8. Provide a complete node enrollment command and a workspace membership list
   so a returning workspace can leave without retaining the invitation.
9. Encode the exact stored float timestamp in history cursors rather than
   rounding it.

## Compatibility correction from independent scope review

The first implementation routed shared-pool cleanup through the existing
`maintain_agent()` entrypoint. That entrypoint is also exposed by the legacy
single-node CLI, so broadening its selected states and deleting request/manual
export fields silently changed the old runtime. This conflicts with the
contract above that `shared_pool_enabled=false` preserves legacy behavior.

Keep the authority split explicit: `maintain_agent()` retains its original
acknowledged-delivery cleanup contract for the legacy CLI. Shared-pool Agents
call a distinct `maintain_shared_pool_agent()` entrypoint for shared-pool
terminal/unstarted cleanup and request/manual-export scrubbing. Regression
tests must prove both paths independently. This avoids turning a shared-pool
retention requirement into an implicit change to existing single-node data
retention.

## Assignment identity and task-admission provenance

The independent code audit also found two projection/identity mismatches.
`next_shared_assignment()` compared only node and executor names when deciding
whether its poller should receive an assignment, although the persisted node
identity includes the pool. Separately, operator history used a workspace's
current or historical membership row as proof that every task in that
workspace had entered the shared pool. Membership alone cannot prove that a
task was submitted after the workspace consented, and legacy queued tasks must
not become eligible for pooled execution after a later join.

The correction model is:

1. Compare `(pool_id, node_id, executor_instance)` as one indivisible node
   identity before returning assignment metadata.
2. Persist the pools that a task was authorized to enter at the same
   transaction that creates it. Only those persisted task/pool links can make
   it scheduler-eligible or visible in shared-pool history. Existing tasks
   without such a link remain legacy work even if their workspace later joins.
3. Keep this provenance additive in a separate table; do not change legacy task
   table layouts or infer historical consent from timestamps or membership.

Regression coverage must use duplicate node names in two pools and seed a
pre-consent legacy queued task, then prove neither metadata nor execution is
projected into the shared pool for the wrong identity or task.

## Final audit projections, scheduler bound, and release evidence

The final independent audit found three remaining bounded defects and two
release-runbook gaps. The correction model is:

1. Operator queue totals count distinct tasks only when a persisted
   `shared_task_pools` admission links them to an active pool and the owning
   workspace still has active membership there. Current/historical membership
   alone is not evidence of shared admission; a task admitted to multiple pools
   is still one task in the global total. The joined-workspace total counts
   distinct active `(source, workspace)` identities, not membership rows.
2. Preserve FIFO among currently runnable tasks per workspace and round-robin
   among workspaces with a runnable task. Do not cap the global candidate scan
   at 10,000 tasks or materialize every tenant task in one scheduler poll.
   Rotate distinct eligible workspace keys after the persisted cursor, inspect
   one workspace queue at a time in created/id order, and choose the first task
   with a compatible, healthy, free node slot. An unavailable task must not
   hide a later runnable task. Cache slot checks by the task's exact admitted
   pool set and profile.
3. Bound history cursor and query filter lengths both at the HTTP boundary and
   in the store entry point, before base64 decoding or query construction.
4. Run FakeRenderer concurrency acceptance in a temporary candidate clone with
   `accepting_new_tasks=true`, synthetic workspaces/nodes, and a disposable
   ledger; keep the production data directory and admission gate untouched.
   Production admission has one explicit opening action: set
   `accepting_new_tasks=true` in the reviewed runtime config, restart/redeploy,
   and verify the live config and task admission status before connecting
   opted-in Source clients.
5. Make backup acceptance reproducible: stop the writer, create a SQLite
   online backup, run `PRAGMA integrity_check`, restore that backup to an
   isolated directory with the complete artifact tree and runtime/deployment
   configuration, start a candidate copy against only the restored data, then
   verify health, migration version, a known task/receipt read, and artifact
   availability. Preserve the Nginx site configuration, service unit, secret
   reference/environment-file path (never secret contents), image tag, and
   runtime config as part of the one rollback release record.

Add regression coverage for task summary provenance and distinct workspace
counts, queue-head scheduling past a full unavailable workspace queue,
skipping an incompatible head within one workspace, and oversized history
inputs. Release documentation must keep production admission closed until all
candidate-copy checks pass.

## Acceptance evidence

Each correction has a synthetic regression test. The HTTP acceptance suite uses
FakeRenderer and a temporary SQLite ledger; it does not call a real image
provider or use private user media. A final audit must verify no change to the
legacy v1 contract, no cross-workspace prompt/history projection, exact
assignment identity at every node mutation, and the release/rollback fence.

## Live capability projection follow-up (2026-11-11)

The first VPS acceptance pass showed that `/bridge/control/v2/nodes` and
`/bridge/source/v2/pools` reported the opted-in shared node online, while the
authenticated `/bridge/v1/capabilities` response still showed an old direct
node as offline and `admission.accepting_new_tasks=false`. Alchemy's existing
Bridge route policy checks that admission field before selecting Bridge, so the
projection mismatch blocked the intended client path even though the shared
scheduler had a healthy Codex slot.

The correction model is:

1. In shared-pool mode, the persisted shared-node heartbeat and shared profile
   health are authoritative for the Source workspace's node/admission view.
   The old direct-node projection remains authoritative outside shared-pool
   membership.
2. `BridgeStore.node_status` was defined on the concrete class and shadowed
   `SharedPoolMixin.node_status`; `capabilities()` therefore read the legacy
   `node_registry` for its admission decision while the shared routing-health
   projection already read the pool tables.
3. Delegate to the existing shared-pool projection only when shared-pool mode
   is enabled and the workspace has a pool membership. Preserve the existing
   legacy projection otherwise. Do not change task scheduling, task admission,
   image execution, quality review, or Alchemy business code.

Regression coverage verifies that a fresh Codex profile report produces an
`available` shared node and `admission=true`, and that the authenticated v1
capabilities HTTP route returns the same readiness for an opted-in workspace
without creating a task. The focused store and HTTP tests pass after the fix.

One exploratory run of the existing FakeRenderer task-flow test returned
`execution_unknown` after capability assertions were temporarily inserted into
that generation test. Those assertions were removed and the generation test
was not repeated; its outcome is recorded as an unresolved validation anomaly,
not a pass. No real provider or production task was invoked. The previously
recorded 348-passed/1-skipped suite and 24/24 synthetic concurrency run apply
to the parent commit, not this follow-up. Keep production admission closed
until the correction is integrated and the read-only VPS capability check
reports the shared node/admission consistently.
