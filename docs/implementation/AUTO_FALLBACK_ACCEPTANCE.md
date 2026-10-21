# Automatic fallback implementation and acceptance

2026-10-21. This supersedes the earlier design-only status, not the real-machine
acceptance gate. The original implementation stage was independently reviewed at runtime revisions
Bridge `89f665d3c157007fa3e6473cfaf5ef42b3b01347` and Alchemy
`68c079bd9da247999b20e7fce5b638d6d2e99919`. Its historical evidence is preserved
below; later semantic binding and terminal-failure fixes changed runtime code.
The latest published baseline and its separate evidence follow that history.
Published commit identities and GitHub CI are recorded in
[Alchemy PR #37](https://github.com/meta-xucong/alchemy-media-agent/pull/37).
Neither repository is merged or deployed by this work.

## Delivered

- Profile health distinct from worker liveness/capacity/acceptance, strict reports,
  durable monotonic revisions, expiry, actual typed Codex failures, opt-in safe
  probes and separately opt-in account-status recovery.
- Atomic queryable withdraw-before-start proof, never ordinary canceled/404/lease
  expiry as proof. Already started/manual-granted/unknown tasks never reroute.
- Default-off Alchemy `prefer_bridge` mode, explicit per-request cloud cost/input
  consent, same canonical intent and one durable route/execution owner.
- One policy for unavailable and queue-budget cases. Enabled-policy background
  health observation restores future requests after distinct reports and 60 stable
  seconds; failed reads interrupt recovery. Completed/in-flight cloud jobs do not
  migrate back. Drain preserves existing reconciliation and does not admit new work.
- Actual configured traditional production-provider class with frozen prompt,
  ordered original references, deterministic native output checkpoint and query-only
  recovery after uncertain dispatch. Existing provider-internal retries remain;
  no claim of exactly one upstream HTTP attempt. Upstream may charge even though
  current V3 uses its zero-credit development adapter.
- Simple administrator toggle/folded threshold; separate trial consent and actual
  route display. Ordinary Generate and old bridge_only requests remain unchanged.
- Explicit supported Codex on-request policy instead of unsupported untrusted;
  old declarations are not silently migrated, sandbox/network and no-auto-approval
  remain. Fake/configuration defaults do not enable real execution.
- Same-Windows installation/configuration/startup guide and loopback-only Hatchet
  recipe. See [Windows local deployment](WINDOWS_LOCAL_LOOPBACK.md).

## Historical original-stage local verification (89f665d / 68c079bd)

These counts and conclusions apply only to the original stage, not later changes.

- Alchemy contract + affected original-path selection: **370 passed**, 174.23s.
- Bridge full offline suite: **211 passed**, 27.744s; schema export reproduced
  without changes. Real config parsers/doctor and static loopback examples passed.
- Independent final audit: no remaining actionable must-fix. Its final narrow
  runs passed 47 routing/native, 4 account, 9 availability and 6 withdrawal cases;
  counts overlap the full selections and must not be added as unique tests.
- Final actual-HTTP matrix: **12 check groups passed** using actual app.main,
  independent Bridge HTTP, NodeAgent and ProductionImageGenerationProvider,
  with synthetic outbound transport/auth/Brain/review/clock only. Healthy local,
  unavailable cloud, stable recovery, queued atomic withdrawal, started/unknown
  non-reroute, local-only, native checkpoint interruption, host/Bridge restart,
  ambiguous cloud no redispatch, ordinary routes and disabled mode verified.
  Six native outputs and six unique reviews; five cloud execution keys (four
  outputs plus one intentionally ambiguous outcome), no duplicate invocation after
  restart. All processes stopped. Evidence run: `20261021-075938`.
- Browser fixtures are synthetic shipped-page tests, not live login. Final exact
  published-head Chromium/Windows/Linux CI must be read from the PR checks; this
  local receipt does not predeclare them successful.

## Latest published baseline and prior acceptance (2026-10-21 09:24 UTC)

- Bridge `755555e2131693f8c3ecdb3adf48f5eb82d34120`, branch
  `feat/phase1-bridge-contracts`.
- Alchemy `4da1cd541bc3961e29b751781995824c94da0c55`, branch
  `feature/render-bridge-preview`.
- Later fixes preserve sibling frozen plans through server-owned `semantic_v2`
  binding while still enforcing genuine project changes and cancellation. Confirmed
  Codex auth/quota terminal failures require durable evidence and idempotent Node
  confirmation before releasing capacity; the old grant remains and the failed
  task never rerenders or falls back. Unknown executions remain protected.
- Prior final local suites: **394 Alchemy affected-path tests** and **220 Bridge
  tests** passed. The actual app.main + independent Bridge HTTP + NodeAgent/Codex
  simulated-process matrix passed **16 groups**, with 11 outputs / 11 unique
  reviews. Auth, Brain, review, upstream transport and CLI processes were synthetic;
  all spawned processes stopped. These results are recorded in PR #37's
  “Final re-audit and local-machine handoff gate” section.
- Exact-head CI was verified successful:
  [Alchemy mock/browser](https://github.com/meta-xucong/alchemy-media-agent/actions/runs/39575606411),
  [Alchemy legacy regressions](https://github.com/meta-xucong/alchemy-media-agent/actions/runs/39575613605),
  [Bridge Windows/Linux contracts](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39576048403),
  [Bridge experimental Hatchet lifecycle](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39576048358).

The section above records the 09:24 acceptance. The separate repeat audit below
must not be confused with that earlier 16-group matrix.

## Fresh independent repeat audit (2026-10-21 17:17–17:26 UTC)

Clean clones of the two published baseline commits above were installed into
separate fresh Python 3.12 environments. No new runtime blocker was found; this
follow-up changes only acceptance documentation and the Windows explanation that
saved administrator allowlists take precedence over environment defaults.

- Fresh Alchemy complete affected-path selection: **394 passed**, 290 existing
  FastAPI deprecation warnings, 105.51s. Fresh Bridge full suite: **220 passed**,
  13.533s. Both environments passed dependency compatibility checks; exported
  schemas match the checked-in snapshots.
- Independent Bridge recovery audit: 49 existing targeted cases and 3 additional
  cancellation/terminal-evidence/restart combinations passed. Independent Alchemy
  audit: 3 additional pending-review/lost-ACK, committed-response-loss/cancel,
  and cloud-review-resume combinations passed. Targeted existing counts overlap
  the full suite, not additional unique tests.
- A fresh independent **8-scenario actual loopback HTTP** harness passed: normal
  native registration and replay; lost native-commit response; unknown native
  import refusing repeat registration/ACK; Bridge process outage and recovery;
  pre-start queue withdrawal then cloud; unavailable node then cloud; disabled
  integration/default-provider isolation; and retryable review timeout followed
  by natural scheduled recovery. It uses real Alchemy module routes, native
  gateway/persistence, a separate Bridge process and separate Agent execution.
  Host auth/planning/review and outbound provider transport are synthetic. The
  review scenario waits for the normal 30-second retry, without DB/clock edits.
- During that harness, the Bridge checkout moved only through this two-document
  update. Runtime `src` tree remained
  `a9fd75c25436dabf8bb94be343eb55274ca20f85` and `tests` tree remained
  `67aa5017b64daae7dd05266a41f167075c13527d`; Alchemy stayed at `4da1cd54`.
  This is runtime-content equivalence, not a claim that checkout SHAs were equal.
- Configuration parsers, doctor and static loopback Compose checks passed. Doctor
  still reports real login and generation unverified. No user computer, actual
  Codex account, paid provider, merge or production deployment was used.

Fresh browser/Windows execution was not repeated locally in this documentation-only
follow-up. The prior exact-runtime browser/platform CI links above remain evidence
for those unchanged files; any new documentation-head CI is recorded separately
in PR #37. Real-machine gates below remain open.

## Remaining real acceptance and bounded limitations

User's first real topology is one Windows computer: local Alchemy, local Bridge,
user-owned/logged-in Codex CLI and a local compatible Hatchet engine. Independent
HTTP and private data directories remain required. Docker/PowerShell/real CLI
were not run here; Compose was validated statically, not launched. No credentials,
actual account refresh, real Discord send, paid generation or user-PC operation
occurred. Real local image quality, reference fidelity, provider compatibility,
network/sleep recovery and charges still require explicit local acceptance.

Account probing defaults off. Enabling it may let the user's CLI refresh its own
existing login; only a strict positive official status is recovery evidence.
If disabled/unsupported, account/quota faults need explicit cause-confirmation
revalidation. No paid image probe is used. First-ever offline Bridge has no trusted
negotiated contract and fails closed; after compatible negotiation, new unsubmitted
requests can fall back during outage. Ambiguous existing creation cannot do so.

Current integration remains V3 Standard/general, single-image, development and
review-only. It is not a release of formal delivery/billing or all-version adapters.
No source is allowed to manufacture account permission, bypass input/capability
constraints or promote test output. See [08 contract](../shared-contract/08_自动回退代码落地与兼容契约.md).
