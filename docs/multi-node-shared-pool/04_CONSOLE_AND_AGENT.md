# Console and Node Agent Experience

## Console

The Bridge serves a self-contained, dependency-free UI at `/bridge/console`. It uses same-origin APIs and text-only rendering for user-controlled names. The operator or workspace bearer is typed into a password field, held in page memory and cleared by reload; it is never stored in localStorage, cookies or URLs. Remote access requires HTTPS.

The implemented operator view contains:

- **Overview**: active pools/workspaces/nodes, online node count, slot use, queued tasks and active assignments.
- **Pools and onboarding**: create a pool, create workspace/node invitations, create a one-time workspace Source token, and copy a node enrollment command.
- **Nodes**: last heartbeat, active/draining/revoked state, profile list, current assignment, and explicit drain/revoke/reactivate controls.
- **History**: cursor-paginated tasks filtered by status; task detail shows only allowlisted metadata and the Bridge assignment event timeline.

The workspace view accepts a workspace Source token, displays the sharing disclosure before joining, can leave the pool to stop future assignments, and lists only that workspace's pool-admitted task metadata. Tasks created before consent are not pulled into the pool or projected in its history. Existing authenticated Source v1 routes remain the workspace path for task detail, receipt and media access.

Operator history does not expose prompts/media. The UI does not draw empty success/error cards. User-controlled data is assigned with `textContent`; there is no third-party script, remote font or CDN dependency.

## Pairing and startup

A browser cannot start or stop a process on another user's machine. It provides a one-use code and a copyable command. The local CLI asks for the code through a hidden prompt and requires the operator to type `YES` after the data-sharing disclosure. The node token is stored once in `shared-node.json` under the operator-selected state directory; the CLI never prints it. Protect that directory with the machine user's filesystem permissions. POSIX mode is restricted to `0700/0600`; Windows inherits the directory's existing ACL and this package does not claim to harden it.

Example commands after installing this Bridge release:

```text
python -m render_bridge.agent.pool_node enroll --url https://bridge.example --state-dir C:\\BridgeAgent\\node-a --profiles fake-image
python -m render_bridge.agent.pool_node run --state-dir C:\\BridgeAgent\\node-a --config C:\\BridgeAgent\\agent.json
```

`--profiles` must be allowed by the invitation and the Bridge runtime. Real profiles require a local executor and `health_reporting_enabled` in the Agent config so the node can report validated readiness. The browser only copies commands; the Agent runs in the foreground and stops with Ctrl+C. A tray or auto-start Windows service is a separate enhancement.

The foreground pool Agent uses the pool-specific `maintain_shared_pool_agent()` cleanup entrypoint; the legacy CLI's `maintain_agent()` retention behavior is unchanged. Node status uses the API's last heartbeat and reported state. Execution unknown is shown separately from failure. Source task labels and assignment labels retain their distinct meanings.
