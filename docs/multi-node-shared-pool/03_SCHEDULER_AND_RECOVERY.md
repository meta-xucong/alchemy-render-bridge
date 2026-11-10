# Scheduler, Slot, and Recovery Contract

## Eligibility

A node slot can receive a task only when all checks pass in the scheduler transaction:

- pool, node and workspace membership are active;
- the node is not draining/revoked, heartbeat is fresh, and profile health is verified available;
- profile and profile revision match the frozen task;
- the slot is configured and has no active assignment;
- task is queued, not canceled/expired, within queue limits, and its workspace opted into that pool;
- a persisted task/pool admission link proves the task was created after workspace consent for this exact pool;
- the existing per-ledger queue limit is not exceeded.

First-release capacity is exactly one physical slot per node. It is not configurable in the UI or API. Manual-import assignments release that physical slot after preparation. Never infer concurrency from heartbeat state alone. The built-in `fake-image` executor is treated as available from its heartbeat; every real profile requires a fresh, validated available profile-health report.

## Selection policy

Task choice and node choice are separate decisions:

1. For each workspace, find its oldest currently runnable task by created,id: a task is runnable only when at least one pool recorded at admission has a compatible, healthy, fresh, free node slot. A blocked profile or full slot at the front does not hide a later task that can run. Apply the persisted round-robin cursor across workspaces that have a runnable task.
2. Enumerate all eligible free slots for that task's profile only in pools recorded for that task at admission and still joined by the workspace.
3. Select uniformly from those free node slots using `uniform-slot-v1`. Each eligible node has one slot and therefore equal probability.
4. Persist task, attempt, pool, node, executor, slot, assignment lease and policy version before returning an offer.

Random selection occurs once per assignment, not on every GET. Cache free-slot checks by profile and exact admitted-pool set while scanning one scheduler transaction. Inject the RNG in tests; production uses a cryptographically strong standard-library source. Record the chosen identity and policy version, not the random seed.

If a different node triggered the scheduler poll, it receives no task; the chosen node receives the persisted assignment on its next poll. Concurrent polls serialize through SQLite's write transaction, so one task cannot receive two active assignments.
The poll response is returned only when the complete `(pool_id, node_id, executor_instance)` identity matches the selected persisted assignment; duplicate node names across pools do not alias.

## State transitions

    queued -> assigned -> claimed -> running -> artifacts_ready -> ACK recorded
                    |          |       |
                    |          |       +-> execution_unknown (slot remains occupied)
                    |          +-> lease expired before start -> queued
                    +-> lease expired with no claim/start evidence -> queued

An assignment is never a new task attempt. It refers to the task's existing attempt ID. Reassignment before start is allowed only after the assignment lease expired and no live claim or start grant exists. Releasing the assignment frees its slot and appends a metadata event. Source ACK remains its existing receipt record; it does not add a task state.

## Start and unknown invariants

The start transaction verifies the exact assignment and claim, reserves its exact node slot, writes the existing attempt's start grant and original node/executor, marks assignment running and task running, then commits. A repeated start returns the same grant with may_execute=false; it never grants a second execution.

Once a start grant exists, the assignment is pinned to the original node/executor. If the response, provider stream, process or upload becomes uncertain, mark the attempt execution_unknown; keep its slot occupied and do not assign the task to another machine. Reconcile only the same attempt and original node. Release the slot only after a durable artifact receipt or explicit terminal reconciliation proves the execution ended. Cancellation requests do not prove that the provider stopped.

Uploads and ACK are same-attempt and idempotent. If output already exists, recover or commit that output; never run a replacement render to fill a slot.

## Queue and overload

Keep the configured bounded queue limit per workspace in shared mode. Scheduler order is FIFO among currently runnable tasks within each workspace; a task with no available compatible profile/slot may wait while a later task with an available slot proceeds. Busy nodes are eligible only when a free slot exists; a pool whose slots are full may still accept queued work up to that workspace's limit. When no healthy compatible node exists, preserve the pre-admission `node_unavailable` result so existing Alchemy fallback rules remain unchanged. A workspace that has left or never joined cannot silently fall back to the legacy node API. A queued job is never silently migrated to another provider.

The API store-call gate must be finite and configurable for shared mode. A full gate returns store_busy before unbounded thread growth. SQLite transaction lock waits remain bounded; tests cover simultaneous heartbeat, poll, claim and start requests.

\n