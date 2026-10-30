# Hatchet task isolation by Bridge endpoint

## Failure model

Each configured Bridge runtime owns one source/workspace and one durable task
ledger. The Hatchet worker callback only receives `task_id` and uses the Bridge
URL configured when that worker started. If two Bridge endpoints share one
Hatchet task name, a worker for endpoint A can receive endpoint B's run, query
ledger A, and receive `404 not_found`. Hatchet may retry that run, but retries
cannot correct the endpoint mismatch.

The local incident had V1/V3/LAB on `127.0.0.1:8797` and V2 on
`127.0.0.1:8798`. Both dispatchers used the fixed task name
`bridge-node-execute-v1`. The 8797 worker log shows the V2 Hatchet run reaching
its callback, then failing against the 8797 ledger. A later retry received
`503 store_busy`; this is the API's single-operation semaphore response and
does not establish database corruption.

## Minimal correction

Derive the Hatchet task name from the normalized Bridge endpoint. Both
dispatcher and worker call the same helper, so the same endpoint uses one name
and different endpoint URLs cannot share a name.

No task payload, Source/Node API contract, queue, attempt, or ledger is changed.
For deployments with multiple source/workspace scopes behind one endpoint,
keep one source/workspace per configured runtime as required by `RuntimeConfig`.

## Existing Hatchet runs

This correction routes future submissions. A run already submitted under the
legacy fixed name keeps that name and is not migrated. The low-level submitter
can create a separate Hatchet run for the exact Bridge task by reusing its
existing `task_id` and `dispatch_key`, but it does not check recovery safety.
An operator must first verify that the previous Hatchet run is terminal and the
Bridge task is still queued with no claim or start grant, then submit exactly
once to the endpoint-scoped task name. Keep the durable outbox acknowledgement
and its original `engine_run_id`; do not create another Bridge task or change
task/attempt identity. Repeated manual submissions can create multiple Hatchet
runs; Bridge's start latch remains the physical execution guard. This recovery
is not automated. It was live-verified locally once for the V2 incident: the
original task, attempt, and dispatch key were reused, and the resulting image
was delivered with an accepted receipt.

Do not change workers while legacy-name runs are still active across endpoints:
they may still be consumed by any worker registered to that shared name. First
resolve or reconcile those runs; endpoint scoping applies to subsequent runs.

## Regression

- URL trailing-slash normalization yields the same task name.
- Different Bridge endpoint URLs yield different task names.
- Dispatcher submission and worker registration receive the same derived name.
- Existing Agent identity, start latch, retry and receipt tests remain passing.
