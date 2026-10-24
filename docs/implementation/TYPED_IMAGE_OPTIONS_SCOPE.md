# Typed image-options scope

This bounded change adds optional `parameters.image_options` without changing the
v1 task envelope, omitted legacy parameters, request hashes, or attempt identity.
It validates the same contract at HTTP, domain, and executor boundaries: nested
version `1`, quality `auto` only (unspecified tier), PNG/JPEG decoded-original
postconditions, and omitted-only input fidelity. Manual profiles reject references.

Codex 0.163.0 has no structured quality, output-format, or input-fidelity control.
The adapter must not invent one, transform/relabel originals, or start another
external turn to repair a mismatched completed artifact. This work preserves
completed evidence and tests normal, replay, and recovery paths using mock stdio
and synthetic images only. Descriptor/schema exports and migration/doctor wording
will distinguish admission/postconditions from actual native control. Defaults
remain fake-only and all real-tool acceptance declarations remain false.

No real CLI, account, provider, deployment, push, merge, or pull request is part of
this implementation. Existing boundaries and independently deployed API remain.


## Wire and descriptor

When present, all three fields are required, with no coercion or defaults:

```json
{"parameters":{"width":32,"height":24,"image_options":{"schema_version":"1","quality":"auto","output_format":"jpeg"}}}
```

`image_options_contract` is additive to each capabilities profile:

```json
{"schema_version":"1","quality":{"values":["auto"],"semantics":"unspecified_only"},"output_format":{"values":["png","jpeg"],"semantics":"original_artifact_postcondition"},"input_fidelity":{"values":[],"semantics":"omitted_only"},"references":{"max_count":8,"semantics":"local_image_input_unverified"}}
```

Fake references use `hash_checked_not_conditioning`; manual uses max_count 0 and
`not_supported`. Existing parameter_contract version/revisions remain unchanged;
accepted_parameters adds image_options. Its unsupported_parameters are names at
the top level, not a denial of the explicitly advertised nested contract.
Consumers of old v1 descriptors must not infer support for the optional object.

## Fixed protocol evidence and real-tool boundary

Official Codex `rust-v0.163.0` schema sources:

- [TurnStartParams](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/app-server-protocol/schema/json/v2/TurnStartParams.json)
- [ThreadStartParams](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/app-server-protocol/schema/json/v2/ThreadStartParams.json)
- [Core configuration](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/core/config.schema.json)

These schemas expose no structured quality, output-format, or input-fidelity
control. No CLI probe was repeated for this change. Width/height/seed and optional
image options are conveyed in human-readable instructions only. The adapter
accepts only a matching completed imageGeneration savedPath and original bytes.
It does not invent provider options or assert actual image-tool capability.

## Completion, mismatch, and recovery

All HTTP/domain/executor entry points reject unsupported options before a claim,
start, export, or external call as applicable. Manual reference requests are
rejected before export/import. The frozen object is retained without dropping
fidelity or substituting defaults. Omitted legacy payloads preserve their hash,
server idempotency identity, and local AttemptFiles binding.

Fake can generate either deterministic format directly; legacy PNG bytes remain
unchanged. Real originals are never transcoded. The collector persists original
bytes and their true decoded MIME, then returns `needs_attention` with
`output_format_mismatch` if the postcondition is unmet. Cached replay recomputes
that check from bytes rather than trusting a suffix or cached MIME. A different
original cannot replace the retained evidence in the same attempt. Manual import
uses the same rule and does not submit another task on a mismatch.

The server independently matches upload MIME to decoded format and rejects a
postcondition mismatch with 422 before a receipt. A crash after completed Codex
evidence but before Agent artifact persistence can recover that same original
and finish upload, including an expired-claim upload-only renewal. Recovery does
not probe, execute, or request another start grant. Incomplete starts stay unknown.
Local mismatch attention does not invent a new server lifecycle state: the server
keeps the conservative execution_unknown hold until the attempt is reconciled.

## Verification

The original 123 tests remain part of the regression run. Added offline coverage
includes nested HTTP/domain parity and canonical identity, unsupported quality
and fidelity rejection, direct executor bypass rejection, descriptors, PNG/JPEG
fake/manual flows, actual mock stdio JSONL PNG/JPEG completion, refusal/mismatch
replay, missing-manifest recovery, and interrupted Agent result recovery.
Account login, real CLI/protocol calls, paid providers, production acceptance and
Windows-local execution were not run. Configuration defaults and acceptance flags
are unchanged; the flags remain operator declarations, not measured acceptance.

Verified on 2026-10-21 against base
`5c157ff91127b8a39dd749f3a282b20b4e60bd69` (tree
`0ccdec5dbffe801d5774f35f8463d89f289aba81`): 141 unittest cases passed,
including the original 123 and 18 added cases with parameterized combinations.
Compileall, contract export, and `git diff --check` passed. Only task.schema.json
changes in the exported contracts, adding the optional closed ImageOptions object;
input and acknowledgement schemas are unchanged. Tests ran using the existing
isolated Python 3.12 environment with this worktree's src selected via PYTHONPATH.
