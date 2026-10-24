# Alchemy read-only integration preflight

- Date: 2026-10-20 UTC
- Target: `meta-xucong/alchemy-media-agent`, current `main`
- Fixed revision: [`df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157`](https://github.com/meta-xucong/alchemy-media-agent/commit/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157)
- Previous baseline: the same revision; no baseline drift was found.
- Method: authenticated, read-only GitHub source inspection. Main was checked before and after inspection.

## Decision

**The inspected Alchemy API does not already provide a supported generic Render Bridge import contract or an independent optional Bridge module. Native Alchemy registration is BLOCKED pending an approved Alchemy-side extension.**

There are useful existing V3 frozen-input and MCP checkpoint primitives. They are internal or local-only, tied to the existing V3/Codex materialization flow, and do not establish the generic import guarantees required by the shared Bridge contract.

This does **not** block the standalone Bridge server, simulated Alchemy caller, or FakeRenderer work. Those can use the version-neutral shared API. A module-only preview must remain explicitly unregistered until the native import gate passes.

No Alchemy files, jobs, outputs, databases, deployment settings, or provider state were changed. No runtime was started, no paid provider was called, and no user computer or local Codex was used.

## Scope and evidence

Read Alchemy `AGENTS.md` and these relevant files at the fixed revision:

| Short name used below | Exact repository path |
|---|---|
| `main.py` | `src_skeleton/app/main.py` |
| `routes.py` | `alchemy_creative_agent_3_0/app/app_shell/routes.py` |
| `route_handlers.py` | `alchemy_creative_agent_3_0/app/product_api/route_handlers.py` |
| `providers.py` | `alchemy_creative_agent_3_0/app/generation_router/providers.py` |
| `mcp_materialization.py` | `alchemy_creative_agent_3_0/app/generation_router/mcp_materialization.py` |
| `outputs.py` | `alchemy_creative_agent_3_0/app/product_api/outputs.py` |

The recursive Git tree was not truncated. Every downloaded file's Git blob hash matched that tree. All six Python files parsed with `ast.parse`; they were not imported or executed. Conclusions about absent hooks are limited to this inspected composition and V3 surface, not a claim about every subsystem or unpublished branch.

## 1. Optional module mount: a small host addition is needed

- `main.py:137` constructs the FastAPI application directly.
- `main.py:290–307` eagerly constructs the V3 service and route-handler graph.
- `main.py:435–436` mounts static applications. Static AST inspection found these two mounts and no `app.include_router` or `app.add_api_route` calls in this file.
- The same inspection found 62 direct `/api/v3/…` route declarations. `routes.py:6–43` lists the V3 route contract, including only GET/submit for MCP materialization at lines 29–30.
- `route_handlers.py:50–86` supports injection of existing V3 service/project/asset hosts. It is not a general optional integration registration interface.

Sources: [application composition](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/src_skeleton/app/main.py#L290-L307), [route contract](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/app_shell/routes.py#L6-L43).

**Finding:** no existing Bridge-specific optional mount, disabled/enabled/draining lifecycle, independent integration-job store, or bounded Bridge reconciler was established by the inspected source. FastAPI permits an additive mount in principle; that is a proposed host change, not an already-present supported module seam.

## 2. Frozen inputs: an Alchemy-owned primitive exists

- `providers.py:242–266` defines `ProviderPromptMaterialization`: final prompt, prompt hash, size/quality/format, resolved references, asset plan, and image options.
- `providers.py:269–287` defines `build_provider_generation_request`, expressly shared by the Brain and non-rendering adapters so the frozen envelope is not omitted.
- `providers.py:2502–2532` defines `materialize_final_prompt` before provider selection/calling, and resolves the actual provider-input reference files rather than an earlier reference list.
- `mcp_materialization.py:576–705` freezes operation, prompt/hash, ordered reference hashes/semantics and rendering-contract fingerprint into a durable handoff.
- `mcp_materialization.py:755–793` projects the canonical renderer request. The caller is expressly forbidden from composing a second prompt itself.

Sources: [frozen request primitives](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/providers.py#L242-L287), [materialization boundary](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/providers.py#L2502-L2532), [frozen handoff](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/mcp_materialization.py#L576-L705).

**Finding:** this is a suitable authority to preserve on the Alchemy side. It is not a version-neutral remote Bridge input API:

- `route_handlers.py:983–987` exposes internal handoff/read helpers; the public HTTP composition mounts only the existing handoff GET and submit, not a generic freeze endpoint.
- `mcp_materialization.py:1341–1389`, particularly line 1377, includes local `file_path` references.
- `providers.py:8221–8234` freezes this MCP renderer as `codex_builtin_imagegen`, model `gpt-image-2`, count 1, with its current image options.

A future Alchemy-side adapter must obtain an authorized final request and transfer its references through Bridge's input API. Bridge must not import these Python classes, read Alchemy paths, infer V3 lineage, or reproduce its prompt/reference rules.

## 3. Existing import: local MCP submission, not generic native registration

The concrete HTTP surface is:

- `GET /api/v3/creative-agent/mcp-materializations/{handoff_id}`
- `POST /api/v3/creative-agent/mcp-materializations/{handoff_id}/submit`

Both call `_require_local_mcp_materialization` at `main.py:2221–2231`. That guard accepts only loopback client addresses and otherwise returns HTTP 403 (`main.py:801–807`). Do not weaken, proxy around, or repurpose this guard for Bridge.

`route_handlers.py:989–1042` accepts nonce, prompt/reference hashes, optional renderer receipt hashes, and exactly one `artifact_base64` or local `artifact_path`. It submits bytes into an **existing handoff**. It does not accept a generic Bridge receipt/import key and return a native registration reference.

`providers.py:7632–7642` describes `McpMaterializationProvider` as a local Codex/MCP transport adapter inheriting the ordinary V3 provider pipeline. `providers.py:8899–8921` creates/loads and consumes its handoff; lines 8964–8988 return provider-shaped output fixed to `codex_builtin_imagegen` / `gpt-image-2`. Submission alone is therefore not the whole native output/candidate/review/selection lifecycle.

Sources: [loopback guard](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/src_skeleton/app/main.py#L801-L807), [HTTP routes](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/src_skeleton/app/main.py#L2221-L2231), [submit handler](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/product_api/route_handlers.py#L989-L1042), [existing transport](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/providers.py#L8883-L8989).

**Finding:** neither opening this endpoint to a remote VPS nor wrapping its private store is an approved generic importer. The local restriction alone is not the entire blocker: the contract also lacks the required generic receipt-to-native-registration boundary.

## 4. Idempotency exists, but only within its current authority

| Existing protection | Exact evidence | Limit for Bridge |
|---|---|---|
| Cross-process handoff read/check/write locking | `mcp_materialization.py:523–574` | Protects this handoff store; does not establish an end-to-end generic import transaction. |
| Same submitted content replays; different content conflicts | `mcp_materialization.py:974–1046` | Keyed to an already-issued MCP handoff and nonce. |
| Artifact hash and expected native checkpoint validation | `mcp_materialization.py:1084–1158` | Checkpoint fields include V3 `job_id`, `candidate_id`, `output_id`. |
| Ordered output/job checkpoints without rollback | `mcp_materialization.py:1160–1285` | Native V3 recovery semantics; not Bridge delivery acknowledgment. |
| Server-owned deterministic MCP output ID | `providers.py:8666–8706` | Identity includes Codex/model and V3 prompt/candidate lineage. |
| Reuse existing output ID only for matching native binding/content | `outputs.py:86–135` | Internal storage API; a missing output ID creates a new UUID. |

Sources: [handoff submit/replay](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/mcp_materialization.py#L974-L1046), [checkpoint persistence](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/generation_router/mcp_materialization.py#L1084-L1285), [output storage](https://github.com/meta-xucong/alchemy-media-agent/blob/df3e10fb4d4bb2eb346651cf8d5c3e92bbc2a157/alchemy_creative_agent_3_0/app/product_api/outputs.py#L86-L135).

`providers.py:1370–1380` explicitly keeps persisted output identity server-owned; lines 1471–1479 record the output checkpoint after storage. Bridge must not generate these IDs or call `save_base64_output` directly. Replaying the same Bridge receipt 100 times with one native result and one usage event remains **unverified and blocked**, not passed by analogy with these primitives.

## 5. Authorization and native lifecycle gates

Existing routes show reusable Alchemy-owned patterns:

- Server-derived owner injection: `main.py:655–662` and `2298–2304`.
- Existing project-visibility check: `main.py:3196–3203`.
- Existing output reads use visibility checks: `main.py:2263–2295`.
- Authentication has a configured local/disabled mode: `main.py:2559–2562`. A new remote integration must explicitly define its trusted mode rather than assume all installations authenticate identically.

These patterns are evidence of existing authority, not proof of a supported importer. This scoped audit did **not** verify a generic import path covering current permission revocation, cancellation/deletion/version changes, native review-only/selection/delivery rules, or exactly-once usage/billing. The loopback submit route cannot supply that proof.

## 6. Minimal Alchemy-side proposal, requiring approval before implementation

1. Add one explicit optional-module mount/configuration boundary. Keep it disabled by default; avoid Bridge client creation, configuration requirements, or reconciler startup while disabled. Use independent integration records and bounded resources. Draining accepts no new tasks and only reconciles this module's records.
2. Define a supported Alchemy-owned frozen-input facade. It checks the current actor/project and returns a final immutable rendering request plus authorized reference transfer handles. V3-specific translation stays in this facade.
3. Define a supported native registration facade and lookup by a stable, scoped import key. It owns native IDs and binding; verifies receipt/hash, current permissions and target revision; preserves cancellation/deletion; and applies existing review, selection, delivery and usage rules. It must distinguish registered, rejected, and outcome-unknown.
4. Persist the same receipt/import key through response loss. Query before any retry whose effect is unknown. Only confirmed native registration permits an accepted Bridge acknowledgment. Keep the receipt and remain `import_unknown` otherwise.
5. Test module-disabled startup, no-config startup, Bridge outage, and unchanged default endpoints. Then test cross-scope denial, late result after cancellation/deletion, changed revision/permissions, content conflicts, response-loss reconciliation, and 100 repeated receipts with one native registration/usage effect.

This proposal does not authorize changes to Alchemy, a provider default, the Brain, billing, review, global queues, or native job recovery. It does not prescribe an unverified public route name. The owning Alchemy implementation must settle the supported facade and tests before native joint acceptance.

## Verification receipt

- PASS: main revision resolved twice and remained at the stated SHA.
- PASS: seven source files matched Git tree blob hashes; six Python files parsed statically.
- PASS: scoped route inventory and code trace completed without executing Alchemy.
- BLOCKED: generic frozen-input export/native import and optional-module acceptance are not established by the existing inspected API.
- NOT RUN: unit/integration/UI/regression tests, real native import, billing/review acceptance, deployment, provider calls, Windows/Codex or user-computer validation.
- Changed scope: this Bridge documentation file only. No commit, push, or deployment.
