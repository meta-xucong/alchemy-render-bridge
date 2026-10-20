# Local readiness doctor: bounded implementation plan and contract

This slice adds a local configuration diagnostic, not a renderer acceptance test.
It preserves task schema v1, profile revision 1, the default fake route and exact
output-dimension checks. It does not change Alchemy's native generation path.

## Scope

1. Share strict non-secret Agent configuration between the registry and doctor.
2. Derive an additive version-1 parameter descriptor from each execution profile.
3. Report Bridge configuration, Agent configuration, profile alignment, parameter
   semantics, local CLI availability, and explicitly unverified account/generation.
4. Permit only an explicit isolated version/help probe, never login or generation.
5. Test offline, correct migration documentation, and stop after scoped review.

No network client, database, credential value, saved-account file, executor or
configuration mutation is needed for the default check. Only the two explicitly
selected configuration files and executable metadata are read. The optional
probe creates a temporary empty home and runs version/help, then removes it.

## Command and exit status

```sh
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json --json
# Optional installed-CLI metadata probe; no account operation:
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json --probe-codex --json
```

Exit 0 means the selected local configuration checks passed. It does not mean
that the server is running or a real renderer works. Exit 1 requires attention;
exit 2 is invalid/unreadable configuration (or invalid CLI usage). Real profiles
remain attention until separate local acceptance, regardless of operator flags.
The doctor deliberately never records that acceptance as observed evidence.

## Stable JSON contract

```json
{
  "schema_version": "1",
  "kind": "render_bridge_doctor",
  "producer": {"name": "alchemy-render-bridge", "version": "0.1.0a1"},
  "outcome": "ready",
  "profiles": ["fake-image"],
  "checks": [
    {"id": "bridge_config", "status": "pass", "evidence": "configuration", "code": "bridge_config_valid", "next_step": "Use this configuration only for the bounded development runtime."}
  ],
  "real_generation_verified": false,
  "login_verified": false
}
```

`status` is `pass`, `attention`, `fail`, or `not_checked`. `evidence` is
`configuration`, `local_probe`, `operator_declaration`, or `not_checked`.
`profiles` lists Bridge-enabled IDs when that configuration is valid, otherwise
Agent-enabled IDs if available. IDs and codes are stable snake_case; human text
is not a parsing contract. Config values, paths, credentials and raw CLI output
are not included. A version string matching the bounded `codex-cli` version
pattern may be shown as sanitized local probe evidence. Required fields remain present for invalid configurations.

## Additive capabilities descriptor

Each source capabilities profile gains `parameter_contract` with its own
`schema_version: "1"`; it is independent of task schema/profile revision:

- `accepted_parameters`: `width`, `height`, `seed` only.
- `required_parameters`: empty for fake, `width` and `height` for real profiles.
- `dimension_semantics`: `exact_output_match`, an original-artifact acceptance
  requirement, not a promise that an external image tool obeys the dimensions.
- `executor_control`: `deterministic_fixture`, `text_instruction_only`, or
  `manual_user_selected` for fake, Codex, or Midjourney respectively.
- `seed_semantics`: `accepted_not_applied`, `text_instruction_only`, or
  `manual_user_selected` respectively. Accepting seed is not reproducibility.
- `reference_semantics`: `hash_checked_not_conditioning`,
  `local_image_input_unverified`, or `not_supported` respectively.
- `unsupported_parameters`: `quality`, `output_format`, `input_fidelity`.
  Unlisted parameters are not implicitly supported; the accepted list is closed.

Consumers must not turn operator declarations, installed-CLI metadata or profile
enablement into claims of login, real generation, reference fidelity or quality.
The current Codex lifecycle passes rendering parameters as text instructions,
not structured image-tool controls. Midjourney reference requests remain blocked.
Alchemy's optional preview/fake-native integration exists in its own repository;
native real-profile quality/format/fidelity adaptation is still an implementation
gap. Logging in does not fill that gap or authorize a real render.

## Verification for this slice

Base revision: `be9fe313155f5ddd18815a1d083da7b1b887dbf0`, tree
`e717e60288861503e18b5f53e82d0cc12041d603`. Offline verification on
2026-10-20 passed 123 unittest cases (including 15 new doctor/config/descriptor
cases), compileall, contract export, and whitespace validation. Contract export
made no changes to the existing API/task schemas. Tests verify default diagnostics
make no client, network, subprocess, database, write or credential-access calls;
optional probe behavior is checked with mocked version/help only.

Not run: a real installed Codex probe, account login, actual provider generation,
real Midjourney/Discord use, Windows-local acceptance or production deployment.
No branch was merged, pushed, or deployed as part of this slice.
