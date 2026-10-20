# Isolated WSL1 loopback capability preflight

This is **only the first capability gate**, not a Hatchet integration test. It
creates no engine, database, worker, authentication credential, public endpoint,
production connection, renderer, or tunnel. A green result does not establish
PostgreSQL compatibility, Hatchet registration/task delivery, shutdown semantics,
or Phase 1 acceptance.

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
`engine_tested: false` and `postgres_tested: false`; no skipped probe counts as a
pass. Failed Linux startup/syscalls, download mismatch, inaccessible loopback,
unverifiable WSL version or cleanup failure all produce a nonzero workflow step.
The report contains only image/version/checksum, inventories, synthetic port and
bounded diagnostics. Only that report is uploaded, never the rootfs or logs.

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
`4.4.0-26100-Microsoft`. It remained **blocked** because the original Linux
`/proc/net/tcp` assertion saw no matching listener; cleanup reported no errors.
This is not evidence that localhost connectivity or WSL1 is unsupported, and the
first attempt is not retroactively a pass. The revised Windows listener oracle
requires a fresh actual CI run; its result is not yet claimed. If this gate passes,
a separately reviewed extension must verify temporary PostgreSQL startup and
shutdown before any real Hatchet engine/Windows worker trial. That later trial
must pin its binary/dependency inputs and preserve loopback-only endpoints,
ephemeral credentials, UTF-8 Windows output, bounded waits and cleanup.

## Platform references

- https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro
- https://learn.microsoft.com/en-us/windows/wsl/compare-versions
- https://learn.microsoft.com/en-us/windows/wsl/networking
- https://github.com/actions/runner-images/blob/win25-vs2026/20261007.264/images/windows/Windows2025-VS2026-Readme.md

The runner image label is not an immutable image pin: the actual `ImageOS` and
`ImageVersion` are recorded in each result, and runtime capability is checked.
WSL1 syscall support and localhost behavior are measured, not assumed from these
references.
