# Blueprint coverage correction and bounded delivery scope

Audit baseline: Bridge 1159150; Alchemy 60b2764e. Earlier stage tests establish
only their stated slices, not full blueprint delivery. No real account, provider,
Discord channel, deployment or Windows execution is verified by this audit.

## Delivery gaps in priority order

1. Static capabilities do not establish live node availability. Add scoped node
   heartbeat, freshness, profile readiness, and truthful unknown/offline states.
2. Agent preflight failures currently return only to the engine callback and leave
   source tasks queued. Persist a bounded attention reason, distinguishing pre-start
   waiting from post-start execution uncertainty. Preserve the legacy field and
   add structured detail for compatible clients.
3. Ordinary repeated use exhausts storage: only harness-only unpinned input cleanup
   exists, no acknowledged output retention or runtime maintenance. A separate
   slice must add bounded reclamation without deleting unknown/unconfirmed copies.
4. The v1.2 Midjourney blueprint explicitly calls for official Discord task-message
   delivery. Local export/import exists, notification does not. A separate opt-in
   mock-tested slice must preserve manual generation and ambiguous-send evidence.
5. A stop-new-admissions mode is missing. Add draining configuration while keeping
   existing task lookup, upload, acknowledgment and same-attempt recovery available.
6. Alchemy enablement and availability belong in the existing administrator
   configuration screen, not another disconnected preview page.

## This implementation slice

Single trusted node, existing bearer scopes, additive capabilities and task detail;
90-second heartbeat freshness, 30-second worker reports, bounded report bodies;
static accepting_new_tasks configuration (default true); documented explicit
same-attempt run-task recovery. No second dispatcher or automatic retries.
Readiness means a fresh configured worker report, not measured login, real image
capability, or a proof that Hatchet can schedule work. Codex remains attention until
separate local acceptance; all login/generation verified fields remain false.

## Remaining design, recommendation and acceptance classification

- PostgreSQL is explicitly recommended in blueprint section 2, not mandatory for
  the initial one-trusted-workspace/node scope. SQLite remains a bounded development
  store; the documented production migration is not completed.
- Static source/workspace/node/executor token binding provides configured
  provisioning for that initial trusted node. Self-service pairing and a durable
  node-management registry are intended later lifecycle functions, not necessary
  to run this single-node workflow. Multi-node expansion is not this deliverable.
- Immediate hot revocation is not implemented. The bounded operator procedure is
  replace/remove the configured node credential and restart the Bridge service;
  the old bearer is denied thereafter. This is not online revocation, but no new
  registry framework is required for a trusted-node development handoff. Keep
  source credentials available for task/receipt reconciliation when rotating node
  access. Never reset existing execution facts during credential rotation.
- An audit-event table is listed in section 4 and remains absent. Existing task,
  receipt and acknowledgment records establish outcomes, not full event history.
  Full event auditing is an explicitly deferred operational feature.
- Full streaming and execution lease renewal remain partial; bounded byte buffers,
  fixed leases and upload-only recovery are the present implementations.
- Windows real-tool acceptance, remote TLS/failure recovery and dual-VPS joint
  acceptance remain unverified. They cannot be established by offline unit tests.
- Structured Codex image controls and a built-in tool allowlist are absent in the
  pinned upstream schema, not capabilities this adapter can invent. ComfyUI/video
  remain explicitly deferred.
- Bounded retention and Discord work are separate practical delivery slices and
  must be verified before being reported complete.

## Acceptance / stopping condition

Tests must demonstrate fresh/stale/absent node reporting, profile mismatch, task
attention before/after start, compatibility of legacy fields, draining with replay
and recovery, unchanged once-only execution and default fake flow. Integrate the
administrator view against those contracts. Stop after scoped tests/review, list
real-tool/deployment gaps honestly; do not chase speculative edge hardening.

## Verification of this readiness slice

149 offline unittest cases passed (141 baseline plus 8 new); focused 8 also passed
independent review. Compileall, unchanged contract export and git diff --check
passed. No real account/tool, actual message, persistent credential creation,
Windows execution or deployment was performed. The test run emitted only existing
Starlette TestClient deprecation warnings (including per-request timeout warning).
