# Security, Privacy, and Data Migration

## Trust boundaries

A shared pool deliberately crosses workspace boundaries only for execution. The workspace submits a frozen task to Bridge; Bridge chooses a consented node; that node can see only the assigned prompt and reference inputs needed to execute it; the resulting media is uploaded to Bridge and remains visible to its owning workspace. Pool membership never grants a node a workspace-wide token or lets a user browse another tenant's task list.

Both sides opt in for future work: the workspace accepts a pool invitation; the node operator enrolls with a pool-specific code after a data-sharing disclosure. Revoking either side stops new assignments. Already-started and unknown tasks remain pinned and are reconciled without cross-node rerender.

## Credentials and browser

Generate random operator, node and invite credentials with the standard cryptographic library. Persist only SHA-256 hashes for high-entropy dynamic bearer tokens and one-use codes; do not use human passwords as invitations. Reveal a node/source token once. Runtime access logs are disabled by the existing service unit; application error responses never reflect request values. No invitation or bearer is accepted in a query parameter.

Use the existing TLS ingress for remote console/API access. Serve console assets same-origin with a Content Security Policy that blocks remote scripts, plus nosniff, no-store, bounded request sizes and `textContent` rendering. Use explicit bearer headers rather than ambient cookies so cross-site request forgery cannot authorize control actions. One-use invitation entropy is generated with the cryptographic random source; an authentication rate limiter is not part of this first release.

Control task history is metadata-only. Audit events use allowlisted codes and identifiers; never record full prompts, reference bytes/paths, generated media, provider responses, token values or arbitrary exception text. Artifact access remains through existing scoped media APIs.

## SQLite deployment and migration

This release keeps the single-host SQLite ledger. Apply only additive tables/indexes and schema-version markers through v4; do not rewrite historical tasks or receipts. Keep legacy positional table shapes intact. Migration is idempotent and runs before the server accepts requests. Existing queued tasks are not automatically adopted by a new pool: they have no `shared_task_pools` admission link and remain legacy work. Drain or reconcile them through the old route before cutover; do not create a replacement pooled job automatically. Replaying an old idempotency key after joining returns `task_not_shared_pool_admitted` instead of silently reviving that legacy job. Take the SQLite online backup and preserve the artifact directory together before upgrading; never copy only a live WAL database file.

One Bridge service process is authoritative for the SQLite ledger. Use local persistent disk, WAL and bounded lock timeout. Restore acceptance copies the stopped writer's database through SQLite online backup, requires `PRAGMA integrity_check`=`ok`, restores the complete artifact tree and runtime/deployment config to an isolated loopback-only candidate, and verifies migration version, health, a known task/receipt read, and artifact availability. Preserve the Nginx site and service unit, image tag, config, and the path/permissions of any secret environment file as the rollback record; do not print, log or commit secret contents. Run FakeRenderer concurrency separately against an empty synthetic ledger with admission enabled, never against restored production data. Do not place SQLite on NFS or start two writers against one data directory. Shared Source workspaces use dynamically issued credentials; they must not share the old single-node Source/Node credential pair.

## Rollback

Keep the previously deployed image and config available. Before rollback, close admission for shared-pool work and list assigned/running/unknown tasks. Never migrate those tasks into a new ledger or create replacement jobs. The old image does not understand dynamic pool credentials or v2 assignments. During rollback, keep shared Node Agents stopped and keep the old `/bridge/node/v1/` ingress closed while shared tasks remain in the ledger; otherwise an old single-node service might offer a matching-scope queued task without assignment checks. Leave unresolved shared tasks frozen until the new runtime is restored. Existing v1 tasks and receipts remain in the original ledger.

If migration or health checks fail before admission opens, stop the new service, restore the last known config/image and retain the database/artifacts for diagnosis. Do not drop tables or remove credentials as part of rollback. Do not claim rollback success until existing task queries, receipt ACKs and node heartbeats work.

\n