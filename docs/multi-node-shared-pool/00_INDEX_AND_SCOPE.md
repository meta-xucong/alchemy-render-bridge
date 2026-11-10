# Shared Pool: Scope, Audit, and Phase Index

Date: 2026-11-11
Status: isolated implementation and local acceptance complete; GitHub integration and live-runtime replacement remain release phases.

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

## Release evidence boundary

This branch starts from Render Bridge GitHub `origin/main` at
`6a7188816588c71878628438af03a313e77c7602`. Local implementation acceptance,
GitHub publication, and replacing a running VPS image are separate outcomes.
The deployment target and live release must be read-only verified before any
replacement is claimed. Preserve the previous image and SQLite/artifact data.

Local acceptance evidence for this branch:

- Full Windows test suite: 348 passed, 1 skipped; the skipped case is the
  explicitly gated real-provider path. Shared-pool acceptance uses temporary
  SQLite ledgers and FakeRenderer only.
- `compileall` and `git diff --check` pass. The only diff-check messages are
  Git's line-ending notices for existing Windows worktree files.
- Independent code and release-scope reviews found no remaining blockers after
  corrections for task provenance, pool identity, runnable queue heads,
  tenant-safe summaries, bounded history inputs, and isolated cutover checks.
- No GitHub commit/merge or live VPS replacement is claimed until publication
  checks pass and the exact target runtime is read-only verified.
