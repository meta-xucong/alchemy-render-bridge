# Codex render execution effort

Current candidate: explicit `medium` reasoning, optional trusted-node
`priority` tier, and a native return-shape/one-invocation contract. Historical
diagnoses and their corrections below are retained as evidence; `low` is no
longer the default and was not a measured acceleration.

## Correction model (2026-11-08)

The rendering node receives an already planned, frozen prompt. Its job is to
invoke ImageGen once, preserve all input references and rendering options, and
return the original only after a bound terminal turn. Creative planning and
visual review remain owned by Alchemy.

The deployed node did not specify turn reasoning effort. Two completed local
rollouts confirmed inheritance of the developer account's `xhigh` setting.
Before the first ImageGen invocation they spent 136.673 and 127.838 seconds;
the image-tool windows were approximately 92 and 95 seconds, and the terminal
tails approximately 7 and 6 seconds. Their node executions were serial with
about eight seconds between them. These observations identify repeated
pre-tool reasoning as the first bounded optimization target; they do not
measure all remote queue time or promise a future latency.

This is an execution-adapter correction, not a change to image quality or
Alchemy's creative/review contracts. A desktop-wide reasoning preference is
not the authority for the renderer's execution effort. The trusted node
configuration will explicitly select `low` for new rendering turns, with a
validated operator override. The same model, full prompt, references, native
image options, one-image limit, deadline and terminal/recovery checks remain.
No prompt shortening, alternate creative plan, concurrency change or review
threshold change is needed. Historical attempts must still replay unchanged.

Focused regression must demonstrate explicit default and override on the
actual `turn/start` wire, lossless prompt and reference input, unchanged image
options/model and original bytes, and recovery without another CLI turn.
An offline fixture proves the contract only; measured acceleration and visual
adequacy require a bounded real-render acceptance after the code checks.

The Alchemy application and VPS API do not need a coordinated release for
this node-local configuration change. The shared HTTP request schema stays
unchanged. The active local node must be idle before replacing its process.

## Offline verification

The accepted local CLI 0.163.0 schema includes `TurnStartParams.effort`; the
configured gpt-6-luna model's local capability cache advertises `low`.
The lifecycle, offline executor, contract upgrade, aborted reconciliation and
health-error suites ran 70 tests successfully, with one existing opt-in skip.
Independent read-only review found no scope or correctness defect in this
change. These checks do not establish a measured runtime improvement.

## Correction after the first controlled render

The first real comparison completed successfully in 243.922 seconds, at
1024x1536 with the same model and frozen prompt. The rollout confirmed `low`
and zero reasoning-output tokens before the image tool. However, its pre-tool
interval was still 135.491 seconds, versus 136.674 in the baseline. The
baseline itself had only 139 reasoning-output tokens in that first response.
Therefore the initial attribution to excessive reasoning is **superseded**:
effort inheritance is real, but lowering it alone has not accelerated this
sample. Pre-tool wall time must not be described as measured thinking time.

Both rollouts waited about 101 seconds from ready context to the first
assistant message. Baseline input was 24,752 tokens (12,928 cached); the
comparison had 29,942 (8,320 cached), including repository instructions because
its scratch cwd was under the Alchemy checkout. Both inherited a large skills
catalog. The next investigation must distinguish upstream response waiting
from irrelevant input context; the one-render comparison does not establish
their individual causal contributions. Do not shorten the approved image
prompt, remove terminal evidence, or advertise a speedup from this result.

The node was restored on revision 433dc66 after terminal image completion.
The isolated acceptance did not create a production Alchemy job/output or
alter earlier receipts. Production frontend/review behavior was not rerun in
this node-only comparison. Visual inspection found the requested portrait,
lighting, costume, fan and indoor setting preserved; this is not a claim of
universal quality equivalence or a substitute for V3's normal review.

## Revised minimal correction

The accepted CLI's live model catalog advertises the same gpt-6-luna model's
`priority` service tier. Its protocol supports an explicit `turn/start`
`serviceTier`; `fast` is the UI/config alias, while `priority` is the advertised
wire identifier. Add an optional trusted-node `service_tier` setting, omitted
by default, and opt this node into `priority`. This changes response scheduling
without changing the creative model or image-generation parameters. Fast mode
can consume the account's usage allowance faster.

Keep the model, complete prompt and production-style cwd/context for the next
bounded comparison. Do not combine catalog removal with the tier change and
then assert a single cause. The large inherited skills catalog is a separately
identified candidate, not yet a verified latency cause. Regression must prove
default inheritance, explicit priority wire value and unchanged rendering
inputs/terminal recovery. Real timing remains required after those checks.

## Full workflow audit after the priority comparison

The priority rollout confirmed `service_tier=priority`. Its first tool call
started 23.318 seconds after turn start (baseline 136.674), with 24,560 input
tokens and zero cached tokens. However, the comparison is **not accepted**:
the CLI called ImageGen three times and the existing collector correctly
returned `codex_single_image_event_required`. No production job, image or
receipt was created by this isolated comparison. The original production
service tier was restored after the bound test turn completed.

Authority trace:

1. Alchemy owns the frozen creative prompt and requested output count.
2. Bridge owns one task/attempt and one physical execution permit.
3. The CLI invokes native ImageGen. Its result is `{image_url, output_hint}`;
   code mode must display it with `generatedImage(result)`.
4. Native completed image events own artifact identity and savedPath. Empty
   code-mode display text does not invalidate a completed native generation.
5. Bridge accepts exactly one distinct native image only after its bound turn
   completes, then normal Alchemy persistence/review/delivery owns the result.

The faulty CLI code iterated `result.content`, treating the native return as
an MCP content array. That silently displayed nothing. It interpreted the
empty display as no image and repeated generation twice. Thus the owning
defect is the Provider/tool return contract, not image quality, review or the
collector's single-image rule. An instruction to produce one image did not
clearly define a one-invocation budget or the native return shape.

Minimal complete correction: explicitly give the renderer the native result
shape and `generatedImage(result)` forwarding contract; permit at most one
ImageGen invocation, including failure/empty-display cases; never retry or
change the prompt inside the CLI. Failures remain owned by the existing outer
workflow. Keep terminal and single-image acceptance checks unchanged, and
regress rejection of multiple distinct completed image events. The public
turn protocol exposes no max-tool-call field; do not invent a hard guarantee
from outputSchema or hide extra images by selecting the first.

Use the model's normal `medium` reasoning effort for the balanced final
configuration: low alone had no latency benefit. Priority targets response
waiting without removing useful protocol reasoning. No further real render
may run until the contract change and focused regressions pass.
