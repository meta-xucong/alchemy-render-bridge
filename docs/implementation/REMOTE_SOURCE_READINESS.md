# Remote source readiness: bounded correction model

2026-10-25. Base: 80f155d4e1217d1f7f9304bfb9154d16071ec67d.

## Intended boundary and defect

VPS Alchemy calls the source HTTPS API; Bridge, Agent and the authenticated CLI
may remain on the user's computer. Bytes, not cross-machine paths, cross the API.
The existing fixed 256 MiB non-fake object budget is too small for sustained use,
while disabling retention indefinitely makes exhaustion inevitable. Increasing a
quota alone does not authorize deleting unknown executions or unacknowledged media.

## Minimal correction

Add an optional strictly bounded object-byte quota to the existing runtime/store,
with legacy defaults unchanged. Publish additive capacity diagnostics through the
already authenticated capabilities response; diagnostics are not an admission
permit. Keep the transaction-serialized immutable write quota and include orphaned
files. Reuse existing ACK/grace-period retention without changing execution,
identity, profile, original bytes, or tombstone semantics. Provide an explicit
remote configuration example and source-only TLS proxy template, never install it
or create credentials automatically.

Do not silently resize references or raise one layer's input limit: API, schemas,
domain and Agent currently agree on 1 MiB / 1,048,576 pixels for each PNG/JPEG
reference. Codex native max-five references is a separate tool constraint. Actual
product media outside those limits needs explicit future end-to-end limit work,
not an undocumented mutation of frozen inputs.

## Validation plan

Reject booleans, floats, strings, zero and oversized quotas. Verify default quota
compatibility, concurrent writes at quota, orphan accounting, immutable retry,
capacity diagnostics and reopening. Run existing retention tests proving unknown
and unacknowledged protection, ACK grace, idempotency and byte reclamation. Validate
remote examples and source-only path routing. Live TLS, NAT, WAN, Windows power-loss,
CLI generation and account/billing acceptance remain separate gates.

## Operator runbook (not an automatic installation)

1. Preserve the current local configuration and state. The existing
   `config/bridge.example.json`, loopback HTTP exception and retention-disabled
   default are unchanged. First test this patch locally as before.
2. For remote staging, copy `config/bridge.remote.example.json` to a private local
   path and select a durable absolute data directory. The example remains
   fake-only, on loopback, with an explicit 4 GiB object budget and 24-hour ACK
   grace. Enable real profiles only after separate real-tool approval/acceptance.
   `max_storage_bytes` accepts an integer from 1 through 1 TiB; omitted/null keeps
   16 MiB fake-only or 256 MiB non-fake legacy defaults. Choose a realistic budget
   with room for input and output plus OS/DB space. No automatic quota growth.
3. Keep three distinct source/node/dispatch credentials, supplied via their named
   environment variables outside Git. Only source credentials go to VPS Alchemy.
   Never copy Codex auth, Veyra secrets or Alchemy end-user sessions into Bridge.
4. Use a stable dedicated HTTPS origin with a publicly trusted certificate (or a
   deliberately provisioned trusted private CA). Alchemy does not accept a path
   prefix, query, userinfo, cross-host redirects or TLS-verification bypass.
   The review-only `config/source-https.nginx.example.conf` shows source-only
   routing to a same-host loopback Bridge; it is not a configured tunnel. A remote
   gateway requires a separately authorized secure tunnel to this computer.
   Obtain authorization before installing gateways, changing firewall rules or
   creating persistent credentials. Do not expose node/dispatch/engine endpoints.
5. The proxy preserves Authorization, PUT/content types, binary bytes and API error
   responses, disables request replay and buffering, and does not redirect to an
   interactive login page. Current reference upload max is 1 MiB; raising proxy
   size alone does not raise Bridge limits. Output downloads may be up to 16 MiB.
   Validate actual proxy syntax and trusted certificate/hostname before launch.
6. Monitor authenticated `/bridge/v1/capabilities` -> `storage`: `used_bytes`,
   `max_bytes`, `available_bytes`, `near_capacity` (80%), `at_capacity`. These are
   cheap tracked-object snapshots, not reserved capacity or a submit permit.
   `includes_orphan_files=false` explicitly means crash-leftover files are excluded
   from diagnostics, avoiding a filesystem walk on every preflight/health poll.
   The write path still scans and counts them, so diagnostics may overestimate
   available bytes; `storage_capacity` and actual disk monitoring remain authoritative. An
   immutable write still checks capacity under the store transaction. The quota
   includes tracked object bytes and orphan regular files but excludes SQLite,
   logs, engine data and arbitrary other directories; monitor filesystem free
   space separately. A smaller quota never deletes old files. `storage_capacity`
   remains a typed 503; never interpret a create timeout as proven rejection.
7. Retention only reclaims acknowledged media after the configured grace, or
   provably unstarted terminal tasks; unknown/running/unacknowledged data stays
   protected regardless of age. It keeps task/receipt/idempotency metadata.
   Persistent `near_capacity` with no eligible data needs operator reconciliation,
   storage expansion or stopping new admission, not deleting ledgers/start latches.
   Back up both the SQLite ledger and artifact directory together; keep endpoint,
   workspace, node and executor identities stable through restart.
8. Before production, test actual VPS -> HTTPS -> local fixture round trips,
   wrong-token/certificate failures, reference hashes, output hashes and ACK;
   upload/download interruption, accepted-create response loss and same-origin
   recovery; local sleep/restart and queue/capacity limits. Then run separately
   authorized real CLI/account/billing acceptance. Synthetic or loopback success
   is not evidence of NAT reachability, Windows power-loss safety or real charges.

No PostgreSQL migration, new credential manager, public deployment or actual
network/security configuration change is included in this patch.
