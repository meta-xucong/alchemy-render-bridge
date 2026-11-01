> Historical liveness slice: profile-aware health, actual executor errors, optional account status and safe withdrawal were added later. See [PROFILE_HEALTH_RUNTIME](PROFILE_HEALTH_RUNTIME.md), [08 contract](../shared-contract/08_自动回退代码落地与兼容契约.md) and [current acceptance](AUTO_FALLBACK_ACCEPTANCE.md). Liveness alone still never proves real generation.

# Single-node readiness, task attention and admission control

This additive API slice keeps schema/profile revision 1, the fake-only default,
existing credentials and the single Hatchet dispatcher. No login, real generation,
new credential, external notification or production deployment is performed.

## Public capabilities

`GET /bridge/v1/capabilities` now adds:

- `admission`: `accepting_new_tasks` boolean and `mode` enabled/draining.
- `node_status`: state unknown/offline/available/busy/attention, `last_seen_at`
  (server timestamp or null), `heartbeat_timeout_seconds: 90`, `reported_profiles`,
  and always-false `login_verified` / `real_generation_verified`.
- Each existing profile adds `availability` using the same state vocabulary.

No report means unknown; reports at least 90 seconds old mean offline. A fresh
configured persistent worker can report available, an active/unresolved automatic
execution reports busy. Missing configured profiles and Codex pending local real
acceptance report attention. Available is worker liveness/configuration evidence,
not a successful render, account check or end-to-end Hatchet scheduling proof.
An explicit one-shot run does not leave the node advertised as available after it
finishes. Older servers without these fields must be shown as unknown by clients.

The authenticated Node API accepts `POST /bridge/node/v1/heartbeat` with state
available/busy/attention and a bounded known profile list. Identity is derived from
its existing token. The Hatchet worker reports every 30 seconds while alive, with
90-second freshness. This is a liveness loop, never a task poller or dispatcher.
Failed heartbeat delivery is best-effort and freshness eventually expires.

## Task attention

`POST /bridge/node/v1/tasks/{id}/attention` accepts `attempt_id` and an allowlisted
`code`. Source task responses add `action_required_detail: {code,message}` or null.
The existing `action_required` field stays unchanged (manual import string/null).
Messages are server-owned safe text, never CLI output, paths or credentials.

Pre-start failure persists awaiting_user and a reason; it does not obtain a start
grant. Post-start uncertainty stays execution_unknown and keeps the physical slot.
Canceled/terminal tasks are not revived. Start, receipt and attempt identities are
unchanged. A successful resumed start clears prior pre-start attention. If reporting
itself fails, the local result exposes `attention_reported: false`; it never starts
a new generation to repair a reporting failure.

## Drain and same-attempt recovery

Set `accepting_new_tasks: false` in the existing Bridge runtime config and restart
the configured server. New origin keys receive `503 bridge_draining`; exact task
replays, task/receipt reads, existing claims/execution, uploads and acknowledgments
remain available. This is an admission switch, not cancellation or a worker stop.
Restoring true reopens admission. Alchemy's independent module toggle must also
preserve reconciliation for previously submitted work.

For pre-start attention: fix only the named configuration/disk prerequisite, reuse
the original state directory and task ID, then explicitly invoke:

    python -m render_bridge.agent --state-dir ./local-agent-state --config ./agent.local.json run-task TASK_ID

This existing bounded operator command handles one task and uses the same attempt.
For completed local evidence/upload failure the same command recovers/reuploads the
same original. If the attempt is incomplete/unknown it remains unknown and does
not launch another turn. Do not delete the journal, change origin keys, reset the
start latch or add a parallel poller. Lost engine handoff after acknowledgment needs
this explicit inspection/recovery; automatic engine reconciliation remains absent.
Manual tasks continue through the existing explicit manual-import command.

## Verification boundary

Offline tests cover absent/fresh/stale/scoped heartbeat, mismatched profile,
metadata-only reporting, persistent pre/post-start attention, same-task resume,
manual backward compatibility, drain replay/upload/ack and bounded intervals.
No real engine, account, provider, Windows runtime or dual-VPS link is verified here.
Full remaining blueprint gaps are recorded in DELIVERY_GAP_AUDIT.md.
