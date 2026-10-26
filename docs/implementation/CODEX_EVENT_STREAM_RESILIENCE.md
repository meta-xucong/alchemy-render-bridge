# Codex app-server event stream resilience

## Correction model

The Codex stdio adapter must distinguish protocol evidence from ordinary
streaming notifications. A legitimate burst of `item/commandExecution/outputDelta`
messages is not evidence that execution is invalid and must not abort a turn.
Image success still requires exactly one event-bound completed image item and a
matching completed turn; streamed text, a CLI exit, or a saved file alone never
proves success.

The former implementation imposed a lifetime count of 256 method notifications
both while waiting for RPC responses and while waiting for turn completion. The
Codex CLI can emit hundreds of output deltas when a local skill file is read.
Crossing that generic count caused a protocol exception, context cleanup
terminated the local app-server process, and the attempt was conservatively
reported as `execution_unknown` before an image event could arrive.

## Implementation contract

- Read valid JSONL notifications with backpressure. The bounded queue may slow
  the producer, but queue fullness alone must not turn into a failed attempt.
- Preserve existing line, total-byte, JSON-depth/complexity, request, timeout,
  and captured-lifecycle-event bounds. Stream notification count is diagnostic,
  not a correctness limit.
- Retain only precisely useful lifecycle evidence: an image-generation item
  completion and the matching turn terminal. Count other notifications by a
  fixed allowlist and discard their payloads. Never persist command output,
  full prompts, credentials, or image base64 in diagnostics.
- Diagnostics are append-only and restricted to stage, failure class, event
  counts, byte/structure counts, and child exit status. A failed diagnostic
  write cannot change the `execution_unknown` safety outcome.
- Normal completion accepts both verified Codex image item forms:
  `imageGeneration` and `Extension(kind=image_gen.generation)`. Both require the
  same thread, turn, item, single-image, exact original path, and byte/hash
  validation. Interrupted-turn recovery remains separately bound to rollout
  identity and its existing review-only contract.
- A started attempt is never launched again because event collection failed.
  Recovery continues to use the same task and attempt identity.

## Regression evidence

The local stdio fixture emits 500 command-output deltas before one bound image
item and a completed turn. Acceptance requires one CLI launch, the exact original
bytes, and a completion record containing event counts but none of the streamed
payload text. A second fixture exercises the verified Extension image-item form.
The existing protocol/resource limits and approval fail-closed tests remain in
the adapter regression suite.

This document and its synthetic fixture do not establish that a real local
account/model is healthy or can generate. Real Codex execution remains a guarded
local acceptance step after the paired Alchemy and Bridge candidates are tested.
