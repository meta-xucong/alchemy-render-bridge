# Offline executor components and migration boundary

The cloud implementation can be developed without a Codex login. This slice adds
local components under `render_bridge.executors`. Server and Agent configuration
can now explicitly enable Codex/Midjourney profiles; fake remains the default.
Enabling a profile does not mean a real renderer has passed local acceptance.

## Common API

`ExecutionRequest(task_id, attempt_id, prompt, parameters={}, references=())`
contains one frozen request. `ExecutionResult` is `ready`, `needs_attention` or
`execution_unknown`. A ready result contains the exact original file path, SHA256,
size, MIME, dimensions and provenance. An Agent must still obtain its server start
grant, enforce the server upload limits and reconcile cancellation before upload.

`FakeExecutor(root).execute(request)` produces a deterministic synthetic PNG (or declared JPEG) and
reuses the cached original for the same attempt. A changed request cannot reuse
the same attempt. A start with no result stays unknown rather than rerendering.
References, when supplied, are ordered dictionaries with `input_ref`, `role`,
`hash`, and a staged local `path`. They are hash-checked and recorded, **not used
as conditioning for the synthetic image**. Fake dimensions remain 1–1024 per side
and the fake profile's 1 MiB output limit still applies. Reference input uses
the shared 16 MiB / 16,777,216-pixel Bridge contract.

The local artifact collector supports PNG/JPEG up to 16 MiB / 40 MP. That is only
also the configured Codex/manual server profile limit; the default fake profile
still has its own smaller limit. Manual requests require explicit width/height
and reject originals whose dimensions differ. Codex dimensions are request
evidence only: the server records and accepts the one validated original at its
actual dimensions, without resizing or rerendering it.

## Explicit local commands

Install this repository in the existing Python 3.12+ environment. A request JSON
has `task_id`, `attempt_id`, `prompt`, optional `parameters` and `references`.
For example:

```json
{"task_id":"sample-task","attempt_id":"sample-attempt","prompt":"白色瓶子","parameters":{"width":32,"height":32},"references":[]}
```

```sh
python -m render_bridge.executors fake --root ./executor-data --request request.json
python -m render_bridge.executors probe-codex
python -m render_bridge.executors manual-export --root ./executor-data --request request.json
python -m render_bridge.executors manual-import --root ./executor-data --request request.json --file ./chosen-original.png --confirm-request-sha256 <digest-from-export> --confirm-single-original
```

Manual export writes full UTF-8 `prompt.txt` and `task.json`, preserving identifiers,
parameters and prompt hash. It never invokes Discord or `/imagine`. Import is an
explicit selection of one task and one original, with a matching request digest
and a single-original confirmation. Provenance is `user_declared_midjourney_original`,
not platform-verified authenticity. No global Downloads scanning occurs. Reference
image manual workflows remain explicitly unvalidated, not silently reduced to text.

## Codex implementation and exact limitation

The cloud has `codex-cli 0.163.0`. Only `--version`, `app-server --help` and local
schema generation were run, using an empty temporary CODEX_HOME for the schema
probe. No login, model request or credential read was performed. Schema hashes
are recorded in `CODEX_SCHEMA_PROBE.json`; the actual command is recorded there.

The generated schema confirms `item/completed` with `threadId`, `turnId`, and an
`imageGeneration` item containing `id`, `status`, `result`, and optional `savedPath`.
The collector requires one completed image item and a completed matching turn.
It accepts an original already inside the bound attempt, or a direct file under
`CODEX_HOME/generated_images/<matching-thread-id>/`; the latter is copied into the
Bridge attempt and retention registry. Symlink/junction paths, other thread folders,
and all other locations are rejected. Text saying “image generated”, latest file
timestamps, or another turn's image cannot satisfy it. `result` is not guessed as a
file path or base64 payload. These protocol fields are evidence of schema, not
evidence that the current account/model can generate images.

`StdioSession(argv, cwd)` implements bounded JSONL request/response transport and
server notifications without a shell. Server requests for approval/input are
returned as attention requirements, never auto-approved. It is tested against a
fixed local mock subprocess. Closing it only confirms the local subprocess stop,
not that upstream work has stopped.

`CodexExecutor(root, CodexConfig(...)).execute(request)` now implements the full
versioned lifecycle: initialize, initialized notification, thread/start, turn/start,
exact item/completed collection and matching turn/completed. It persists the start
marker before launch, thread and turn IDs immediately after responses, and the
completed event evidence before importing the original. Replay returns the cached
image or reprocesses completed evidence; an incomplete prior start never launches
a replacement turn. Approval requests become needs_attention. Transport failures
remain execution_unknown rather than causing duplicate generation.

