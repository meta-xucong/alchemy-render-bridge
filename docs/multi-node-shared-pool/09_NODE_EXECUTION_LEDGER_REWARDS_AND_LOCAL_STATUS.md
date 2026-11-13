# Node Execution Ledger, Reward Metrics, and Local Status UI

## Objective and boundary

Record each node's actual image execution outcome, expose durable success/failure/total counts to the Alchemy administrator, and let a node provider open a local page that identifies and monitors that exact node. This is status and accounting support only; it does not move money, alter generation routing, change visual review, or decide whether an image is acceptable.

Render Bridge remains the sole authority for assignment, start, receipt, and node identity. Alchemy reads a validated, read-only projection over HTTP. Neither service reads the other's database. The public fleet dashboard remains anonymous and must not expose reward counts, provider references, task history, or machine identity.

## Correction model

### Intended behavior

- Count a generation once when its attempt crosses the persisted Bridge start boundary after schema-v5 activation.
- Attribute it to the `pool_id/node_id/executor_instance` bound by that start grant.
- Let Bridge receipt commit define rendering success and a confirmed terminal-failure record define failure.
- Keep running, awaiting-user, execution-unknown, and canceled attempts visible as separate outcomes; never turn them into success or confirmed failure by inference.
- Show the node provider the status for the exact node authenticated by its local Node Agent token.
- Show Alchemy administrators a read-only, per-node projection for manual reward decisions.

### Existing authority and gap

`attempts.start_grant_id/original_node/original_executor`, `shared_assignments`, task state, receipts, and ACKs already contain the execution truth. Task/media retention can later remove task rows, while the existing public dashboard intentionally removes machine identity and per-task information. Recomputing long-term reward totals only from retained task rows therefore loses history. A second mutable counter cache would drift from the task ledger.

### Minimal complete correction

Add a compact, one-row-per-started-attempt accounting ledger in the Bridge SQLite transaction. It contains no prompt, image bytes, workspace identifier, or raw task ID. Update that row idempotently at authoritative start, receipt, terminal-failure, cancellation, and ACK transitions. Read live task state from the existing task ledger; read tracked totals from the compact execution ledger. Do not backfill pre-upgrade history: after retention, its completeness cannot be proven. Keep task scheduling, admission, provider calls, review, and delivery unchanged.

## Counting contract

All values are attempt counts, not image-file counts. An attempt is included in `total_started` exactly once after a start grant (or the corresponding manual-ready boundary). Queue offers, pre-start reassignments, expired-before-start tasks, unsupported profiles, and canceled-before-start tasks are excluded.

| Metric | Authority | Counting rule |
|---|---|---|
| `total_started` | compact node execution ledger | Unique started attempts attributed to this exact node identity since schema-v5 activation |
| `success` | Bridge receipt commit | One per started attempt whose output receipt is committed as `artifacts_ready` |
| `failed` | confirmed terminal-failure record | One per started attempt only after the assigned node's terminal failure is durably confirmed |
| `in_progress` | current task/assignment state | Started attempts currently running, uploading, or awaiting a manual result |
| `unknown` | current task/assignment state | Started attempts whose execution outcome is explicitly unknown |
| `canceled` | cancellation completion record | Started attempts authoritatively canceled after the start boundary |
| `delivery_ack` | Alchemy receipt ACK | Accepted, rejected, or pending; it is displayed separately and does not rewrite render success |

`total_started` is not presented as `success + failed`; open, unknown, and canceled outcomes remain visible. An output rejected by Alchemy after a valid Bridge receipt remains a render success with a rejected delivery ACK. Reward policy and payout are not encoded in this feature.

## Data model and migration

Add schema version 5 with additive tables only:

- `node_execution_ledger(attempt_key, pool_id, node_id, executor, profile, state, started_at, updated_at, completed_at, artifact_count, failure_code, delivery_ack, delivery_ack_at)`.
- `attempt_key` is a SHA-256 digest of the existing random attempt ID, unique and used only for idempotent updates; do not persist task IDs or request data in this ledger.
- The primary key prevents duplicate counts after repeated start/upload/ACK calls. State changes are performed in the same SQLite transaction as their authoritative task transition.
- Keep accounting rows after task/media retention. Their schema contains only node attribution, profile, timestamps, status, bounded reason code, output count, and ACK decision.
- Keep provider mapping in additive invite/node metadata tables. `provider_ref` is a short operator-assigned Alchemy user/account reference; it is not treated as identity proof and is never exposed publicly.

No legacy task, attempt, receipt, assignment, or public-status schema is rewritten. Legacy single-node mode and all existing API v1 routes remain unchanged.

## API and UI contract

### Node self-status

