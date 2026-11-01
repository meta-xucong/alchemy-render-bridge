# VPS deployment: API-pull Bridge service

This runbook deploys the Bridge Source API and its task/artifact store on the
Alchemy VPS. The Windows Node Agent connects outbound over HTTPS. Keep the
Bridge runtime bound to loopback; Nginx is the public TLS entry point.

The runtime is the existing `node_pull` mode. It requires distinct
`BRIDGE_SOURCE_TOKEN` and `BRIDGE_NODE_TOKEN` values. Alchemy receives only the
Source token; the Windows Agent receives only the Node token. Do not configure
the Hatchet dispatcher for this ledger.

## Files and runtime paths

| Purpose | Path |
|---|---|
| Container image | `alchemy-render-bridge:rb33-20261102` |
| Runtime config | `/etc/alchemy/render-bridge/bridge.json` |
| Runtime credentials | `/etc/alchemy/render-bridge/bridge.env` |
| SQLite ledger and artifacts | `/var/lib/alchemy/render-bridge` |
| systemd unit | `/etc/systemd/system/alchemy-render-bridge-api.service` |

The Bridge process runs as UID/GID `65532:65532` inside a Python 3.12
container managed by systemd. Host networking makes its loopback listener
available to the existing Nginx proxy. The container filesystem is read-only;
only the Bridge data directory is writable. Back up the SQLite file and the
artifacts directory together.

## Install and start

1. Place the reviewed source release at
   `/opt/alchemy-render-bridge/releases/api-pull-20261102`, then build its runtime image
   from the repository root. The VPS uses the existing Python 3.12 container
   image; the host system Python remains unchanged.

   ```sh
   cd /opt/alchemy-render-bridge/releases/api-pull-20261102
   docker build -f deploy/docker/Dockerfile -t alchemy-render-bridge:rb33-20261102 .
   ```

2. Create the persistent data/config directories. The data directory must be
   writable by container UID/GID 65532; the credential file is root-owned and
   mode `0600`, because the Docker daemon reads it when starting the container.

   ```sh
   install -d -o 65532 -g 65532 -m 0750 /var/lib/alchemy/render-bridge
   install -d -o root -g root -m 0711 /etc/alchemy/render-bridge
   ```

   If the data directory already exists, preserve it and verify ownership
   instead of recreating it.

3. Copy `config/bridge.remote-pull.example.json` to
   `/etc/alchemy/render-bridge/bridge.json`. Set the real source/workspace/node
   and executor identities; retain `dispatch_mode: "node_pull"`,
   `host: "127.0.0.1"`, `accepting_new_tasks: false`, and the fake-only profile
   for the initial service and Agent heartbeat check. Keep the data path at
   `/var/lib/alchemy/render-bridge`. Set the non-secret config file to
   `root:root` mode `0644` so the container can read it; its parent directory
   must allow traversal by UID 65532.

4. Create `/etc/alchemy/render-bridge/bridge.env` with distinct random values
   for `BRIDGE_SOURCE_TOKEN` and `BRIDGE_NODE_TOKEN`, then set ownership to
   `root:root` and mode `0600`. Do not place credentials in the
   runtime JSON, repository, shell history or service command line.

5. Install the unit and start it:

   ```sh
   install -o root -g root -m 0644 deploy/systemd/alchemy-render-bridge-api.service /etc/systemd/system/alchemy-render-bridge-api.service
   systemctl daemon-reload
   systemctl enable --now alchemy-render-bridge-api.service
   systemctl status --no-pager alchemy-render-bridge-api.service
   ```

   The runtime listens only on `127.0.0.1:8787`. Confirm it is active before
   adding the HTTPS virtual host. Service logs are available with
   `journalctl -u alchemy-render-bridge-api.service`.

6. Configure the Nginx example `config/source-https.nginx.example.conf` with
   the Bridge hostname and its trusted certificate paths. It forwards only
   `/bridge/v1/` and `/bridge/node/v1/` to loopback. Keep the root path and all
   dispatcher, engine, admin, database and CLI interfaces private.

7. Connect the Windows Agent in `pull-worker` mode with admission closed.
   Verify heartbeat and the fake-image profile. For the synthetic
   create/pull/transfer/ACK flow, temporarily set `accepting_new_tasks: true`
   while keeping `enabled_profiles` fake-only. After the flow completes, close
   admission again. Add `codex-image` and enable new-task admission only during
   the later, explicitly approved production cutover.

## Updating the code release

Build each new source release as a distinct image tag, update the systemd
unit's image tag, then restart the unit. Retain the previous image tag for
rollback. Keep `/var/lib/alchemy/render-bridge` and the configured
workspace/node/executor identities unchanged. Do not copy a local Bridge
database into this directory.

## Operational boundary

This runbook supplies the service lifecycle and file layout; it does not create
credentials, DNS records, certificates or firewall rules. A started service is
not proof that the public TLS ingress, Windows Agent, Alchemy routes, or real
Codex image delivery have passed acceptance.
