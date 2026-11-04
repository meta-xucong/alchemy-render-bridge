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
- Keep sending the saved artifact's recorded `original_sha256` as the existing
  API contract requires. Bridge continues to validate the upload and receipt
  against that value.
- Add a regression that fails the first upload reservation, then prevents the
  local runner module from accessing a digest function while retrying the same
  artifact. The retry must receive a receipt and the renderer must still have
  run only once.

This corrects the observed local preflight boundary without changing the
shared API, routing, fallback, or receipt contract.

## Acceptance boundary

The offline regression proves that artifact replay no longer performs a second
local digest calculation. The guarded live test must then show local journal
`complete`, a Bridge receipt for the same task/attempt, and zero active queue.
It does not claim Alchemy history binding.
