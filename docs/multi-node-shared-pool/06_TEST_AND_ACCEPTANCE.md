# Test and Acceptance Plan

## Unit tests

- Migration creates additive pool tables on a fresh database and upgrades an existing v1 ledger without changing legacy rows/column layouts.
- Migration does not backfill legacy tasks into `shared_task_pools`; a pre-consent queued task remains outside shared history and scheduling after the workspace joins.
- Invite codes are one-use, expire, are bound to pool/kind, and store only hashes.
- Pool membership requires explicit SourceScope acceptance; revoke blocks future scheduling.
- Node bearer resolves only its node/executor; every v2 task/media path rejects a wrong or stale assignment.
- Duplicate node/executor names in different pools cannot receive one another's assignment metadata.
- Deterministic RNG selects only eligible slots and produces auditable policy version.
- Workspace round-robin chooses the next workspace with runnable work; within a workspace, creation order applies among tasks that currently have a compatible free slot, while an unavailable profile head does not block a later runnable task.
- A workspace with a full 10,000-task unavailable queue cannot hide a later runnable workspace from the scheduler.
- Slot reserve/release is atomic; a node never exceeds configured capacity.
- Start replay returns the same grant; start-after-expiry or start on another node fails.
- Unknown remains pinned and consumes capacity; only same-attempt terminal receipt/reconciliation frees it.
- Operator history never includes prompt, input names, media bytes or credentials.

## API and concurrency tests

Use temporary SQLite databases, FakeRenderer and independent node identities. No real provider or user media.

1. Three opted-in workspaces and three one-slot nodes share a pool.
2. Queue at least three eligible tasks, trigger concurrent polls, and assert three distinct persisted assignments, no duplicate task assignment, and random choice within the eligible set.
3. Execute all three FakeRenderer jobs concurrently; each slot has at most one active task.
4. Simultaneously send heartbeat, next, claim, start, upload and history requests; bounded admission does not turn ordinary three-node traffic into repeated store_busy.
5. A workspace not joined to a pool cannot see another workspace's tasks; an unassigned node cannot read a task, prompt, reference or upload route even with a valid node token.
6. Two concurrent claims/starts for the same task issue one start grant. A lost start response and a lost upload/commit response do not duplicate rendering or receipt.
7. Expired assignment before start can be reassigned only after the old claim expires and no start evidence exists.
8. Start grant, timeout, cancellation request or lost provider stream leaves task pinned/unknown; no other node starts it.
9. Drain/revoke/member removal stop new work without hiding or releasing active/unknown attempts.
10. Cursor history pagination is stable, bounded and tenant-scoped.

## Console acceptance

- Fresh operator can create a pool, create a node invite, pair a FakeRenderer node and see heartbeat/slots.
- Workspace token sees only its own history and can explicitly accept/revoke pool membership.
- Console displays queue, assignment and unknown truth from API; no empty state box or false success percentage.
- Text content is escaped; no third-party script; token not persisted in local storage or URL.
- The Windows first-run command prepares the isolated environment before invoking shared-pool enrollment; the later batch launcher starts the shared-pool `pool_node run` entry point and leaves the legacy V1/Hatchet launcher untouched.
- Operator summary and node list auto-refresh every five seconds only while the operator page is visible, never overlap, and do not auto-load task history. Manual refresh remains available; workspace view is not polled.
- The browser lifecycle harness exercises hidden/visible transitions, a delayed in-flight refresh plus manual click, session reconnection with a new bearer, and the workspace view's separate history load.
- The UI does not pretend it can launch a process on a remote machine.

## Release gate

Required evidence: exact source base/head commit, migration result, relevant existing regression suite, new unit/API/concurrency/UI checks, audit result, skipped real-provider tests, and deployment target/version evidence. A FakeRenderer pass proves scheduling and API behavior only, not real Codex login or production image quality. Keep production admission closed during acceptance: first restore the database backup/artifacts/config into a loopback-only copy with admission disabled and verify integrity, schema version, health, a known task/receipt and artifact read; then run three-node FakeRenderer against a separate empty synthetic ledger/config with admission enabled. Production opens admission only after both candidate checks pass and the live config has been explicitly changed and re-read.
