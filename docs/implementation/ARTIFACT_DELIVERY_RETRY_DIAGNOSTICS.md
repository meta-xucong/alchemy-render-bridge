# Artifact delivery retry diagnostics

## Correction model

Expected behavior: after a renderer has durably produced an artifact, Bridge
retries only the same artifact for the same task and attempt until the existing
receipt flow succeeds. The Codex 600-second terminal policy applies to Codex
execution, not to an artifact that already exists.

Observed mismatch: during the 2026-11-04 VPS run, Codex produced a local image
and the journal reached `artifact_ready`, while Alchemy continued to show the
task as running. A first direct delivery attempt ended in a transport disconnect;
an immediate same-attempt retry completed the upload and receipt. Before this
correction, pull-worker discarded both exceptions and `NodeAgent._upload`
returned `upload_pending` without naming the failing stage or exception class.
That hid whether progress had stopped at claim, upload reservation, byte transfer,
or receipt commit.

## Owning layer and authority

The local Agent journal is authoritative for the saved artifact and same-attempt
recovery. The Bridge server receipt is authoritative for completed delivery and
capacity release. Pull-worker is only a retry adapter. It must not rerender,
create a new task/attempt, or change the existing terminal-timeout policy.

## Minimal correction

- Persist metadata-only upload retry count, stage, and UTC attempt time in the
  local journal. The count increments once when a saved artifact passes local
  validation and enters its upload attempt, not once per HTTP stage. On failure,
  retain only the safe error class and an allowlisted
  Bridge error code; unknown codes become `bridge_error`. Never store exception
  text, prompts, tokens, or image data.
- Return the stage and safe error category to the caller.
- Have pull-worker emit a structured event when a task's delivery outcome
  changes, repeat an unchanged pending state at most every 30 seconds, and emit
  recovery instead of silently discarding every retry result.
- Add a deterministic same-process retry regression: the initial upload
  reservation fails transiently, the next pull-worker pass commits the same
  artifact, and the renderer is called once.

No upload-delivery deadline or new terminal state is introduced. The observed
task eventually received its receipt within the existing task lifecycle, so the
evidence supports an observability defect and a transient delivery retry, not a
new slot-release contract. Revisit only with evidence of an unresolved delivery
past the existing task deadline.

## Acceptance boundary

Offline regression proves retry metadata is safe and that an in-process pull
retry reuses the saved artifact without rerender. It does not prove Alchemy
history/output binding. Real end-to-end acceptance still requires a guarded
Bridge task and confirmation of its receipt and zero active capacity.
