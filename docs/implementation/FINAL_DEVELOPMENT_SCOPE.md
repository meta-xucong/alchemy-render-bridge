# Final development scope and machine acceptance handoff

Audit date: 2026-10-21. Code inspected: Bridge `de072bc`, Alchemy `6a56652a`.
The bounded code audit is closed with the corrections and local verification
recorded below. This is not real-tool or deployment acceptance. No real account,
Discord message, paid generation, physical Windows execution or deployment was
run for this update. GitHub checks on the published revision remain the separate
CI authority; earlier green runs must not be substituted for them.

## Authoritative first-delivery boundary

The total bounded objective is an explicitly selected Alchemy Bridge path through
canonical frozen input, independent Bridge execution, native registration and
shared review, with the original default paths unaffected. The current UI admits
V3 Standard/general, one image, the supported sizes/formats and explicit profiles.
Review-only results cannot be selected/exported as formal delivery. Completing a
frontend phase alone does not prove this whole path.

Authority: `docs/README_开发边界与文档导航.md`,
`docs/render-bridge/01_Render_Bridge程序开发方案.md`,
`docs/alchemy-integration/02_Alchemy新增接入模块开发方案.md`, and
`docs/shared-contract/03_双方API与联合验收契约.md`.
The integration blueprint sections 2–3 explicitly permit an independent entry;
ordinary Generate/Provider integration is conditional later work. Section 8's
version neutrality is not an instruction to implement every Alchemy version now.
The original test-image acceptance excludes formal export and memory promotion.

## Completion / deferred / acceptance matrix

Paths below are relative to the named repository, not shared runtime imports.
“Implemented” means code exists, not that the final revision or real tool passed.

| Area | Current classification | Exact implementation / evidence sources |
|---|---|---|
| Versioned source API, input binding, single execution, receipts and ACK | Implemented; final offline suite passed | Bridge `src/render_bridge/api.py`, `domain.py`, `schemas.py`; `tests/test_api.py`, `test_domain.py`, `test_fake_e2e.py` |
| Configured server, one-node Agent, persistent journal and upload-only recovery | Implemented; real-machine recovery unverified | Bridge `runtime.py`, `node_api.py`, `agent/runner.py`, `agent/journal.py`; `tests/test_configured_e2e.py`, `test_agent_runtime.py`, `test_agent_profiles.py` (source paths under `src/render_bridge/`) |
| One authoritative Hatchet dispatcher/worker | Implemented adapter and isolated sidecar dependency recipe; clean Linux SDK smoke passed; remote TLS/permissions/physical Windows gates remain | Bridge `src/render_bridge/agent/hatchet_sidecar.py`; `tests/test_agent_dispatch.py`, `test_configured_worker_callback.py`; `WSL_NATIVE_ENGINE_VALIDATION.md` and `HATCHET_POC_GATE.md` contain dated evidence, not final acceptance |
| Readiness, attention, draining, persistent node revoke/reactivate, bounded events | Implemented initial trusted-node operations | Bridge `src/render_bridge/readiness.py`, `operational_store.py`, `operations.py`; `tests/test_readiness.py`, `test_node_operations.py` |
| Acknowledged media retention and explicit Agent cleanup | Implemented, opt-in/explicit; protects unknown and unacknowledged copies | Bridge `src/render_bridge/domain.py`, `agent/maintenance.py`; `tests/test_retention.py`, `test_agent_retention.py`; `RETENTION_CORRECTION_SCOPE.md` |
| Codex CLI lifecycle and exact original collection | Implemented protocol adapter; real login/image/tool-policy capability unverified | Bridge `src/render_bridge/executors/codex.py`; `tests/test_executors_codex_lifecycle.py`; `OFFLINE_EXECUTORS.md` |
| Manual MJ export/import and official Discord task notification | Implemented; actual channel/send/manual generation unverified | Bridge `src/render_bridge/executors/manual.py`, `agent/discord_notice.py`, `agent/runner.py`; `tests/test_discord_notice.py`, `test_agent_profiles.py`; `DISCORD_TASK_NOTICES.md` |
| Alchemy default-off module, HTTP client, mapping/reconciliation, admin view | Implemented; preserve original routes and rerun regressions | Alchemy `src_skeleton/app/integrations/render_bridge/` (`client.py`, `service.py`, `native_service.py`, `store.py`, `admin.py`); `tests/test_render_bridge_*.py` |
| Canonical plan/freeze, native receipt, same-output review recovery | Implemented bounded V3 path, not formal selection/export/billing | Alchemy `alchemy_creative_agent_3_0/app/product_api/render_bridge_native.py`, `render_bridge_facade.py`, `app/generation_router/render_bridge_provider.py`; `docs/render_bridge_module/RB07_REVIEW_RECOVERY.md` |
| Current-project trial dialog and review preview | Implemented; final browser fixtures/screenshots and live-app acceptance distinct | Alchemy `src_skeleton/app/static/v3-bridge-trial.js`, `v3-bridge-trial.css`; `tests/browser/test_render_bridge_v3_trial_ui.py`; module `docs/RB08_V3_TRIAL_ENTRY.md` |
| HTTP throttling semantics | Fixed and regression-tested | Alchemy `client.py` parses numeric/HTTP-date Retry-After for 429/503; shared coordinator/store persist a nonblocking server deadline across restart/cancel. See module `docs/RB09_RETRY_AFTER.md` |
| Full file streaming | Partial bounded implementation, not literal full blueprint coverage | Bridge `api.py:_bounded_body` buffers the capped body; artifact response chunks an already loaded object; `agent/runner.py` reads capped originals. Current limits are 1 MiB input and up to 16 MiB output. Do not label this full streaming or silently expand to unlimited media |
| Execution lease renewal | Deliberate current limit, not new scheduler work | Bridge `node_api.py` issues fixed 3600-second claims; `agent/runner.py` recovers output using upload-only claims. Unknown execution never becomes permission to start again |
| PostgreSQL, self-service pairing, multi-node management | Deferred platform/production expansion | Blueprint recommends PostgreSQL; initial one trusted workspace/node uses SQLite and configured credentials plus persistent authorization. No production scalability claim |
| Ordinary Generate, V1/V2/Lab/Professional, video, ComfyUI, formal delivery and real billing | Outside current first-delivery boundary | Integration blueprint sections 3/8; explicit Comfy/video deferral; Alchemy `RB08_V3_TRIAL_ENTRY.md`, `docs/render_bridge_module/RB06_NATIVE_OPTIONS_CORE.md` |
| Windows sleep/crash/disk, real account images, remote TLS, separate VPS joint path | Machine/deployment-only acceptance, not proof obtainable from mocks | Bridge blueprint sections 9–10, shared contract section 7, adapter docs under `docs/render-bridge/adapters/`; requires separate authorized setup and execution |

