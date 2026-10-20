# Isolated WSL1 loopback and PostgreSQL capability gates

These are capability gates, **not a Hatchet integration test**. The default
mode creates no database or credentials. The workflow now enables `--postgres`,
which adds a temporary PostgreSQL-only gate after the canary. Neither mode
creates a Hatchet engine/worker, public endpoint, production connection, renderer
or tunnel. A green PostgreSQL result does not establish Hatchet registration,
task delivery, worker shutdown semantics, or Phase 1 acceptance.

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
`engine_tested: false`; `postgres_tested` remains false unless the separate
PostgreSQL gate below passes. No skipped probe counts as a pass. Failed Linux startup/syscalls, download mismatch, inaccessible loopback,
unverifiable WSL version or cleanup failure all produce a nonzero workflow step.
The report contains only image/version/checksum, inventories, synthetic ports,
synthetic SQL result, cleanup evidence and bounded diagnostics. Only that report is uploaded, never the rootfs or logs.

All subprocesses have bounded timeouts; downloads are bounded by 240 seconds and
600 MiB. The job has a 15-minute outer limit. `finally` and the independent
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

The workflow runs `python preflight.py --postgres`. After the canary succeeds,
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
and credential-bearing output are never uploaded. The new PostgreSQL stage
has not yet been validated in actual WSL1 CI at implementation time. Hatchet
integration can be considered only after this separate gate passes.

## Platform references

- https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro
- https://learn.microsoft.com/en-us/windows/wsl/compare-versions
- https://learn.microsoft.com/en-us/windows/wsl/networking
- https://github.com/actions/runner-images/blob/win25-vs2026/20261007.264/images/windows/Windows2025-VS2026-Readme.md

The runner image label is not an immutable image pin: the actual `ImageOS` and
`ImageVersion` are recorded in each result, and runtime capability is checked.
WSL1 syscall support and localhost behavior are measured, not assumed from these
references.
