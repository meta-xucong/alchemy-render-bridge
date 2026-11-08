# Native Harness P1b: opt-in typed Bridge extension

This extends the actual `BridgeStore` and its existing FastAPI application. It is
not the P1a fake ledger and not a second dispatcher. No WorkBuddy or other real
model adapter is installed or executed by this change. Only explicitly configured
`synthetic-*` / `fake-*` model bindings are enabled at this stage.

## Scope and durable authority

- Baseline: `cf1e72ec3830854fa0983eb9acb92fad62a604c8`.
- `tasks`, `attempts` and `claims` remain the sole execution authority. Typed
  `harness_tasks` metadata references an existing task. Every admission, claim,
  start latch, cancellation, observation and result transaction uses the same DB.
- P1b requires a dedicated Harness instance, ledger and execution resource. Image
  heartbeats and image creation are rejected in Harness mode. Startup refuses a
  ledger containing any image task (including terminal tasks) or image heartbeat;
  a pinned Harness ledger cannot be reopened in image mode. Ordinary unconfigured
  image instances retain their existing behavior. Independent image/Harness runners
  cannot safely share the untyped next-task endpoint, so mixed operation is NOT
  supported. Separate instances do not coordinate a shared physical machine/tool;
  do not point both at the same execution resource. Each Harness ledger retains
  the existing single physical slot and task/attempt/claim/start authority.
- Typed readiness uses only its own heartbeat timestamp and configured binding
  intersection; it cannot refresh image capabilities or revive removed bindings.
- A new `harness_queued` scheduling state avoids feeding the historical image
  Hatchet outbox. Harness mode is accepted only with `dispatch_mode=node_pull`.
- `outcome` and `liveness` are separate. A terminal success/failure with unknown
  liveness still occupies the physical slot. `harness_terminal` only releases a
  terminal stopped execution, or an atomically cancelled never-started task.
- Post-start claim reassignment is forbidden, including `upload_only`. The
  original configured node/consumer, exact claim and immutable grant can submit
  the same result after lease expiry. Lease expiry never grants a second launch.
- Node result and observation routes reject stale/foreign identities. Observation
  cannot claim `stopped_confirmed` or `not_started`; it never releases a slot.
- Frozen result retries are idempotent. A conflicting terminal payload is rejected.
  Native completion, output types, actual selected/reported model and unknown
  usage are validated. Empty/unsupported output is not promoted to success.

## Explicit configuration

The Python entry point is:

```python
from render_bridge.domain import BridgeStore
from render_bridge.harness import HarnessBinding, HarnessConfig

binding = HarnessBinding(binding_id="fake-basic", revision=1,
    model_alias="synthetic-basic", native_model="fake-basic")
config = HarnessConfig(
    source_id="fixture-source", workspace_id="fixture-workspace",
    ledger_instance="fixture-ledger", node_id="fixture-node",
    executor_instance="fixture-executor",
    allowed_owners=(("tenant-1", "key-1"),), bindings=(binding,),
    accepting_new=True,
)
# Provide a NEW isolated DB/artifact directory, never production state.
store = BridgeStore(db_path, artifact_root, dispatch_mode="node_pull", harness=config)
```

The normal runtime accepts an optional `harness` block. Its source/workspace/node/
executor are inherited from the existing runtime configuration, not independently
supplied by untrusted task input. `harness=None` remains the default. In the
nested block configure `ledger_instance`, `allowed_owners`, `bindings` and
`accepting_new` (false by default). Service/Node credentials use existing distinct
runtime environment variables. No credentials are included in examples.

At initialization the tuple `(ledger_instance, source, workspace, node, executor)`
is transactionally pinned. A mismatched later configuration fails closed. This
is NOT proof against restoring a whole old database snapshot; disaster recovery
still requires stopping new admission and reconciling node execution evidence.

After start, the native Node must report an explicit authenticated heartbeat:

```http
POST /bridge/node/v1/harness-heartbeat
Authorization: Bearer <node-service-credential>
Content-Type: application/json

{"state":"available","binding_digests":["<sha256-of-canonical-binding>"]}
```

Canonical JSON uses sorted keys, compact separators, UTF-8 and no nonfinite
numbers. Heartbeat freshness is 90 seconds; it conveys readiness, not a start
permit. Node presence uses the existing node registry/heartbeat, with a bounded
extra capability-fingerprint record. It is not a queue.

## HTTP integration

The native Facade/Node clients implement `harness.invoke.v1`:

- Existing source `POST /bridge/v1/tasks` accepts a strict discriminated Harness
  envelope in addition to the unchanged strict image DTO.
- Existing source by-origin/task GET paths return typed Harness tasks. Source
  credentials bind source/workspace; identities are checked against configured
  tenant/principal allowlists. The Facade remains responsible for authenticating
  and reconstructing its per-user context from the gateway.
