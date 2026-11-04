# Canceled artifact recovery correction

## Correction model

Expected behavior: an `artifact_ready` journal record is retried only while the
authoritative Bridge task can still accept the same artifact. If Bridge reports
the task as `canceled`, `failed`, or `expired`, that task is terminal and must
not remain in the Agent's pending-artifact scan.

Observed mismatch: `reconcile_pending_artifacts()` selects every local
`artifact_ready` record. `NodeAgent._execute()` returns `not_executable` for a
terminal remote task, but leaves the local record as `artifact_ready`. The next
pull-worker pass selects it again, repeatedly entering and leaving the active
execution state. This can make an idle worker appear busy and repeats requests
for work the server has already canceled.

## Authority and bounded correction

Bridge's remote terminal state is authoritative for upload eligibility. For a
matching local `artifact_ready` attempt, copy that exact terminal state into
the local journal before returning `not_executable`. Keep the saved artifact
and its metadata for existing retention/review rules; do not upload or delete
it. Do not apply this transition to `cancel_requested`, `execution_unknown`,
or other nonterminal states.

## Regression and acceptance

The regression must show that a canceled remote task with a saved local
artifact is returned once as `not_executable`, becomes absent from the next
pending-artifact scan, and preserves its local artifact without attempting an
upload. Existing recovery and unknown-execution tests must continue to pass.
