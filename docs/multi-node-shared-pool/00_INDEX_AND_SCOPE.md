# Shared Pool: Scope, Audit, and Phase Index

Date: 2026-11-11
Status: shared-pool release is on GitHub main and deployed to the VPS; the current Windows launcher and Fleet auto-refresh improvements are local review work and have not been released.

## Objective

Add an opt-in shared compute pool to the standalone Render Bridge so several
independently authenticated workspaces can submit work to several user-owned
machines. The scheduler selects a compatible, online, free node slot uniformly
at random, records the assignment, and permits multiple different tasks to run
at the same time on different machines. Operators need a browser console for
pairing, node health, queue state, and task history, plus a clear local command
for starting an Agent.

## Audit of the RB35 proposal

The RB35 proposal in the Alchemy repository is directionally correct: current
`node_pull` is single-node, multiple health reporters are intentionally
ambiguous, task offers are same-workspace FIFO, all automatic work shares one
physical slot, and task/media access is SourceScope-bound. The implementation
must not widen NodeScope to every tenant or treat a heartbeat as an assignment.

The implementation plan narrows three items:

1. Preserve Source API v1 and the legacy single-node path. Introduce additive
   control and Node API v2 routes for shared-pool operations; use one dispatcher
   authority (`node_pull`) for the existing ledger.
2. The requested topology is many worker machines connected to one Bridge
   service, not multiple Bridge API replicas. Keep the existing SQLite WAL
   ledger for this release and serialize assignment/slot/start decisions with
   `BEGIN IMMEDIATE`. Require PostgreSQL before running multiple Bridge service
   instances or promising high availability. This narrows RB35's unconditional
   PostgreSQL recommendation.
3. Every cross-workspace task read or mutation must prove a current persisted
   assignment. Start grant and unknown execution pin the attempt to the original
   node; only an unstarted assignment with no active claim can be reassigned.

The current `create_app` store gate is capacity one and returns `store_busy` to
overlapping requests. Shared mode must provide bounded concurrent API
admission, while database transactions remain the source of assignment
serialization. This is necessary for multiple Agents' heartbeat and polling
traffic; it does not mean image execution runs in the API process.

## Boundaries

- This repository owns the Bridge control plane, scheduler, Node Agent and
  console. Do not modify Alchemy code or data paths.
- Workspace task history remains tenant-scoped. The operator console exposes
  allowlisted metadata, not prompts, input bytes, credentials, or private media.
- A workspace explicitly joins a pool; a node operator explicitly pairs with a
  pool after being told that authorized pool jobs can send prompts/references to
  that machine. Pool membership does not share upstream account credentials.
- Keep existing `/bridge/v1` and `/bridge/node/v1` behavior compatible.
- Do not enable Hatchet alongside `node_pull` for the same task ledger.
- Start grant, execution-unknown, artifact receipt, ACK, and same-attempt
  recovery semantics remain authoritative.
- First acceptance uses FakeRenderer. No paid provider calls or real private
  user media are needed for implementation acceptance.

## Phase documents

| Document | Responsibility |
|---|---|
| [01 Architecture and data](01_ARCHITECTURE_AND_DATA.md) | Components, ownership, tables, concurrency authority |
| [02 API and identity](02_API_AND_IDENTITY.md) | Versioned endpoints, credentials, consent and assignment scope |
| [03 Scheduler and recovery](03_SCHEDULER_AND_RECOVERY.md) | Fair task selection, random node slots, lease and unknown rules |
| [04 Console and Agent](04_CONSOLE_AND_AGENT.md) | Operator flows, history, startup and browser boundaries |
| [05 Security and migration](05_SECURITY_AND_MIGRATION.md) | Privacy, secret handling, additive schema and rollback |
| [06 Test and acceptance](06_TEST_AND_ACCEPTANCE.md) | Regression matrix and release evidence |
| [07 Release and rollback](07_RELEASE_AND_ROLLBACK.md) | GitHub and existing-service replacement procedure |
| [08 Implementation audit](08_IMPLEMENTATION_AUDIT_AND_CORRECTION_MODEL.md) | Root-cause model, authority boundaries, and regression evidence |
| [09 Execution ledger and local status](09_NODE_EXECUTION_LEDGER_REWARDS_AND_LOCAL_STATUS.md) | Per-node execution accounting, Alchemy admin projection, and local node self-status |
| [10 Release readiness gap closure](10_RELEASE_READINESS_GAP_CLOSURE.md) | CI collection, running source revision visibility, release evidence, and explicit product limits |

## Release evidence boundary

The earlier 2026-11-11 VPS note below is historical evidence only. A later
read-only status observation is recorded in [10 Release readiness gap
closure](10_RELEASE_READINESS_GAP_CLOSURE.md); the public endpoint did not
expose an immutable build revision, so the currently running source commit
was not verifiable from that response. Do not treat an image tag or the old
snapshot as proof of the current deployed revision. The shared-pool topology
continues to mean many worker nodes on one SQLite-authoritative Bridge service,
not multiple Bridge replicas or high availability.

Historical 2026-11-11 snapshot: the Bridge image tag was reported as
`alchemy-render-bridge:shared-pool-capability-5c8ac7f`, with one shared pool,
one joined workspace, one online node, one free slot, no queued tasks, and no
active assignments. This point-in-time report had no standalone receipt and
does not establish current service health or real-image acceptance.

The current feature worktree adds a dedicated Windows shared-pool Agent
launcher, Fleet-only browser auto-refresh, and this release-status correction.
Those changes are not included in `origin/main` or the VPS image until separately
reviewed and released. Keep GitHub publication and replacing the VPS image as
distinct release steps; preserve the previous image and SQLite/artifact data.
