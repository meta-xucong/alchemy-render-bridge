# Release Readiness Gap Closure

Date: 2026-11-14
Status: code and contract tests are being completed on an isolated feature branch. This document is not evidence of a published or deployed release.

## Scope and correction model

The previous readiness review found three actionable gaps and two intentional
scope limits. This document owns the Bridge-side corrections; Alchemy account
binding correction is specified in its own repository at
`docs/render_bridge_module/RB35_NODE_REWARD_BINDING_CORRECTION.md`.

1. Contract CI installed a locked runtime/test environment but ran
   `unittest` against a test module that imports `pytest` and contains
   pytest-only function tests. The corrected runner installs the pinned pytest
   test dependency and invokes pytest on the supported `tests/` suite, which
   collects both unittest classes and pytest-style tests. The separate
   `experiments/` tree has environment-specific integration experiments and is
   not part of the cross-platform contract job.
2. The anonymous, redacted fleet status response did not report which source
   revision built the running image. The container build now accepts an exact
   source revision and the existing status response includes only that
   validated revision (or `unknown`). This does not expose node, task, account,
   prompt, media, or credential data.
3. The deployment runbook must pass the checked-out commit to the image build
   and compare the running status revision to the intended release before
   claiming version consistency.

The reference VPS observation available for this audit was a successful
redacted status request with one offline node and no online/free nodes. The
status response did not include a release revision, so the running commit
could not be established from that endpoint. This is an operational snapshot,
not a multi-node acceptance run.

## Product and architecture limits

- The configured `0.02` per-image reward is a reporting calculation. It does
  not create Alchemy spendable balance, generation credits, invoices, or
  payouts.
- Shared-pool scheduling supports multiple worker machines against one
  SQLite-authoritative Bridge service. Multiple Bridge API replicas and
  high-availability failover are outside this release; PostgreSQL and its
  migration/operations design are prerequisites before changing that boundary.
- A successful FakeRenderer suite proves scheduler and contract behavior. It
  does not prove real provider quality, authenticated Codex execution, or that
  two independently enrolled real machines are online.

These are explicit scope boundaries, not fixes to add to this change.

## API contract

`GET /bridge/status/v1` keeps its existing redacted shape and adds:

```json
{"release_revision":"<40 lowercase hexadecimal characters or unknown>"}
```

The field is derived from the image build argument
`RENDER_BRIDGE_BUILD_REVISION`. Invalid values are returned as `unknown`.
No request parameter can override it. The field identifies source code only;
it does not attest to runtime configuration, node health, or successful image
execution.

## Build and release acceptance

Build an image with the exact commit that was reviewed:

```sh
docker build \
  --build-arg RENDER_BRIDGE_BUILD_REVISION="$(git rev-parse HEAD)" \
  -f deploy/docker/Dockerfile \
  -t alchemy-render-bridge:<short-commit> .
```

Before claiming deployment consistency, the authenticated operator must verify
the immutable image tag and compare the live `release_revision` with the
reviewed 40-character commit. `unknown`, a mismatch, or an unreachable status
endpoint leaves the deployed revision unverified. The version field alone is
not a substitute for the existing health, data, migration, or rollback gates.

Required automated evidence:

- pytest is installed from the locked test dependency set;
- CI runs `pytest -q tests` on Ubuntu and Windows, then compile and whitespace
  checks. This includes all supported Bridge tests without collecting the
  environment-specific experiments tree;
- tests cover the exact revision, invalid/missing revision, and continued
  public-status redaction;
- the existing shared-pool, lifecycle, privacy, concurrency, and migration
  tests pass on both CI operating systems.

Required live operational evidence before calling the fleet ready:

- running revision equals the reviewed release commit;
- the expected node count is online and has eligible free slots;
- a bounded, synthetic FakeRenderer concurrency acceptance passes on an
  isolated candidate ledger;
- no real provider task is created as a deployment probe.

Until those checks are recorded, report the affected phase as code/CI-ready
only and keep the live multi-node capability unverified.
