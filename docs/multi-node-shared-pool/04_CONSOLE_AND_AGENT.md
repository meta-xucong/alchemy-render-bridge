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

A browser cannot start or stop a process on another user's machine. The operator creates a one-use node invitation and can download a per-node `node-enrollment.json` package. The package contains that invitation, the Bridge origin, the allowed profiles, and the sharing-consent decision; it expires with the invitation (one hour by default) and must be sent only to its intended machine. Do not commit or reuse it. There is deliberately no fleet-wide enrollment secret in the public launcher. Clicking the one-click launcher is the node operator's consent to receive participating workspaces' prompts and reference images while assigned work runs.

On Windows, create one node invitation in the operator page and download its one-click package. Place `node-enrollment.json` beside `scripts\start_shared_pool_node.bat`, then double-click the batch file. The package is consumed after successful enrollment. Every later start uses the same batch file without another invitation or credential prompt. Keep its window open while the node is online; Ctrl+C stops it.

The launcher prepares the isolated environment at `%LOCALAPPDATA%\AlchemyRenderBridge\SharedPool\venv`, separate from the legacy V1 environment. It checks Python 3.12, pip, runtime dependencies, the invited profiles against the local Agent configuration, and each configured executor's read-only preflight before consuming the invitation. After enrollment it verifies the saved node token against the exact node identity, then starts the foreground Agent. The Bridge node token stays in `shared-node.json` under `%LOCALAPPDATA%\AlchemyRenderBridge\SharedPool\agent-state`; it is never printed. The local status page opens at `http://127.0.0.1:8766/` and refreshes every five seconds.

The public `scripts\start_shared_pool_node.bat` contains no enrollment credential. Each machine needs its own short-lived invitation package once; a shared reusable secret would allow unapproved machines into the pool. If the local environment fails preflight, the launcher reports the missing requirement and leaves the package unconsumed so it can be retried after repair. The default config enables only FakeRenderer. A real profile such as `codex-image` requires its separately accepted local executor configuration and CLI version; the launcher's checks do not sign in to Codex, inspect its account, or perform a real image generation. Keep such local credentials on the node machine.

The PowerShell launcher accepts `-StateDir`, `-ConfigPath` and `-Interval` when a node uses non-default local paths. The first-run command also supplies `-Url` and `-Profiles`; the enrollment path must match `-StateDir`. The batch launcher has no raw argument forwarding; use PowerShell when custom paths are required.

For non-Windows systems, the CLI remains available after installing this Bridge release:

```text
python -m render_bridge.agent.pool_node enroll --url https://bridge.example --state-dir ./renderbridge-node --profiles fake-image
python -m render_bridge.agent.pool_node run --state-dir ./renderbridge-node --config ./config/agent.example.json
```

`--profiles` must be allowed by the invitation and the Bridge runtime. Real profiles require a local executor and `health_reporting_enabled` in the Agent config so the node can report validated readiness. The browser only copies commands; the Agent runs in the foreground and stops with Ctrl+C. A tray or auto-start Windows service is a separate enhancement.

The foreground pool Agent uses the pool-specific `maintain_shared_pool_agent()` cleanup entrypoint; the legacy CLI's `maintain_agent()` retention behavior is unchanged. Node status uses the API's last heartbeat and reported state. Execution unknown is shown separately from failure. Source task labels and assignment labels retain their distinct meanings.
