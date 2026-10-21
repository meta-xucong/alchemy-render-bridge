# Profile health and withdrawal runtime correction

Implemented in the feature branch, not production or real Windows acceptance.

- `routing_availability_v1` is additive; legacy heartbeat and source task payload remain compatible. A missing report is unknown, not proof that the worker can render. Server timestamps expire after 90 seconds. Durable node/profile report revisions reject replay without refreshing TTL; the global signal revision survives restart.
- Tool health, physical-slot capacity, node authorization, server admission and production acceptance remain separate. Recovery hysteresis belongs only to Alchemy. Bridge debounces transient failures twice; auth/quota faults remain latched through omissions and unrelated local checks.
- Withdraw-before-start is an immutable scoped proof table plus permanent canceled fence in the same SQLite transaction as automatic/manual start. Only raw queued and pre-start awaiting_user are eligible, with no grant, upload or receipt. Ordinary cancellation, 404 and unknown execution are never proofs. Late dispatch acknowledgement and start are rejected. No second dispatcher or origin create tombstone is introduced.
- Agent `health_reporting_enabled` defaults false. Enabled probes share the existing heartbeat, at most once every 30 seconds, checking disk and sanitized CLI version/help by default; separately enabled account status is described below. Reports contain allowlisted codes, not logs or account details.
- Codex `approval_policy` defaults empty and requires explicit `on-request` acceptance alongside existing flags. Existing untrusted declarations are not silently migrated. Sandbox remains workspace-write with network disabled, and every server approval/input/tool request still stops with human attention; no auto-approval is added.
- Matched failed-turn structured errors map unauthorized to auth_unavailable and usageLimitExceeded to quota_exhausted. Terminal connection/rate-limit/server errors map probe_failed; input/context/sandbox/policy failures do not poison global health. Current started tasks stay execution_unknown and cannot fall back. Agent persists the bounded cause across restart. No raw provider message enters health reports.
- `health_revalidation_generation` defaults zero. After confirming the original cause resolved, the operator may explicitly increment it. A durable 120-second reporting window is consumed once; restart or rollback cannot extend it. CLI version alone never heals auth/quota failure.

## Pinned upstream evidence, checked 2026-10-21

Official `rust-v0.163.0` sources used without invoking credentials or generation:
- [Typed error vocabulary](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/CodexErrorInfo.ts)
- [Failed turn error binding](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/TurnError.ts)
- [Core error mapping](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/protocol/src/error.rs)
- [Supported policy wire vocabulary](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/AskForApproval.ts)
- [Untrusted-policy startup handling](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/lib.rs)

## Verification

Synthetic tests cover HTTP authentication/strict schemas, withdrawal replay/restart and automatic/manual grant races, revision rollback/expiry, profile isolation, production/admission preservation, actual executor-failure-to-health propagation, and explicit policy gating. No real account, paid provider, local user computer, merge or deployment was used. Full-suite counts and final source revision are reported with the implementation delivery; tests do not establish real-tool acceptance.

## Optional authenticated status probe (separate opt-in)

`account_health_probe_enabled` is a strict AgentConfig boolean, default false. It
only operates when health reporting and all Codex acceptance gates are enabled.
It uses the configured CLI's existing login. The CLI MAY internally refresh its
own existing authentication; an operator must explicitly accept that normal CLI
behavior before enabling this option. Bridge never reads/copies/returns tokens,
starts login, creates grants, or performs paid generation for health checks.

The bounded session sends only initialize, initialized, and
account/rateLimits/read, with supportsLunaReserve=false and
excludeResetCreditDetails=true. Only strict ordinaryUsageAllowed=true is accepted
as active-account-validated recovery evidence (`account_status`). False means
ordinary usage is disallowed, reported conservatively as probe_failed, not a claim
of quota exhaustion. Null, absent, malformed, denied or timed-out probes never heal
an existing fault and otherwise report unknown. Account metadata and credit fields
are discarded; no percentages/reset times are used to infer recovery.

Node heartbeat profile_health additionally accepts revalidation_generation,
a nonnegative integer defaulting to zero. Bridge persists the highest consumed
value per node/profile. Once a new fault occurs, repeated reports from the prior
120-second operator-validation window cannot clear it: only a strictly higher
operator generation or a fresh positive account_status fact can do so. Older
report_revision values still cannot refresh TTL or clear any fault.

Authoritative pinned sources:
- [Status read parameters](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountRateLimitsParams.ts)
- [Account-bound ordinary usage permission](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountRateLimitsResponse.ts)
- [Implementation and existing-auth handling](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/account_processor.rs)
