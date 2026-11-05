# Artifact upload preflight correction

## Theory-first correction model

Expected behavior: after the local Agent has saved and decoded the generated
artifact, it should send those saved bytes with the artifact metadata already
recorded by that same save operation. Bridge's existing upload contract remains
the authority for accepting bytes and issuing a receipt.

Observed mismatch during the 2026-11-04 guarded live run: the local journal
reached `artifact_ready`; the saved file existed, its recorded and actual byte
lengths matched, and the image decoded as PNG. The remote task remained
`running`, with no upload reservation or receipt. The worker surfaced a
`ValueError` at the same timestamp that the saved artifact appeared. This audit
did not independently calculate or compare a content digest.

## Owning layer and authority

The local Agent owns the saved artifact bytes and their metadata. The Bridge
server owns upload acceptance and receipt creation. The extra client-side digest
recalculation immediately before upload duplicates the digest binding already
captured when the artifact was saved, and can stop the request before Bridge
receives any bytes. It is supporting evidence, not a second authority.

## Minimal correction

- Remove the local re-read digest comparison in `_upload()`; retain the recorded
  size check and image decode/format checks.
- Send the saved artifact's recorded size and MIME type. Generated output
  hashes are not part of the upload, receipt, or ACK contract; reference-input
  hashes remain unchanged.
- Add a regression that fails the first upload reservation, then prevents the
  local runner module from accessing a digest function while retrying the same
  artifact. The retry must receive a receipt and the renderer must still have
  run only once.

This corrects the observed local preflight boundary without changing the
shared API, routing, fallback, or receipt contract.

## Acceptance boundary

The offline regression proves that artifact replay does not calculate a
generated-image digest. The guarded live test must then show local journal
`complete`, a Bridge receipt for the same task/attempt, and zero active queue.
It does not claim Alchemy history binding.

## Follow-up root-cause correction

The initial live `ValueError` was later traced to the path-containment guard at
`_upload()` line 614, not to the digest comparison removed above. The test
process had its temporary directory under the Codex sandbox and wrote its local
state through a sandbox filesystem mapping: the logical Agent state root and
the resolved backing path of its files differed. The recorded relative artifact
therefore resolved outside the logical root, and the existing guard correctly
stopped the upload in that sandbox context.

To distinguish a product defect from the test environment, the same saved task
and attempt were retried once outside the sandbox, using their resolved backing
state directory. The existing containment guard passed and the task returned
`artifact_committed`; read-only follow-up confirmed Bridge state `artifacts_ready`,
local journal state `complete`, and the same attempt and receipt on both sides.
No new image task was created. This evidence does not justify weakening or
removing the path-containment guard, and it does not establish a production
filesystem defect. No Alchemy history binding is claimed.