- `GET /bridge/v1/capabilities` advertises `harness_contract`, `ledger_instance`
  and separate diagnostic `harness_admission`. The original `admission` field is
  still the image-admission diagnostic; neither diagnostic is an execution permit.
- Existing node next/task/claim/start paths use the same shared task authority.
- New `POST .../tasks/{task_id}/harness-observation` only records pending activity.
- New `POST .../tasks/{task_id}/harness-result` persists the immutable typed result
  and returns task/claim/grant/result identity for the Node's ACK verification.
- New `POST /bridge/node/v1/harness-heartbeat` reports bounded binding fingerprints.
- Generic image upload, unknown/attention, withdrawal and manual-result paths are
  not substitutes for typed Harness receipt semantics.

A source idempotent create looks up an existing operation before current binding,
node freshness or new-task admission. Body/binding conflicts return 409. Removing
a binding or setting `accepting_new=False` does not destroy accepted result
replay. Removing an owner from the current allowlist denies its reads as an
explicit authorization revocation. Keep the original `HarnessConfig` scope to
finish existing tasks; removing the entire config disables the Harness interface.

New Harness pre-admission capacity/offline/draining failures explicitly include
`X-Native-Accepted: false`. Missing/ambiguous transport responses are never proof
of nonacceptance. Existing generic error paths may leave acceptance unknown;
clients must retain their original operation and ledger identity.

## Time bounds and retention

- Default unstarted admission deadline: 3600 seconds, configurable 1..86400.
  A deadline can expire only a task with no start grant. Same-key reads remain the
  same expired task; no new generation occurs. Its result error code is
  `TASK_EXPIRED`, distinct from completed-result body retention expiry.
- A conservative default maximum of 10000 Harness identity records is enforced.
  No automatic deletion of operation identity/tombstones is provided.
- Individual result max 256 KiB; default aggregate result quota 16 MiB. A full
  result reservation is required before new admission. Resource exhaustion fails
  closed rather than starting first and recording later.
- Explicit `expire_harness_results(scope, retention_seconds=..., batch_size=...)`
  removes only terminal, stopped result bodies and keeps identity/digest. Pending
  or unknown executions are never expired. Expired reads carry `result_expired`.
  This maintenance API is not exposed as an unauthenticated HTTP endpoint.
- There is no Node ACK-of-ACK retention protocol yet. If a Node loses its original
  commit response until after result retention, it may remain quarantined rather
  than treating expiry as permission to run again. Retention must exceed the
  operational reconciliation window; full operational recovery remains unverified.

## Safety limits and unverified scope

A synthetic node's `stopped_confirmed` result is trusted only within this isolated
single-node experiment. No real supervisor/OS/child-process fencing proof or
admin force-release API is implemented. Unknown terminal liveness stays isolated;
there is no unsafe endpoint to turn it into a free slot. WorkBuddy/real tools,
late asynchronous native children, commercial permission, multi-user OS isolation,
production TLS deployment and billing are NOT verified by these tests.

Use a separate instance/data directory. Do not point this configuration at an
existing production ledger. No deployment, merge or paid inference is authorized
or performed by implementing this extension.

## Verification commands

Run against this checkout's `src`, never another Bridge checkout's implementation:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_harness.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Results, skips and exact counts must come from the current run, not historical
README milestones. The native project separately owns socket-based cross-repo
Facade/Bridge/Node tests. Sub2API and real Harness acceptance are separate stages.

### Ledger mode fence (audit correction)

Every constructor pins `image` or `harness`, even for an empty ledger, under
`BEGIN IMMEDIATE` alongside Harness identity validation. Competing constructors
cannot leave live objects with different modes. There is no supported mode
switch or marker-reset endpoint. Stop all older-version processes before upgrading
or migrating a ledger: this is not a fence against already-running old binaries
that do not implement the marker. Use separate state directories and execution
resources for separate instances.

### Bounded HTTP store admission

Dedicated Harness instances admit at most two concurrent store calls: one runs
on the existing single worker limiter, and one can wait. This accommodates an
ordinary Facade read overlapping a Node start. Further concurrent calls receive
503 `store_busy`; this is not an execution/task queue or a start retry. Image
instances retain their original single-call gate. Cancellation before worker
startup marks the waiting invocation canceled, releasing only its admission
permit and preventing delayed execution; an already-started transaction retains
its permit until completion. This does not eliminate uncertainty from network
loss or higher contention, and no start is automatically retried.

Harness HTTP serving uses the existing asyncio/Uvicorn runtime. A shielded asyncio
worker task retains the single AnyIO limiter across cancellation of an already
running HTTP call; otherwise raw asyncio cancellation can release the limiter
before the synchronous transaction exits. A canceled not-yet-started worker is
marked canceled under the state lock and its waiting task is canceled, so no
unbounded collection of detached waiters is created. Trio serving is not part of
this opt-in Harness acceptance scope. The image HTTP path is unchanged.
