# Bounded single-node operations scope

Base: integrated Bridge 5e3003f. The blueprint explicitly includes immediate node
revocation and audit events. This slice implements those basics without a new
scheduler, remote administrator API, credential service or pairing platform.

- Existing configured source/workspace/node/executor bindings provision persistent
  active records once. Restart does not reactivate revoked nodes.
- Node bearer authorization checks that registry on every request; claim/start and
  upload transactions also check activity at the grant boundary.
- A local operator CLI targets only its configured registered node. It uses local
  operator filesystem authority, not a broader dispatcher bearer, and never reads
  or creates credentials. No actual deployment or real node is changed here.
- A latest-1000 metadata-only event ring records meaningful transitions, preserving
  transaction semantics. Queries return at most 100 events. No prompt, origin key,
  binding ID, client reason, credentials, paths or image bytes enter events.
- Revocation blocks future node authorization, not external execution already
  issued. Source read/download/ack and immutable execution evidence remain intact.
- Static identity provisioning is the current trusted-single-node pairing method;
  self-service pairing is not implemented. This is not a multi-node management UI.

Acceptance: revoked survives restart; no revoked claim/start/upload; source
receipt read/ack unaffected; reactivation never resets attempts; safe idempotent
bounded audit; no credentials accessed by operator CLI. Fixtures only.

## Operator usage

Run on the Bridge host under the existing operator account with its private
configuration. The database must already exist. These commands use the configured
identity and local database access; no bearer values, account files or credentials
are read or generated, and there is no new remote administrator endpoint.

    python -m render_bridge.operations --config bridge.local.json node-status
    python -m render_bridge.operations --config bridge.local.json revoke-node
    python -m render_bridge.operations --config bridge.local.json reactivate-node
    python -m render_bridge.operations --config bridge.local.json audit --limit 50

Revoke takes effect on the next authorization and at the serialized claim/start/
upload transaction boundary. Work authorized before revocation may still run
externally. Reactivation restores the same identity only; it never resets grants,
replaces attempts, rotates credentials or regenerates images. Startup registration
uses insert-if-absent, so a service restart preserves revocation. Source API reads
and receipt acknowledgment do not depend on the node's active status.

Capabilities node_status adds authorization active/revoked/unknown; revoked is
shown as attention rather than available. No login or generation evidence changes.

Audit kinds: node_configured, node_revoked, node_reactivated, task_created,
start_granted, manual_ready, execution_unknown, task_attention, cancel_requested,
canceled, receipt_committed, delivery_accepted, delivery_rejected. Stored payloads
contain server-owned task/attempt/receipt IDs, a timestamp and optional allowlisted
attention code only. Replays do not duplicate outcome events. Retention is a global
latest-1000 ring and queries are scope-filtered; this is operational history, not
an immutable compliance archive. Polls/heartbeats and arbitrary external errors
are not recorded. The event records cannot reconstruct prompts or media.

## Offline verification

182 unittest cases passed (176 integrated baseline plus 6 new operations cases);
compileall, unchanged contract export and whitespace validation passed. The CLI
help and fixture commands were exercised; no actual node was revoked/reactivated,
no real credentials were accessed, and no service was deployed. Existing Starlette
TestClient deprecation warnings remain unchanged.
