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
