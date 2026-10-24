# Isolated WSL1 engine / native Windows synthetic smoke

The default mode is a loopback canary. `--postgres` adds the separate database
gate; `--engine` runs both gates before a real Linux Hatchet engine with a native
Windows SDK worker and three synthetic tasks. This is **not** Bridge API/replay,
TLS, active-task cancellation/draining, production, or full Phase 1 acceptance.
No mode uses a real provider, public endpoint, tunnel, or production data.

## Purpose and authorization boundary

On an ephemeral GitHub-hosted `windows-2025` runner, check whether **already
enabled WSL1** can import a verified official Ubuntu image, execute Linux Python,
and accept one synthetic TCP exchange from native Windows on `127.0.0.1`.

The workflow never enables Windows features, changes the default WSL version,
converts an existing distro, configures virtualization, changes firewall/network
settings, reboots, or touches another distro. If the existing capability is
missing or a syscall fails, it exits nonzero with a blocked result. It does not
try WSL2 or another access route. Only official GitHub actions are used.

## Auditable input

Canonical Ubuntu 24.04 amd64 WSL rootfs, fixed release directory `20240423`:

- Rootfs: https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz
- Official checksum: https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/SHA256SUMS
- SHA256: `2a790896740b14d637dbdc583cce1ba081ac53b9e9cdb46dc09a2f73abbd9934`
- Package inventory: https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/ubuntu-noble-wsl-amd64-wsl.manifest
- Inventory SHA256: `d3930f601ea875e3480c3383e4f5200572b749bf3a4340b81a95f14bb82ec7ea`

These values were read from Canonical's HTTPS checksum manifest on 2026-10-20.
The harness compares downloaded bytes against the committed rootfs digest before
import. It rejects redirects, oversized downloads and timeouts. It does not
claim detached-signature verification. The image is an old, fixed test fixture,
not a patched production image; no package installation or network-facing
service is started. Ubuntu's included licenses/notices remain in the imported
rootfs. Download and import do not make the rootfs part of this repository.

## Stages and exact acceptance criteria

1. Require Windows + GitHub Actions; read the WSL optional-feature state and
   inventories. A read-only current-user WSL registration inventory avoids treating
   localized no-distribution messages as names. The feature must already be `Enabled`.
2. Generate a unique `bridge-wsl1-<32 hex>` distro name; reject a collision. Save
   cleanup ownership state before import.
3. Download/check the fixed image, then `wsl --import ... --version 1`. Verify
   the named distro reports version 1. No default-version command is issued.
4. Execute `/bin/uname` and the image's `/usr/bin/python3` without installing
   dependencies. Python binds exactly `127.0.0.1` on an OS-selected port and
   reports its actual `getsockname()` address. Matching-port Linux `/proc/net/tcp`
   and `/proc/net/tcp6` address/state entries are diagnostic only, not the
   authoritative listener oracle on WSL1. No unrelated sockets are reported.
5. Before any connection, Windows `Get-NetTCPConnection -State Listen` must
   independently expose exactly one listener at that ephemeral port, with address
   exactly `127.0.0.1`, integer matching port and state `Listen`. Empty visibility,
   command failure, unexpected/duplicate rows, IPv6 or wildcard/non-loopback
   addresses block the test; there is no fallback that drops this requirement.
   Only then native Windows Python connects to that exact loopback port, sends the fixed
   synthetic probe, receives the fixed reply, and checks the Linux process exits
   successfully. The listener accepts only one loopback connection and has a
   40-second timeout. It serves no files and carries no user data or credential.
6. Terminate/unregister **only the uniquely owned distro**, confirm it is absent,
   and remove its temporary archive/log/rootfs. Any cleanup failure blocks the
   result. An `always()` workflow step retries cleanup if Python was interrupted.

A pass requires all six stages. `result.json` explicitly reports
`engine_tested` remains false unless the real-engine gate passes;
`postgres_tested` remains false unless the separate
PostgreSQL gate below passes. No skipped probe counts as a pass. Failed Linux startup/syscalls, download mismatch, inaccessible loopback,
unverifiable WSL version or cleanup failure all produce a nonzero workflow step.
The report contains only image/version/checksum, inventories, synthetic ports,
synthetic SQL result, cleanup evidence and bounded diagnostics. Only that report is uploaded, never the rootfs or logs.

All subprocesses have bounded timeouts; downloads are bounded by 240 seconds and
600 MiB. The job has a 25-minute outer limit. `finally` and the independent
`always()` cleanup are best effort under runner failure or hard cancellation;
GitHub runner disposal is the final isolation boundary, not a claimed guarantee
that cleanup executes after an infrastructure crash.

## Run and inspect

The workflow runs on changes to this experiment or its workflow and can also be
manually dispatched. It does not run on pull-request events or use repository
secrets. Its token has read-only contents permission. Locally, only pure tests
are meaningful:

    python -m unittest discover -s experiments/hatchet-wsl1-integration/tests -v

On the GitHub Windows runner, in this directory:

    python preflight.py
    python preflight.py --cleanup

