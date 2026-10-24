# Source contract v1

Generated from `src/render_bridge/schemas.py` by `scripts/export_contracts.py`.
The schema describes valid wire shapes; the configured runtime capability catalog
is authoritative for which profiles are enabled and their per-profile limits.

- `origin_key` must match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`; other binding values remain opaque.
- `schema_version` is the strict string `"1"`.
- `image.generate`, revision `1`, one `primary` slot. Default enabled profile:
  `fake-image`. `codex-image` and `midjourney-manual` require explicit server and
  Agent configuration; admission does not certify a real model or account.
- Inputs remain PNG/JPEG, at most 1 MiB and 1,048,576 pixels each; up to eight
  references and a 16,000-character prompt. Manual reference flow is not enabled.
- Fake output remains at most 1 MiB / 1,048,576 pixels / 1024 per axis. Other
  registered profiles permit at most 16 MiB / 40,000,000 pixels. Check the
  profile-specific capability limits and provide intended dimensions explicitly.
- `routing` is `bridge_only`; no fallback or automatic alternate provider.
- Source identity is bound by runtime token configuration, never payload owner
  fields. Separate Node and dispatcher endpoints are described in
  `docs/implementation/CONFIGURED_BRIDGE_RUNTIME.md`; source tokens do not authorize them.
- `remote_task_id` and the by-origin route recover create replays. A receipt is
  media acceptance, not Alchemy review approval or verified model provenance.
- Manual tasks expose `awaiting_user` / `action_required`, do not occupy the
  automatic physical slot, and require explicit original-file import.

The generated schema's widest numeric ranges are not a promise that every profile
accepts them. The domain validates the frozen profile and configured capability
before admitting a task. No real generation or production deployment is implied.
