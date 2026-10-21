# Automatic fallback implementation and acceptance

2026-10-21. This supersedes the earlier design-only status, not the real-machine
acceptance gate. Implemented and independently reviewed at runtime revisions
Bridge `89f665d3c157007fa3e6473cfaf5ef42b3b01347` and Alchemy
`68c079bd9da247999b20e7fce5b638d6d2e99919`; subsequent receipt edits are docs only.
Published commit identities and final GitHub CI are recorded in Alchemy PR #37.
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

## Final local verification

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
