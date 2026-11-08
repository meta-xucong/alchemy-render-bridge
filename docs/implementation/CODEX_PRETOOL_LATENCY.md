# Codex pre-tool latency: correction model

## Authority and observed mismatch

The node executes Alchemy's frozen prompt once, then returns the native original
after its bound turn completes. This is an execution-adapter change; Alchemy's
foundation quality, review, selection and frontend projection remain authoritative.

Read-only CLI logs for the final 2026-11-08 sample establish a transport defect:
startup WebSocket prewarm timed out; six subsequent WebSocket connection attempts
took about 15 seconds each, with five retry delays. The first connection to HTTP
fallback interval was 96.213 seconds. HTTP response headers then arrived in 1.868
seconds and a model output item in another 0.768 seconds. Together with prewarm,
failed connection setup accounts for most of the 126.778-second pre-tool delay.
This supersedes the earlier unverified attribution to excessive reasoning or
context processing. The exact network cause of the WebSocket handshake failures
is not established; HTTP is the observed successful transport on this node.

## Minimal complete correction

1. Add bounded, payload-free subprocess timing to existing execution diagnostics:
   initialization, thread/turn requests, first model delta, image start/end and
   terminal completion. These are observed client times, not provider compute time
   or proof of execution. Existing lifecycle evidence remains the sole authority.
2. Provide a read-only diagnostic for the existing CLI log database, scoped to an
   exact thread. Emit only known event labels, timestamps and numeric timing;
   never copy prompts, headers, credentials, tool arguments or log bodies.
3. Opt this accepted node into HTTP Responses from the start using a child-process
   provider configuration. Keep the official OpenAI login, backend, text model,
   medium effort, default service tier, native ImageGen and all image inputs.
   Do not modify global CLI settings or reduce retry/terminal safety thresholds.
4. After isolating the transport benefit, compare removal of the automatic skills
   instruction catalog only. Keep the full frozen user prompt, references, native
   tool instructions, image options, approval policy and sandbox unchanged.

In CLI 0.163.0, built-in providers cannot be overridden by a same-name config
entry; legacy responses_websockets feature toggles do not control transport.
The supported provider capability is `supports_websockets=false`. A node-local
alias with `name="OpenAI"`, Responses wire API and `requires_openai_auth=true`
keeps the official backend route/auth behavior. This is explicit node opt-in;
other installations keep automatic transport. Native-image availability must be
verified with the installed CLI before activation. Do not invent provider URLs
or move credentials into configuration.

The alias does not inherit the built-in provider's runtime-only internal metadata
opt-in. Thus configuration equality is not claimed: authenticated model discovery
and native-image acceptance must demonstrate the required behavior. Source basis:
[provider merge/routing](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/model-provider-info/src/lib.rs)
and [transport selection](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/client.rs).
The [versioned config schema](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/config.schema.json)
defines `skills.include_instructions`; this removes automatic skill instructions,
not native tool definitions, full user input or project/approval policy.

## Validation plan

Focused offline regressions must verify timing labels/privacy, configuration
wire, unchanged prompt/references/image options/original bytes and existing
terminal/replay/multiple-image rejection. Read config/model metadata without
starting a turn first. Then run a bounded HTTP-only sample and a separate
HTTP-plus-catalog-trim sample with the same frozen prompt in the same isolated
host root. Compare exact-thread transport logs, input usage, image count, native
terminal result and original image. No test creates Alchemy production records.
If the final context sample passes, one repeat is justified to check the earlier
observed transport variability. Deploy only the tested configuration after
independent audit and an idle-node check; preserve all original evidence.

## Correction after the repeat exposed a second wait

The first HTTP samples completed in 125.968 and 135.016 seconds. Disabling the
automatic skill block reduced first-request input from 24,644 to 18,249 tokens,
but did not independently accelerate the sample. The third sample must not be
discarded: it completed in 434.047 seconds with exactly one native image, after
an HTTP response returned headers quickly but delivered no SSE event for 300
seconds. The existing CLI retried that text request once, then received model
output in seconds. No second image was generated. This is a distinct post-header
stream wait; the evidence cannot distinguish upstream scheduling from network
delivery. Do not describe the entire interval as model reasoning.

The smallest additional transport correction is a 60,000 ms SSE idle limit in
the explicitly selected HTTP node profile. Auto transport keeps its defaults.
This is an event-to-event idle limit on text Responses, not a first-token-only
limit, an image-generation deadline, a reasoning budget or a new retry loop.
The CLI's retry budget/backoff, full history, native tool contract and the
outer 600-second deadline remain unchanged. A legitimate text response silent
for over 60 seconds can consequently be resampled earlier.

Source trace in CLI 0.163.0: provider.stream_idle_timeout is passed by
`codex-api/src/endpoint/responses.rs` to `sse/responses.rs::process_sse`, which
wraps each stream.next(). Native ImageGen instead follows
`ext/image-generation/src/tool.rs -> backend.rs -> endpoint/images.rs ->
EndpointSession.execute -> transport.execute`; its request timeout is not
derived from the Responses SSE idle limit. `session/turn.rs` drains in-flight
tools into history before propagating a stream error and resampling. The
existing one-invocation instruction and strict native single-image acceptance
remain necessary; transport changes do not mathematically constrain the model.

An installed-CLI, empty-auth-home, loopback-only deterministic test stalled the
first synthetic SSE stream with a 200 ms idle limit. The original retry made a
second request after 0.438 seconds and completed normally, with no authorization
header and no image calls. Its first fixture omitted required total_tokens;
that local fixture was corrected before the recorded pass. No provider request
or production record was involved. Add regression for the idle-timeout diagnostic
label and the node's 60-second wire setting. One final real native acceptance
is now justified by this observed second defect and bounded correction; retain
all prior samples, and verify an image tool lasting over 60 seconds can complete.
