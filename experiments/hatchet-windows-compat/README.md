# Experimental Hatchet Windows lifecycle candidate

This folder is an isolated development fixture, not a Bridge runtime dependency.
The candidate fixes three signal-registration sites in official SDK 1.42.1.
It does not add a production scheduler, start an engine, create credentials, or
connect to Alchemy/Codex/Discord. All test Clients are explicit no-network stubs;
the dummy config string is intentionally invalid and has no authorization power.

## Reproduce in a disposable Python 3.12 environment

1. `python -m pip install --require-hashes -r requirements-upstream.txt`
2. `python probe_upstream.py`
3. `python -m pip download --no-deps --dest .cache hatchet-sdk==1.42.1`
4. `python build_candidate.py .cache/hatchet_sdk-1.42.1-py3-none-any.whl`
5. `python verify_candidate.py`
6. `python -m pip install --no-deps --force-reinstall dist/hatchet_sdk-1.42.1+bridgewin1-py3-none-any.whl`
7. `python -m pip check`
8. `python -m unittest discover -s tests -v`

Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 before these commands, including on
Windows; the SDK emits emoji in normal logs. The workflow sets both explicitly,
and tests assert UTF-8 stdout/stderr. This is a launch condition of this test
harness, not a fix or proof for arbitrary legacy Windows console encodings.

Use a fresh environment, not a production SDK install. ci-example.yml proposes
this exact optional Linux/Windows matrix under experiments/hatchet-windows-compat.
No wheel binary, upstream package tree, environment or runtime logs should be
committed. Only the reviewed patch, builder, fixed manifest, notices, tests and
hash-locked dependency definitions belong in source control.

## Reproducibility and boundaries

The builder rejects any input wheel except the verified official SHA-256. It
validates three exact source contexts and emits a reviewable unified patch plus
before/after hashes. Candidate version is 1.42.1+bridgewin1; METADATA, wheel path
and RECORD are updated. The MIT license and patch attribution are retained.
ZIP entries are sorted, have a fixed timestamp/create_system/permissions and use
ZIP_STORED to avoid host-dependent zlib output. LF attributes prevent Windows
checkout changes from changing embedded notices. verify_candidate.py verifies
the whole wheel, source manifest, reviewed patch and every RECORD entry.

The original-wheel probe must reproduce missing-SIGQUIT and unsupported-event-loop
errors on real Windows, whereas Linux baseline construction must work. The patched
suite verifies imported version and three source hashes; actual spawned child
Worker construction; both current and legacy listener constructors; real
multiprocessing Event/Queue/STOP_LOOP handshakes; bounded child joins/cleanup;
and platform branches/error propagation. Test mocks apply only to Client,
transport and explicit branch unit tests, not to installed SDK runtime code.

Legacy listener stops its stream and cleans up earlier than the current listener;
the tests preserve and label that upstream difference, rather than changing it
as part of a signal compatibility patch.

## Actual results so far

Linux Python 3.12: original construction control PASS; candidate 8 tests PASS,
zero skips (34.154 seconds); strict manifest/RECORD validation PASS. A byte-identical
rebuild and tampered-input rejection were checked. These are Linux results only.
The first real Windows CI run passed the actual spawned Worker and both listener
lifecycle tests, but failed two simulated platform-branch fixtures. Those fixtures
mutated global sys.platform, causing Windows multiprocessing to import a POSIX-only
module. They now replace only the SDK module-local sys reference, with a regression
asserting the interpreter platform remains unchanged. A separate UTF-8 launch test
was added for SDK emoji logging. Final corrected Windows CI is still pending; the
original failure is not counted as a complete pass. Corrected Linux suite: 10 tests
passed, zero skips (18.256 seconds). The new global-platform regression was also
verified to reject the old fixture with two expected subtest failures. SDK wheel
bytes and its identity manifest are unchanged by this test-harness repair.

Not accepted: real Windows engine connection, console Ctrl+C, network redelivery,
active task draining, TLS, node permissions, sleep/resume, disk failure or the
whole RB-01/Phase 1 gate. A patched lifecycle test pass is not production adoption.

## Source and license

Official source: https://github.com/hatchet-dev/hatchet/tree/19a3474b7963139124151e7a3b1357b1971c2eb8/sdks/python
License: https://github.com/hatchet-dev/hatchet/blob/19a3474b7963139124151e7a3b1357b1971c2eb8/LICENSE
Wheel: https://pypi.org/project/hatchet-sdk/1.42.1/
