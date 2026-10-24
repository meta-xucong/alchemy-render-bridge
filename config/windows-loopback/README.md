# Safe local examples

See [Windows local loopback deployment](../../docs/implementation/WINDOWS_LOCAL_LOOPBACK.md).

- `bridge.fake.json` and `agent.fake.json` enable only `fake-image`. Codex is disabled and its acceptance flags are false. No token values are included; credentials must be supplied separately by the operator. The guide replaces the relative Bridge data directory with a private absolute directory before startup.
- `hatchet.compose.json` is an operator-run Docker Compose input, not an installer. It requires an explicitly supplied private database password, publishes only loopback engine ports and keeps the database internal. It uses independent named volumes; never remove them to resolve a pending task. Its image tags are pinned candidates, not a statement that Docker runtime or this user's machine has passed acceptance.
- Health/account-probe defaults remain false and recovery generation remains 0 by omission for compatibility with older Agent parsers. Enable only on the revision that supports the fields, using the documented acceptance flow. Codex approval_policy remains empty/blocked by omission; no automatic migration to on-request is authorized. Account probing requires separate local operator opt-in and may cause the CLI to refresh its existing login. Automatic paid cloud fallback is not enabled by these files.

From the Bridge root, using its isolated interpreter:

```sh
python config/windows-loopback/check_examples.py
python -m render_bridge.doctor --bridge-config config/windows-loopback/bridge.fake.json --agent-config config/windows-loopback/agent.fake.json --json
```

These commands do not start services, load credentials, call Codex or invoke a provider. JSON/config checks do not prove Docker Compose execution or Windows compatibility. The deployment guide separately requires `docker compose ... config --quiet` on the user's configured Docker installation, without printing interpolated secrets.