## Repeatable safe cloud verification

Use an already provisioned isolated Python 3.12+ environment with the pinned test
and runtime requirements. The commands below do not install software, configure
real credentials, contact providers or deploy anything. Run from the Bridge root;
all generated evidence goes outside the tracked tree. The configured smoke uses
synthetic data, loopback HTTP and fixture credentials, not real Hatchet or accounts.

```sh
export PYTHONPATH="$PWD/src"
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/export_contracts.py
git diff --exit-code -- contracts
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json --json
EVIDENCE_ROOT="$(mktemp -d)"
python scripts/run_configured_demo.py --output-dir "$EVIDENCE_ROOT/configured-demo"
cat "$EVIDENCE_ROOT/configured-demo/result.json"
git diff --check
```

Doctor may report attention for unaccepted real tools. That is expected and must
not be “fixed” by fabricating acceptance flags. The smoke's output directory must
not already exist. Keep its result with the exact tested SHA; successful fake
output is not a real image-generation receipt.

For Alchemy, the reproducible isolation/environment and exact selections are in
`.github/workflows/render-bridge-regressions.yml` at the tested SHA. Run its
`contracts` and `preview-browser` jobs in a disposable runner with their declared
empty provider credentials and isolated storage roots. Do not copy only the pytest
command into a logged-in production environment. The jobs include the Bridge
contracts, narrow original-path regressions, shipped UI synthetic browser fixtures,
JUnit reports and screenshots. CI browser fixtures do not replace real login and
full-app/manual acceptance. Record both job results and inspect screenshots.

## Minimal final sequence and stopping condition

1. Resolve confirmed normal-path/runtime audit findings, including the SDK-sidecar
   dependency recipe in `experiments/hatchet-windows-compat/requirements-sidecar.txt`
   (SDK environment uses source PYTHONPATH, not the main Bridge distribution)
   and the throttling
   mismatch, without adding unrelated platforms or new global business semantics.
2. Run Bridge tests and configured fake smoke, plus Alchemy contract/original-path
   and browser selections on the final integrated SHAs. Record failures/skips and
   distinguish baseline failures from new ones. Keep PRs unmerged unless authorized.
3. Keep this matrix and historical documents reconciled with those results.
   Each required code item must be implemented and tested, or an explicit bounded
   limitation recorded; do not call literal entire-blueprint/production completion.
4. Stop development scope expansion when the supported one-image path and its
   normal recovery are green. Hand off an exact-version machine checklist for
   Codex and manual MJ/Discord, Windows recovery and independent HTTPS deployment.
   Those tests may expose real compatibility fixes; passing mocks cannot promise
   that no such fixes will be needed.

### Integrated local verification, 2026-10-21

- Bridge code/test revision `b9dab41`: **187 tests passed** in 16.077 seconds;
  schema export reproduced without changes; whitespace check passed.
- Alchemy revision `901a9a37`: **316 tests passed** in 98.02 seconds using the
  workflow's scoped contract/original-path selection in a credential-free isolated
  environment. Independent reviewer additionally passed 72 focused cases.
- Clean SDK environment: official Linux Hatchet 1.42.1, Pydantic 2.14.1,
  httpx 0.28.1 and Pillow 12.3.0; no FastAPI or main Bridge distribution.
  `pip check` and all three sidecar import/help/dependency smoke cases passed,
  also independently reviewed. This did not start a live Hatchet service.
- Configured independent server/Agent HTTP smoke passed: one fake execution,
  original receipt/ACK on restart/replay, manual import after restart, manual
  waiting does not occupy the automatic execution slot. Processes stopped.
- Actual Alchemy `app.main` plus independent Bridge HTTP and NodeAgent/FakeExecutor
  passed on Alchemy `901a9a37` and Bridge `d923930` (runtime identical to
  `b9dab41`, whose changes are dependency recipe/docs/tests). Admin lifecycle,
  interrupted same-output review recovery, ACK after native registration,
  owner-authorized image preview, withheld selection/export, process restart and
  one task/attempt/receipt/ACK/output all verified. Authentication, Brain and review
  used explicit synthetic doubles; no charge or real provider. All processes stopped.
- Shipped browser assets are unchanged from remote Alchemy `7c00eb3b`, whose
  46 browser cases and screenshots passed previously. The final publication's
  browser and Windows CI must be checked separately; these local results do not
  predeclare them green.

This document-only receipt follows those code/test revisions. Remote commit IDs
can differ from local commits while their Git trees match; the PR/release handoff
records the actual published revisions and final CI results.

