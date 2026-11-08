# Codex render execution effort

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
