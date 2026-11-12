# Console and Node Agent Experience

## Console

The Bridge serves a self-contained, dependency-free fleet dashboard at `/bridge/console`. It opens directly without login or information entry and refreshes every five seconds. This page is read-only. Its same-origin `/bridge/status/v1` endpoint is intentionally public and returns a redacted projection of active and draining nodes, including offline nodes; revoked records are excluded.

The public projection includes aggregate node/online/available/busy/attention/draining/offline/unknown counts, neutral node labels, active/draining state, availability, heartbeat age, and allowlisted profile names. It never includes pool names or IDs, machine/node/executor identifiers, task or workspace identifiers, assignment details, prompts, media, reasons, or credentials. Both the page and its status endpoint use `Cache-Control: no-store`. The dashboard stops polling while hidden and refreshes immediately when visible again.

The separate `/bridge/operator` page is the management UI. The page shell may load without credentials, but fleet data, history, and management actions require an operator bearer, held in page memory only and never stored in localStorage, cookies or URLs. All `/bridge/control/v2/*` APIs remain bearer-protected. The workspace view also continues to require its workspace Source bearer. Remote access requires HTTPS.

The implemented operator view contains:

- **Overview**: active pools/workspaces/nodes, online node count, slot use, queued tasks and active assignments.
- **Pools and onboarding**: create a pool, create workspace/node invitations, create a one-time workspace Source token, and copy a node enrollment command.
- **Nodes**: last heartbeat, active/draining/revoked state, profile list, current assignment, and explicit drain/revoke/reactivate controls.
- **History**: cursor-paginated tasks filtered by status; task detail shows only allowlisted metadata and the Bridge assignment event timeline.
- The operator Fleet summary and node list refresh every five seconds while the operator page is visible. A single in-flight refresh is allowed; the browser stops polling while hidden and refreshes when shown again. This refresh does not load task history.

The workspace view accepts a workspace Source token, displays the sharing disclosure before joining, can leave the pool to stop future assignments, and lists only that workspace's pool-admitted task metadata. Tasks created before consent are not pulled into the pool or projected in its history. Existing authenticated Source v1 routes remain the workspace path for task detail, receipt and media access.

Operator history does not expose prompts/media. The UI does not draw empty success/error cards. User-controlled data is assigned with `textContent`; there is no third-party script, remote font or CDN dependency.

## Pairing and startup

A browser cannot start or stop a process on another user's machine. It provides a one-use code and a copyable command. The local CLI asks for the code through a hidden prompt and requires the operator to type `YES` after the data-sharing disclosure. The node token is stored once in `shared-node.json` under the operator-selected state directory; the CLI never prints it. Protect that directory with the machine user's filesystem permissions. POSIX mode is restricted to `0700/0600`; Windows inherits the directory's existing ACL and this package does not claim to harden it.

On Windows, the operator page shows a first-run command for `scripts\start_shared_pool_node.ps1`. Run it from PowerShell in the installed Bridge release folder. The command starts a child Windows PowerShell process with a process-scoped execution-policy override; it does not change the machine or user policy. The script creates an isolated environment at `%LOCALAPPDATA%\AlchemyRenderBridge\SharedPool\venv`, separate from the legacy V1 environment. It checks for Python 3.12, the current checkout's Bridge package, and all runtime dependencies. If pip is missing it restores it with Python's `ensurepip`; if runtime imports are missing it retries the editable install, so an interrupted first install can be resumed. It copies `config/agent.example.json` to `%LOCALAPPDATA%\AlchemyRenderBridge\SharedPool\agent.json`, then starts the existing CLI enrollment flow. The CLI displays the sharing notice, asks for `YES` and reads the one-use invite code without echoing it; it saves the node credential under `%LOCALAPPDATA%\AlchemyRenderBridge\SharedPool\agent-state` and starts the Agent. Later starts can use `scripts\start_shared_pool_node.bat`. The launcher calls `render_bridge.agent.pool_node run`; it does not use the legacy V1/Hatchet launcher. Keep its window open and press Ctrl+C to stop it. The default config enables only FakeRenderer; real profiles need their own explicit local setup and validated health reporting.

The PowerShell launcher accepts `-StateDir`, `-ConfigPath` and `-Interval` when a node uses non-default local paths. The first-run command also supplies `-Url` and `-Profiles`; the enrollment path must match `-StateDir`. The batch launcher has no raw argument forwarding; use PowerShell when custom paths are required.

For non-Windows systems, the CLI remains available after installing this Bridge release:

```text
python -m render_bridge.agent.pool_node enroll --url https://bridge.example --state-dir ./renderbridge-node --profiles fake-image
python -m render_bridge.agent.pool_node run --state-dir ./renderbridge-node --config ./config/agent.example.json
```

`--profiles` must be allowed by the invitation and the Bridge runtime. Real profiles require a local executor and `health_reporting_enabled` in the Agent config so the node can report validated readiness. The browser only copies commands; the Agent runs in the foreground and stops with Ctrl+C. A tray or auto-start Windows service is a separate enhancement.

The foreground pool Agent uses the pool-specific `maintain_shared_pool_agent()` cleanup entrypoint; the legacy CLI's `maintain_agent()` retention behavior is unchanged. Node status uses the API's last heartbeat and reported state. Execution unknown is shown separately from failure. Source task labels and assignment labels retain their distinct meanings.
