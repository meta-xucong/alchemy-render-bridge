# Scope correction: Discord task-message transport

Baseline 1159150c exported local Midjourney task files only. This did not implement
the ordinary Discord message transport required by adapter blueprint 06.

This increment adds an explicitly invoked, disabled-by-default Incoming Webhook
transport on the local Agent. It sends task/attempt identity, the frozen prompt
and parameters, never /imagine interactions or personal-account/selfbot calls.
No real credentials, accounts, messages or generation are used in development.
Bot API support is deferred: the blueprint's preferred Incoming Webhook path is
sufficient for a user-managed ordinary text channel. Forum/thread targets are not
supported. Local export and human original import remain independent.

Before actual use, the operator must approve the target channel/audience and
specific prompt/parameter data to share, configure their existing webhook in
BRIDGE_DISCORD_WEBHOOK_URL on the local Agent, and enable the manual profile.
No credential enters Bridge configuration, API, ledger or log.

Run run-task normally to export. Inspect task.json and its request hash, then:

    python -m render_bridge.agent --state-dir STATE --config CONFIG manual-notify TASK --enable-discord-notifications --request-sha256 HASH

This command is the explicit send option. Merely setting the environment variable,
running run-task or importing an original does not send. Every notification has
full UTF-8 prompt.txt and task.json attachments (including prompt SHA-256), with a
short bounded identity-only summary and mentions disabled. Reference attachments
are not sent; reference-mode tasks fail closed until separately accepted.

The Agent persists notification_unknown before HTTP. Success requires a returned
message ID with wait=true. Timeout, 5xx, unexpected/malformed success and a crash
remain unknown; re-invocation never sends again. 4xx rejection and 429 are recorded
as notification_failed, with bounded Retry-After information if supplied. No
automatic retries, even on 429. Check the channel and use the local export manually
for failures or uncertain delivery; this version intentionally has no resend or
unknown-resolution switch. Do not delete the journal to force resend. Notification
state is separate from manual generation state and does not occupy a physical slot.

Tests mock HTTP only. Real channel authorization/delivery, manual generation,
original import and two-end receipt matching remain local acceptance boundaries.
Existing development SQLite durability and Windows limitations still apply.

Official transport reference checked 2026-10-21:
https://docs.discord.com/developers/resources/webhook#execute-webhook

## Mock validation

`PYTHONPATH=src python -m unittest discover -s tests -v`: 152 passed.
`python -m compileall -q src tests scripts`, contract-schema export and
`git diff --check` pass. Focused notice tests cover full attachments and hashes,
2000-character boundaries, mention suppression, 429/rejection/5xx/redirects,
malformed responses, timeouts/process exit, durable latch write failure,
wrong-task/changed-contract/canceled/disabled guards, restart replay and normal
manual original import. These are not live Discord or Midjourney acceptance.
