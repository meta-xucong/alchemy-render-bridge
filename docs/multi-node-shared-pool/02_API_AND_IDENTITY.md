# API and Identity Contract

## API versioning and authentication

Existing `/bridge/v1/*` routes keep their wire contracts. Shared scheduling adds `/bridge/control/v2/*`, `/bridge/source/v2/*` and `/bridge/node/v2/*`. Shared mode resolves Source tokens by stored token hash and disables the legacy static Node and dispatcher credential maps. All API responses are `no-store`; remote access requires HTTPS.

Credential classes are not interchangeable:

- **Operator token** (`BRIDGE_CONTROL_TOKEN`): creates pools/invites/source credentials, drains/revokes/reactivates nodes and reads allowlisted fleet metadata.
- **Source token**: identifies exactly one `source_id/workspace_id`; reads only that workspace and may accept/leave pool membership.
- **Node token**: unique to one pool/node/executor; stored server-side as a hash. Task/media access always needs its persisted assignment.
- **Invitation code**: random, short-lived and one-use. It is submitted in a JSON body, never a URL or process argument.

The public fleet dashboard does not accept credentials. The separate operator console holds the entered operator token only in page memory. Enrollment returns a node token once; the Agent stores it in its owner-selected state directory. Do not include bearer values or invitation codes in URLs, logs, prompts, task data or audit events.

## Implemented routes

| Route | Role | Behavior |
|---|---|---|
| `GET /bridge/console` | none | Serve the read-only fleet dashboard; no data or secrets embedded |
| `GET /bridge/status/v1` | none | Return redacted aggregate and per-node availability; excludes revoked nodes and identifiers |
| `GET /bridge/operator` | none | Serve the operator and workspace console; its control APIs still require bearer authentication |
| `POST /bridge/control/v2/pools` | operator | Create a private pool |
| `GET /bridge/control/v2/pools` | operator | List pool IDs, state and policy |
| `POST /bridge/control/v2/pools/{pool_id}/invites` | operator | Create a one-use workspace or node invitation |
| `POST /bridge/control/v2/source-credentials` | operator | Create a one-time workspace Source token |
| `POST /bridge/control/v2/source-credentials/{source}/{workspace}/revoke` | operator | Revoke that workspace's Source tokens |
| `GET /bridge/control/v2/summary` | operator | Pool/workspace/node/queue/slot counts; no prompt or media |
| `GET /bridge/control/v2/nodes` | operator | Node health, profiles, one-slot capacity and current assignment |
| `POST /bridge/control/v2/pools/{pool}/{node}/{executor}/drain` | operator | Stop new assignments; preserve started/unknown tasks |
| `POST /bridge/control/v2/pools/{pool}/{node}/{executor}/revoke` | operator | Block new assignments/claims; preserve pinned attempts |
| `POST /bridge/control/v2/pools/{pool}/{node}/{executor}/reactivate` | operator | Permit future assignments after the Agent reports fresh health |
| `GET /bridge/control/v2/tasks?cursor=&limit=&status=&node_id=&workspace_id=` | operator | Cursor-paginated metadata history for tasks with persisted shared-pool admission |
| `GET /bridge/control/v2/tasks/{task_id}` | operator | Allowlisted metadata and assignment event timeline |
| `POST /bridge/source/v2/pool-invites/inspect` | source | Preview pool and sharing disclosure; body contains `invite_code` |
| `POST /bridge/source/v2/pool-invites/accept` | source | Requires body `accept_shared_data=true`; records explicit workspace opt-in |
| `POST /bridge/source/v2/pools/{pool_id}/leave` | source | Stop future assignments for the workspace |
| `GET /bridge/source/v2/tasks?cursor=&limit=&status=` | source | History scoped to the authenticated workspace |
| `POST /bridge/node/v2/enroll` | invitation | Pair after local interactive disclosure; body contains the invite; returns node token once |
| `POST /bridge/node/v2/heartbeat` | node | Report profiles/state and optional validated health. First release fixes one slot per node |
| `GET /bridge/node/v2/tasks/next` | node | Atomically offer one persisted assignment to its selected node |
| `GET /bridge/node/v2/assignments/by-task/{task_id}` | node | Recover only this node's exact assignment and frozen task |
| `POST /bridge/node/v2/assignments/{id}/claim` | node | Claim the exact assignment |
| `POST /bridge/node/v2/assignments/{id}/start` | node | Atomically reserve the node slot and issue/replay its start grant |
| `GET /bridge/node/v2/assignments/{id}/tasks/{task}/inputs/{input}/content` | node | Read only an input pinned to the assigned task |
| `POST /bridge/node/v2/assignments/{id}/tasks/{task}/uploads` | node | Reserve the assigned attempt's output |
| `PUT /bridge/node/v2/assignments/{id}/uploads/{upload}/content` | node | Upload bounded bytes for the same assignment and claim |
| `POST /bridge/node/v2/assignments/{id}/uploads/{upload}/commit` | node | Commit the same attempt and release its slot atomically |

The general Source API v1 still handles a workspace's task creation, input upload, task read, artifact download, receipt and ACK. Its bearer must resolve to the same `SourceScope` used to accept membership.

## Assignment authorization

A node bearer alone never grants access to arbitrary tenant tasks. Every v2 task/media operation checks:

1. The credential hash resolves to one pool/node/executor identity.
2. The assignment belongs to that node and the exact task/attempt.
3. Assignment state allows the operation; claims are bound to that node and have not expired.
4. Input IDs are pinned to the assigned task; upload metadata, bytes and receipt are bound to the same attempt.
5. After a start grant, only the original node/executor can reconcile the attempt. Revoking that node blocks new work but does not move started work to another machine.

Never accept client-provided source/workspace or pool IDs as authorization. Derive them from the task, membership and assignment rows. Assignment IDs are identifiers, not secrets.

## History privacy and error semantics

Operator history returns task/workspace IDs, status, profile, creation time, assigned node/slot, assignment state and allowlisted reason code, and includes only tasks with persisted pool admission created after workspace consent. It never returns prompt text, reference labels/bytes, generated media, provider output or credential values. Source history is workspace-scoped and also includes only pool-admitted tasks. Both use bounded pages of at most 100 rows.

Stable error codes include `unauthorized`, `sharing_consent_required`, `invite_expired`, `invite_consumed`, `pool_membership_required`, `task_not_shared_pool_admitted`, `node_revoked`, `node_draining`, `assignment_expired`, `assignment_identity_mismatch`, `node_unavailable`, `queue_capacity`, `store_busy`, `task_not_executable`, `execution_already_started` and `invalid_request`. Overload and transient lock errors are retryable with bounded backoff. Authorization, identity mismatch and terminal task errors do not retry as a new task.