The default config is disabled. Explicit local opt-in after account/model and tool
policy acceptance is through the strict local Agent JSON config (see
`config/agent.example.json`) or Python API, never a task-supplied setting. These
flags are operator declarations, not an observed capability measurement:

```python
from pathlib import Path
from render_bridge.executors import CodexConfig, CodexExecutor
config = CodexConfig(
    enabled=True,
    executable="codex",
    model="<model accepted in your local image smoke test>",
    accepted_cli_version="codex-cli 0.163.0",
    approval_policy="on-request",
    image_capability_verified=True,
    tool_policy_verified=True,
)
executor = CodexExecutor(Path("./executor-data"), config)
# Only after obtaining the Agent start grant:
result = executor.execute(frozen_request)
```

Every new execution additionally checks the installed version exactly matches
one of `codex-cli 0.157.1` or `codex-cli 0.163.0` and advertises stdio. The
private Agent config must set `accepted_cli_version` to the exact version whose
local image and tool acceptance the flags represent; the probed version must
match before a turn can start. Static wire review found parity for the methods
and events used here; it does not prove
login or image generation. Each exact version needs separate local real-image
acceptance before its capability flags may be enabled. The lifecycle requires
`approval_policy=on-request`;
server approval and tool requests are never automatically approved, and human
attention is required when they arise. It uses workspace-write limited to the
attempt, excludes temporary writable roots, and denies sandbox network access. Ordered
references are hash-checked and sent as schema-defined localImage inputs. The
original prompt is preserved as the first text input; rendering parameters are
additional instructions, not invented structured image API arguments.

Concrete remaining local acceptance gaps:
- The schema has **no built-in-tool allowlist**. sandboxPolicy constrains local
  execution, not the model's hosted image tools or configured MCP/plugins. The
  local operator must validate the dedicated Codex configuration has only the
  intended tools available before setting tool_policy_verified. Developer
  instructions are guidance, not a tool isolation guarantee.
- The schema confirms imageGeneration/savedPath but does not confirm any specific
  account/model can invoke it or will save exactly one original. Local smoke
  acceptance must establish that. Missing savedPath/text-only completion returns
  needs_attention, never fake success.
- Reference roles and rendering dimensions are preserved as request evidence but
  are not asserted to be hard guarantees of the Codex image model.

These are capability/configuration acceptance requirements, not missing lifecycle
code. The full enabled path is tested using a fixed Python stdio subprocess only;
no real app-server turn was started in the cloud. The server defaults to the fake profile; explicit profile routing is implemented,
while real local end-to-end capability acceptance remains required.

## Verification and stopping point

Offline tests cover Fake replay, stale-start preservation, reference hashing,
manual binding/import/replay, exact Codex image ownership, text-only rejection,
mock stdio, full enabled lifecycle/image replay, full text-only lifecycle rejection,
and no auto-approval. Real Codex/Midjourney generation, Discord delivery,
account quota and real Windows image production are deferred to local acceptance.
The implementation does not modify Alchemy, create credentials or invoke paid tools.

## Shared configuration and diagnostics

`AgentConfig` is shared by the executor registry and local doctor. It rejects
empty, duplicate and unknown enabled profiles, unknown fields, and string
booleans. Existing valid fake/Codex/manual configuration remains accepted.

Use `python -m render_bridge.doctor --bridge-config ... --agent-config ... --json`
for read-only configuration checks. `--probe-codex` explicitly permits only
version/help in a fresh temporary home, not a login, protocol turn or generation.
The report never claims real generation or login verification. The additive
profile `parameter_contract` describes accepted width/height/seed and optional
nested `image_options` independently of actual executor control. Codex receives
text instructions, manual input is user-selected, and fake seed is not applied.
Nested quality accepts only `auto` (unspecified tier); `output_format` PNG/JPEG
is a decoded-original postcondition; input fidelity must be omitted. Explicit
fidelity, unsupported quality, and manual references fail before execution.
A completed format mismatch retains original bytes and evidence, returns
`needs_attention`, and never converts or regenerates. Cached replay rechecks the
decoded format. Agent interrupted-result recovery uses completed evidence only,
not another CLI turn. See [typed options](TYPED_IMAGE_OPTIONS_SCOPE.md).

Alchemy's integration is maintained and verified separately. Bridge's bounded
typed real-profile contract is implemented; real account/model acceptance is
still pending. Logging in alone does not provide native image-tool controls.
