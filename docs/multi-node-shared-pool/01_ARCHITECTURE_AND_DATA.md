# Architecture and Data Model

## Supported topology

One authoritative Render Bridge API process and one local SQLite WAL ledger serve multiple independent Node Agents over HTTPS. Agents initiate outbound requests; node machines do not expose inbound ports. The Bridge owns task dispatch state and private artifacts. Alchemy remains the source of frozen rendering requests and the authority for review, billing and delivery.

This release does not promise multiple Bridge API replicas or active-active failover. In that future topology SQLite must be replaced by a shared transactional database such as PostgreSQL before adding replicas. Worker concurrency does not require database replicas.

## Component responsibilities

| Component | Responsibility |
|---|---|
| Source API v1 | Existing tenant-scoped create/query/cancel/input/receipt contract; unchanged wire behavior, with source credentials resolved dynamically in opt-in shared mode |
| Pool control API v2 | Pool/invite/source-credential/node pairing, fleet status, metadata history, drain/revoke/reactivate |
| Source API v2 | Explicit workspace consent, pool leave and workspace-scoped history |
| Node API v2 | Per-node credential, heartbeat, assignment-scoped claim/start/input/upload/commit |
| Scheduler | Fair workspace selection, uniform random free-node selection, persisted assignment and slot release |
| Agent | Poll, claim, start, execute configured profile, recover only the same attempt and report configured health |
| Console | Same-origin static UI; calls authenticated control/source endpoints and never starts a remote OS process |
| SQLite ledger | Existing task tables plus additive pools, membership, credentials, status, assignments, cursor and metadata events |
| Artifact store | Existing private, server-named files; no media bytes in scheduler messages or audit events |

Hatchet and node_pull remain mutually exclusive for one ledger. Shared-pool mode requires node_pull and does not install legacy static Node/dispatcher credentials.

## Additive schema

Additive schema migrations create these tables and indexes through version 4. Version 4 adds the shared-mode queue-order index `tasks(source,workspace,state,created,id)`; it does not alter row layouts. The implementation deliberately leaves the positional layouts of `tasks`, `attempts`, `claims`, `node_registry`, `node_heartbeats`, `inputs`, `uploads` and `receipts` unchanged. Existing tasks are not backfilled into shared-pool eligibility; a task is admitted only when created after explicit workspace consent:

- `shared_schema_version(version, applied)` records additive migration application.
- `shared_pools(pool_id, name, active, policy_version, created)` stores pool identity and the `uniform-slot-v1` policy.
- `shared_workspaces(pool_id, source, workspace, state, consented, joined, revoked)` records opt-in membership; the composite primary key prevents duplicate memberships.
- `shared_invites(code_hash, pool_id, kind, profiles, created, expires, consumed, consumed_by)` stores only the hash of a random one-use invitation.
- `shared_source_credentials(token_hash, source, workspace, created, revoked)` and `shared_node_credentials(token_hash, pool_id, node_id, executor, created, revoked)` store hashes, never bearer values.
- `shared_nodes(pool_id, node_id, executor, state, slots, allowed_profiles, created, revoked)` binds one local Agent identity to a pool. First release fixes `slots=1`.
- `shared_node_status(pool_id, node_id, executor, seen, state, profiles)` is the pool-specific heartbeat projection. Existing `profile_health` remains the authority for real profile readiness.
- `shared_assignments(assignment_id, task_id, attempt_id, pool_id, node_id, executor, slot, profile, state, assigned, lease_expires, started, released, release_reason, policy_version)` pins each existing task attempt to one node.
- `shared_task_pools(task_id, pool_id, admitted)` records which opted-in pools may execute each newly created task; the scheduler and shared history use this persisted provenance.
- `shared_scheduler_cursor(singleton, source, workspace)` persists round-robin workspace selection.
- `shared_pool_events(id, pool_id, occurred, kind, source, workspace, node_id, task_id, assignment_id, reason)` stores bounded metadata-only history.

Unique partial indexes enforce one active assignment per task and one slot-holding assignment per node. Completed assignment rows remain for reconciliation/history. An `awaiting_user` assignment keeps its task identity but releases the physical node slot. No second queue or replacement task is created. Node authorization compares the complete pool/node/executor identity tuple, including when node names repeat across pools.

## Transaction and concurrency authority

SQLite `BEGIN IMMEDIATE` serializes assignment, claim, start-grant, upload/receipt and release decisions in the single Bridge process. The HTTP adapter admits a bounded number of store operations concurrently; SQLite remains the serialization point. Shared mode requires a finite configured gate (the example uses 32); saturation returns retryable `store_busy` rather than building an unbounded thread queue.

Use local persistent disk, WAL, foreign keys and a bounded busy timeout. Do not use a network-mounted SQLite file or start multiple writers against one data directory. Run the additive migration before admitting work.

## Compatibility

Source API v1 and the legacy single-node runtime remain unchanged when `shared_pool_enabled=false`. Shared mode requires explicit workspace membership and only installs the dynamic v2 node/control paths. The existing task, attempt, receipt and ACK records remain authoritative; assignments augment dispatch ownership without creating a new task or attempt. Existing v1 task/receipt routes remain available for a workspace's dynamic source token.
