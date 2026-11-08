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
