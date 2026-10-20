# Experimental source contract v1

Generated from `src/render_bridge/schemas.py` by `scripts/export_contracts.py`.
This is the implemented FakeRenderer contract slice, not a claim that all planned
operations, authentication or engine integrations are available.

- `origin_key` must match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}` so every accepted key can be recovered through the by-origin route. Other binding values remain opaque.
- `schema_version` is the strict string `"1"`.
- Only `image.generate`, profile `fake-image`, revision `1`, one `primary` slot.
- PNG/JPEG inputs and synthetic PNG output; at most 1 MiB and 1,048,576 pixels each. Output dimensions are limited to 1024 per axis; input dimensions are constrained by the total pixel limit. At most eight references and a 16,000-character prompt.
- `routing` is `bridge_only`; no fallback, Discord, Codex or real video calls.
- Source identity is supplied by the test application's injected token map,
  never by payload owner fields. These fixture tokens are not deployment credentials.
- Responses expose `remote_task_id`; the origin lookup can recover a lost create
  response. A receipt represents media acceptance, not Alchemy review approval.
- Node claim/start/upload methods are harness-only Python calls. No node routes,
  background dispatcher or network listener is supplied by this stage.

The generated schemas and fixtures preserve fields exactly; planned conceptual
names in earlier design prose do not authorize silently accepting aliases.
