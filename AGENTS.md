# Development boundaries

Read README.md and docs/README_开发边界与文档导航.md before implementation.

- This repository owns the standalone Render Bridge program. Alchemy integration code belongs to the relevant Alchemy repository.
- Use the shared versioned HTTP API. Never access Alchemy databases or private storage directly.
- Preserve the existing Alchemy default API paths, billing, review, selection, delivery and recovery semantics.
- Missing Alchemy extension hooks require a scoped proposal and approval, not an implicit core refactor.
- Bridge first: one trusted workspace/node and FakeRenderer before real rendering adapters.
- Use one authoritative dispatcher. Hatchet remains a candidate until its documented gates pass.
- A retry, expired lease or cancellation request does not prove an external execution has stopped. Keep unknown execution fail-closed.
- Do not include credentials, production data or generated private media in commits.
- Record exact source revisions, tests, skips and unverified scope. Design review is not runtime acceptance.
- Do not merge, deploy, create persistent credentials or invoke paid providers without the applicable authorization.

- Alchemy and Render Bridge remain independent services. The current user-authorized first test runs both services and the local Codex CLI on the same Windows computer, through loopback HTTP with independent state directories. Separate VPS deployment remains a later option. No shared database, storage mounts, internal imports or coordinated-release requirement for compatible API versions. Remote/non-loopback deployment retains TLS and production security gates.
- Keep Alchemy-version-specific adaptations on the Alchemy side. Bridge consumes the same final rendering contract.
- First real adapters: locally authenticated Codex CLI and Midjourney human submission/import, with Discord official API/Webhook for ordinary task messages. Do not substitute keyboard/mouse automation or claim ordinary messages invoke /imagine.
- The project license decision is deferred for this experiment; retain and respect third-party license notices and service restrictions.
