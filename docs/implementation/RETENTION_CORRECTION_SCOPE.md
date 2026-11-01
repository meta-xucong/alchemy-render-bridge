# Bounded artifact retention correction

Baseline: 1159150. Blueprint §5/§10 requires reference-aware reclamation, not permanent pins. Existing cleanup was harness-only and input session capacity counted expired metadata forever.

## Model before implementation

Opt-in configured runtime maintenance, default off; no deployment or deletion of user data in this change. Each pass has a fixed maximum number of candidates/deletions. Eligible tasks: acknowledged artifacts (accepted or rejected), or terminal/expired tasks with no start grant. Never infer stopped execution from cancellation, manual timeout, lease expiry, or age. Started tasks without a receipt acknowledgement retain all bytes and references. Unacknowledged outputs, unknown work and orphan files remain untouched.

Record first observed eligibility durably; wait configured retention delay before releasing task input pins and reclaiming acknowledged output bytes. Input TTL still applies after release; shared references retain inputs. Preserve all task, receipt, acknowledgement, input-key and upload idempotency metadata. Expired artifact downloads return 410 artifact_expired; manifests remain available. Expired input sessions no longer consume the active-session count.

Deletion is two-phase: commit expired metadata and a durable deletion queue, then unlink only exact server-generated blob names. Failures retain queue entries for retry; a crash cannot leave live metadata pointing to bytes deleted by this maintenance. Do not scan or delete arbitrary files. This bounds media storage during normal acknowledged use, not indefinite metadata growth or protected unresolved work. Capacity errors remain correct when protected data fills the quota.

Runtime background loop uses its configured workspace only, a finite interval and per-pass batch; no public maintenance route. Fixtures prove reclamation and preserved sole copies. Local Agent cleanup uses the separate acknowledgement-aware model below: Bridge receipt alone is not source acknowledgement, so never delete Agent originals merely on local complete state.

## Local Agent correction model (before implementation)

Explicit `maintain` CLI, no new scheduler and no execution grant. Bounded completed-journal scan, fresh authenticated node task query matching task/attempt/receipt and accepted/rejected source acknowledgement. Unreachable or mismatched queries preserve files. First observed ACK starts the grace period. Persist exact cleanup intent, retain completed journal and executor start/binding/manifest evidence. Remove only registered result/reference copies inside the managed attempt and the executor's known artifact.original. Codex collection additionally registers its validated in-attempt generated original; manual imported source paths are never registered or removed. No recursive deletion, no directories or unknown cache files. Executor retirement marker makes direct cache reuse return artifact_expired instead of allowing new execution.

## Fixture validation, 2026-10-21

Baseline 1159150; Python 3.12, temporary synthetic filesystem/SQLite/HTTP fixtures.

- `PYTHONPATH=src python -m unittest discover -s tests -p '*retention.py' -q`: 16 passed.
- `PYTHONPATH=src python -m unittest discover -s tests -q`: 157 passed, no skips.
- `git diff --check`: clean.
- CLI `maintain --help`: passed without credentials or creating a runtime store.
- 105 input + acknowledged-output cycles under a two-image byte quota prove both media reclamation and recovery of the 100-active-input-session limit; idempotency metadata survives.
- Source ACK/retention delay, shared queued/unknown pins, unacknowledged originals, expired download410, deletion failure/restart, scoped batches, Agent ACK-check offline skip, Agent deletion retry, no rerender after cleanup, unknown local evidence, symlink rejection, managed generated originals and external manual-import original preservation covered.

No real account, paid rendering, existing user-data deletion, service deployment or push. Metadata growth, protected unknown/unacknowledged media, and unregistered legacy/orphan files remain operator-visible capacity limitations; this is not a generic filesystem vacuum or a production storage implementation.
