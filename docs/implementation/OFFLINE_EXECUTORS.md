# Offline executor components and migration boundary

The cloud implementation can be developed without a Codex login. This slice adds
local components under `render_bridge.executors`; it does not register real
Codex/Midjourney profiles in the server or claim a real renderer has passed.

## Common API

`ExecutionRequest(task_id, attempt_id, prompt, parameters={}, references=())`
contains one frozen request. `ExecutionResult` is `ready`, `needs_attention` or
`execution_unknown`. A ready result contains the exact original file path, SHA256,
size, MIME, dimensions and provenance. An Agent must still obtain its server start
grant, enforce the server upload limits and reconcile cancellation before upload.

`FakeExecutor(root).execute(request)` produces a deterministic synthetic PNG and
reuses the cached original for the same attempt. A changed request cannot reuse
the same attempt. A start with no result stays unknown rather than rerendering.
References, when supplied, are ordered dictionaries with `input_ref`, `role`,
`hash`, and a staged local `path`. They are hash-checked and recorded, **not used
as conditioning for the synthetic image**. Fake dimensions remain 1–1024 per side
and the current server's 1 MiB upload limit still applies.

The local artifact collector supports PNG/JPEG up to 16 MiB / 40 MP. That is only
a local import limit, not a promise that today's fake-only service accepts large
real images. Do not advertise a real profile before its server limits and local
acceptance have been configured and tested.

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
The collector requires one completed image item, a completed matching turn, and
an exact original path within that attempt. Text saying “image generated”, latest
file timestamps, or another turn's image cannot satisfy it. `result` is not guessed
as a file path or base64 payload. These protocol fields are evidence of schema,
not evidence that the current account/model can generate images.

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
policy acceptance is through the Python API (not a task-supplied setting):

```python
from pathlib import Path
from render_bridge.executors import CodexConfig, CodexExecutor
config = CodexConfig(
    enabled=True,
    executable="codex",
    model="<model accepted in your local image smoke test>",
    image_capability_verified=True,
    tool_policy_verified=True,
)
executor = CodexExecutor(Path("./executor-data"), config)
# Only after obtaining the Agent start grant:
result = executor.execute(frozen_request)
```

Every new execution additionally checks the installed version exactly matches
0.163.0 and advertises stdio. The lifecycle uses untrusted approvals routed to the
user, workspace-write limited to the attempt, excludes temporary writable roots,
and denies sandbox network access. It never approves server requests. Ordered
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
  account/model can invoke it or will save exactly one original under cwd. Local
  smoke acceptance must establish that. Missing savedPath/text-only completion
  returns needs_attention, never fake success.
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