The first CI attempt (run `39378823974`, job `118482969614`, image
`20261007.264.1`) verified the rootfs/import as WSL1 and executed Linux
`4.4.0-26100-Microsoft`. It remained blocked because the original Linux
`/proc/net/tcp` assertion saw no matching listener; cleanup reported no errors.

The revised canary passed in CI `39379235604`, job `118484262133`, commit
`a41b4e9302e14b4c71a3269d32092550d139d4d7`: Windows independently observed
`127.0.0.1:49949`, the synthetic exchange passed, and cleanup errors were empty.
This proves only that exact canary/environment, not PostgreSQL or Hatchet.

## Separate PostgreSQL-only gate

Run the independent database gate with `python preflight.py --postgres`. After the canary succeeds,
it downloads the **Zonky-packaged PostgreSQL 18.3 artifact published to Maven
Central**, the packaging used by the embedded stack. This is not described as
an official PostgreSQL-project binary.

- Fixed artifact: https://repo.maven.apache.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64/18.3.0/embedded-postgres-binaries-linux-amd64-18.3.0.jar
- Registry checksum: the same URL with `.sha256` appended.
- JAR SHA256: `1974c3a59c3ae010e9818e81637e425aad94eaac26e4ef533b42e081fe7ca345`
- Inner `postgres-linux-x86_64.txz` SHA256: `e09b13df10e9df257275d498b19b8c214a3e86d7a5d8cd263b0860e308e8b0b6`
- Packaging source: https://github.com/zonkyio/embedded-postgres-binaries/tree/f3c31c8a290fe09fd4ae8c027697a046f3f656b7 (tag `v18.3.0`).

The registry SHA256 and downloaded JAR bytes were independently matched on
2026-10-20. The inner digest was calculated from that verified JAR. Windows
checks the JAR before executing the Linux probe; Linux repeats the JAR/inner
checks before extraction. The exact ZIP member list, expanded size, tar paths,
member types and relative library symlinks are validated; Python's data filter
also confines extraction. Archive contents, including bundled license/notice
material, are preserved in the temporary runtime. Packaging license and
PostgreSQL notices remain applicable; no binary is committed to this repository.

The Linux process must run as the image's **existing unprivileged, non-login
`nobody` account**. It creates its own private `/tmp` directory. No user is added,
no login shell is enabled, and no existing file ownership or security settings
are changed. If that account or capability is absent, the gate blocks.

Acceptance requires all of the following:

1. Verify executable version exactly `postgres (PostgreSQL) 18.3` and initialize
   a fresh private database with a random temporary password. Local socket auth
   is rejected. No trust authentication is enabled.
2. Set `listen_addresses = '127.0.0.1'`, empty `unix_socket_directories`, and an
   ephemeral port. The HBA permits only the temporary role on `127.0.0.1/32`
   with password authentication. No public or wildcard listener is requested.
3. Windows must independently verify exactly one loopback listener at that port
   before granting the Linux process permission to authenticate and execute SQL.
   The password is sent only over that verified loopback connection to this
   PostgreSQL process. It is never passed to Windows, command lines, logs,
   reports or artifacts. Its file has private permissions and is removed.
4. Execute the fixed synthetic `SELECT 42` and verify exactly one result. The
   standard-library protocol client has bounded reads/messages and emits no
   server error payload or credential material.
5. Use `pg_ctl stop -m fast -w` to stop normally, verify the postmaster PID is
   gone, its PID file is absent and its private directory was removed. Windows
   must then observe no listener at that port. Failure in any check blocks.
6. The outer cleanup still unregisters the unique distro and removes Windows
   temporary files; a failure here overrides a successful PostgreSQL probe.

Commands, readiness, SQL, downloads and shutdown are bounded. On a failed or
interrupted probe, distro termination/removal provides final cleanup; it does
not count as an orderly PostgreSQL pass. Unexpected syscall or access errors
remain blocked, with no security-setting workaround. Diagnostics report only
fixed failure categories (syscall, permissions, shared memory, locale, binding,
configuration) and tightly validated missing-library names. Raw database logs
and credential-bearing output are never uploaded. The PostgreSQL gate passed at commit
`67cbbf61426f67fb7902bd8bdbb6a86855283504`, CI `39380555897`, job
`118488462472`: PostgreSQL 18.3 ran as nobody UID 65534, Windows observed
`127.0.0.1:52901`, Linux-side authenticated `SELECT 42` passed, and normal stop,
process/PID-file/temp-directory removal, host listener absence and distro
cleanup all passed. This was database evidence only, not engine evidence.

## Real engine and native Windows worker gate

The workflow now builds and verifies the existing isolated SDK candidate, then
runs `python preflight.py --engine`. Previous gates remain mandatory. The engine
increment requires a new actual CI result; it is not claimed passed at source
implementation time.

Pinned inputs:

- Official Hatchet embedded sidecar `v0.110.5`, Linux amd64:
  https://github.com/hatchet-dev/hatchet-embedded/releases/download/v0.110.5/hatchet-embedded-sidecar_linux_amd64