- Add `GET /bridge/node/v2/status`, authorized by the existing node bearer. Identity is derived from that credential, never from query or body fields.
- Return the authenticated node's ID, provider reference if assigned, heartbeat/readiness, current assignment state, tracked counts since schema-v5 activation, and a bounded recent execution list.
- Do not return another node, workspace IDs, prompts, input/output bytes, or credentials.
- The local Node Agent hosts a loopback-only read-only page at `http://127.0.0.1:<port>/`. The page calls a same-origin local endpoint; the Agent adds its node bearer server-side. The token never reaches browser JavaScript or the page URL.
- The local page refreshes every five seconds and shows current task state, elapsed time, latest outcomes, and tracked counters since schema-v5 activation. It is independent of the VPS public fleet page.
- Windows first-run uses a per-node, one-use enrollment package downloaded from the operator console. Place it beside the shared-pool batch launcher; double-click preflights the environment, consumes the package, verifies the saved node identity, and starts the Agent. Every machine needs its own short-lived package; the public launcher contains no reusable pool credential.

### Alchemy administrator metrics

- Add a read-only `GET /bridge/rewards/v1/nodes`, protected by a dedicated `BRIDGE_REWARD_READ_TOKEN`, distinct from node, source, dispatcher, and operator-control tokens.
- The endpoint returns node identity/provider reference, heartbeat, current execution state, tracking start time, and counts only. It has no control actions and no task detail. The response is bounded to 1,000 nodes and marks when more nodes exist.
- Alchemy's authenticated admin backend proxies this endpoint with `ALCHEMY_RENDER_BRIDGE_REWARD_READ_TOKEN`, stored only in the server environment. The Alchemy browser receives only the sanitized JSON projection.
- Add a per-node execution table and a manual refresh control to Alchemy's RenderBridge admin area, with a 15-second automatic refresh. Show the counting definitions and keep pending/unknown separate from failures.
- Configure `BRIDGE_REWARD_READ_TOKEN` at Bridge and the matching `ALCHEMY_RENDER_BRIDGE_REWARD_READ_TOKEN` at Alchemy as distinct server-side secrets. They are separate from the Bridge operator credential and the Alchemy generation credential. Both surfaces show the schema-v5 tracking start time so reward periods are visible.
- If the read-only credential is absent or the Bridge is unavailable, show “统计暂不可用” and preserve ordinary Alchemy operation.

The existing public `/bridge/console` remains a redacted liveness board. It is not the reward or provider-identity view.

## Provider attribution

The operator may attach `provider_ref` when creating a one-use node invitation, or edit the reference for an already enrolled node in the private operator console. Enrollment copies it to the node's private metadata. Alchemy's admin projection shows this reference beside the node and its counts. The value should be an internal account ID, not an email address or display name. A human administrator remains responsible for verifying ownership and applying any reward policy. This release does not create credits, balances, invoices, or payout records.

## Security and failure semantics

- The reward-read token grants only the list-metrics route; it cannot create invites, read task history, change admission, or control nodes.
- Validate credentials are distinct at Bridge startup. Use constant-time bearer comparison and HTTPS for remote use.
- The Alchemy proxy endpoint remains protected by Alchemy admin authorization. Never send the reward token to the browser.
- Node-local UI binds only to loopback, accepts only loopback Host values, uses same-origin read-only requests, `no-store`, and a restrictive CSP. If its port is unavailable, the Node Agent continues execution and reports the UI as unavailable.
- Failures, missing heartbeats, unknown execution, and rejected delivery ACKs remain distinct. No raw exception text is stored as a failure reason.
- Node/provider identity and reward metrics never appear on the unauthenticated fleet dashboard.

## Implementation and acceptance plan

1. Add schema-v5 accounting and provider-reference migrations; verify existing rows are preserved and no task is backfilled as a generation.
2. Update the ledger atomically at each authoritative start/outcome/receipt/ACK transition; verify retries and replay do not increment totals twice.
3. Add node-authenticated status and dedicated reward-read API routes; verify scope isolation and token-role separation.
4. Add the loopback Node Agent status page and the Alchemy admin read-only metrics table.
5. Exercise concurrent node assignment, multi-node totals, repeated start/commit/ACK, pre-start expiry/reassignment, success, terminal failure, unknown/recovery, cancellation, task/media retention, and delivery ACK acceptance/rejection against a disposable SQLite ledger.
6. Verify the unauthenticated fleet response remains within its existing allowlist contract; verify no prompt/media/task/workspace data or reward counts leak into it.
7. Run existing shared-pool and Alchemy admin checks; review API compatibility, no-token behavior, and the default legacy path.
8. Configure the two dedicated server-side environment variables and deploy Bridge before Alchemy. Read-only live acceptance requires the admin table to show node identity, a recent heartbeat, and totals consistent with Bridge history. Local UI acceptance requires the Node Agent to print a `http://127.0.0.1:<port>/` status URL and display only its own identity and status. The default port is `8766`; `--status-port 0` lets the OS choose an available port. Windows startup acceptance covers package validation, profile/environment preflight, one-use enrollment, token verification, and foreground start without invitation or consent prompts.

This document defines the implementation and acceptance target, not live deployment or payout acceptance. Production rollout and secret provisioning remain separate release actions.
