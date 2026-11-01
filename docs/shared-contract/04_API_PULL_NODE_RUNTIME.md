# API pull node runtime

## Scope

This mode places the Bridge Source API, its existing durable task ledger, and artifact storage on the VPS. A local Node Agent initiates outbound HTTPS requests to that Bridge and runs the configured executor/Codex CLI on the PC. Alchemy keeps its existing Source API contract.

## Ownership and flow

1. Alchemy submits one task to the Bridge Source API. The Bridge stores the task before returning acceptance.
2. The local Agent reports its heartbeat and enabled execution profiles, then reads `GET /bridge/node/v1/tasks/next` over outbound HTTPS.
3. The offer is read-only. The Agent claims the offered task through the existing atomic claim endpoint, then uses the existing start latch, input download, upload, commit, and receipt-ack flow.
4. The VPS is the only task/artifact ledger. The PC journal remains local evidence for same-task recovery; it is not a dispatch queue.

When no fresh available node advertises the requested profile, capability admission is closed and new creation is rejected with `node_unavailable` before acceptance. Alchemy may use its existing API fallback only for this explicit pre-admission result. A lost/uncertain create response, accepted task, start grant, unknown execution, or existing artifact never authorizes a new API generation.

## Runtime mode

`dispatch_mode` is `hatchet` by default and preserves the existing dispatcher/outbox behavior. Set it to `node_pull` on the VPS to omit the Hatchet dispatch route and stop creating outbox entries for new tasks. In that mode the runtime needs distinct Source and Node tokens; the local pull worker needs the Node token and Bridge HTTPS origin. Keep the VPS Bridge API private behind its existing HTTPS ingress. Do not expose the local PC or configure an SSH reverse tunnel.

Start the local Agent with `python -m render_bridge.agent.hatchet_sidecar --url https://bridge.example pull-worker --state-dir <local-state> --config <executor-config>`. The command does not import or require Hatchet. It retries outbound polling and heartbeat after connectivity returns. It first reconciles the existing local journal; it never creates a replacement task for uncertain execution.

The local Agent and VPS runtime must use matching source/workspace/node/executor identity. Use a fresh VPS Bridge data directory for a deliberate migration; do not copy or merge the local test ledger. Existing local unresolved attempts remain bound to their original local endpoint and ledger until reconciled.

## Cutover boundary

While any task is unresolved, keep its original Bridge endpoint, ledger, and enabled execution profile available. Removing a profile or changing the endpoint does not preserve the ability to replay that task. `node_pull` does not consume or acknowledge rows in an old Hatchet `dispatch_outbox`; changing `dispatch_mode` against the same ledger is not an automatic migration. The task table and old outbox may then describe different dispatch histories, so do not run both modes against one ledger as a cutover shortcut. Use a fresh VPS data directory for the new deployment; finish old tasks against the old endpoint/ledger and never recreate them in the new ledger. This runtime remains one node with serial local execution; it adds no multi-node lease-wait scheduler.

## Acceptance boundary

Test with a fresh isolated ledger and a fake executor first: offline admission, online task offer, claim/start, artifact transfer, receipt acknowledgement, disconnect/reconnect, and process restart. Existing Hatchet mode regression tests must remain unchanged. This document does not authorize VPS deployment, secret provisioning, or paid image generation.