- Sidecar SHA256: `18ddacae0005042bd982328bcb8d370cca6907352a1ab2a3c8406001d31e7dee`.
- Embedded source: `3dc26f939dfc5c1072c35dedc65bf51313d665a7`; engine source:
  `56c617e0da399bc9d8a10d2ba74b4950f8a950b8`. The embedded MIT license is retained
  in `ENGINE_LICENSE`. Runtime binaries are not committed.
- SDK official input 1.42.1 SHA256:
  `da9b111aac64fb678f371b92862f48f475631994ba2340ebea28c94de21a61fd`.
- Existing deterministic candidate `1.42.1+bridgewin1` SHA256:
  `f8fbe9ed1944a1c1718b1655b17dddd55ba5e79920d99beabf68c98ad9d8af92`.
  Its source verification, hash-locked dependencies and notices remain in the
  sibling compatibility experiment; this gate does not modify that patch.

Private credential handling and service boundaries:

1. A fresh database and official sidecar run as existing nobody inside the
   uniquely owned WSL1 distro. Binary/JAR/inner hashes are checked before use.
   The database URL on argv contains **no password**. The password is in a
   private Linux `PGPASSFILE`, supported by the exact pgx v5.11.0 source:
   https://github.com/jackc/pgx/blob/v5.11.0/pgconn/config.go .
2. The pinned embedded source sets gRPC bind and broadcast to `127.0.0.1`, TLS
   disabled for this local-only test, and healthcheck off. `--no-api` disables
   REST; Prometheus, analytics and OTEL exporting are disabled explicitly.
   `SERVER_SECURITY_CHECK_ENABLED=false` disables only Hatchet's outbound
   version/security-alert reporting, not Windows/Ubuntu security or request
   authentication. Its exact implementation is
   https://github.com/hatchet-dev/hatchet/blob/56c617e0da399bc9d8a10d2ba74b4950f8a950b8/pkg/security/security.go .
3. The sidecar's private handshake file is read by the Linux controller. Its
   temporary token goes only through a captured subprocess pipe to the Windows
   controller, then through a second stdin pipe to the native worker. These
   pipes are never printed, logged or uploaded. No token appears in argv,
   environment, Windows files, GitHub outputs, or artifacts. Linux handshake,
   keysets, profile, password files and database are deleted with private state.
4. Windows independently checks the PG/gRPC ports and a complete native TCP
   listener delta against a pre-start baseline. Only the two exact IPv4
   loopback endpoints may be new. Repeat the full delta while the actual worker
   is alive after its first completed task. Unexpected REST/metrics/health or
   wildcard listeners block. After shutdown, the complete new-listener delta
   must be empty and both expected ports individually absent.

Real execution and lifecycle evidence:

- The native worker verifies platform `win32` and exact candidate version,
  registers an actual workflow/worker and processes three engine-dispatched
  synthetic tasks. Each result must have its distinct marker, `renderer=fake`,
  platform `win32` and the native worker PID. There are no SDK/transport mocks
  in this runtime smoke. Unit tests remain separate mocked safety checks.
- The SDK's public lifespan captures its running event loop and schedules the
  task driver. After completed tasks, the driver directly awaits public
  `exit_gracefully()` in the same task, using `asyncio.timeout`, not a signal,
  monkeypatch, private event-loop mutation or termination mislabeled graceful.
  It records completion before SDK `loop.stop()` takes effect. Observed listener
  subprocesses must each exit with code zero and no children may remain.
- REST pause is deliberately unavailable at `127.0.0.1:1`; this proves only
  orderly **idle/completed-task** worker shutdown, not server-side pause or
  in-flight drain. Worker stop must finish before engine shutdown begins.
- Closing the official sidecar stdin requests its documented EOF-triggered
  shutdown. Engine exit zero, process disappearance, normal PostgreSQL stop,
  PID-file absence, private directory removal and host listener disappearance
  are all mandatory. Timeout/forced cleanup is a blocked run, never graceful.
- Failure cleanup targets only the owned native worker tree/controller/distro.
  Failure reports allowlist fixed evidence fields and sanitized categories;
  arbitrary exceptions, handshake data, raw SDK/engine logs and credential
  payloads are omitted. The only artifact remains `result.json`.

The engine gate is bounded by download, startup, task, pipe, process and cleanup
timeouts plus the 25-minute disposable-runner job limit. Hard infrastructure
termination can still prevent cleanup callbacks, as noted above.

## Platform references

- https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro
- https://learn.microsoft.com/en-us/windows/wsl/compare-versions
- https://learn.microsoft.com/en-us/windows/wsl/networking
- https://github.com/actions/runner-images/blob/win25-vs2026/20261007.264/images/windows/Windows2025-VS2026-Readme.md

The runner image label is not an immutable image pin: the actual `ImageOS` and
`ImageVersion` are recorded in each result, and runtime capability is checked.
WSL1 syscall support and localhost behavior are measured, not assumed from these
references.
