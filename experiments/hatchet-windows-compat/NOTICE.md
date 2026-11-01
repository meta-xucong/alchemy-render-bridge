# Experimental Windows signal compatibility patch

Derived from Hatchet Python SDK 1.42.1, official source commit
19a3474b7963139124151e7a3b1357b1971c2eb8, under its MIT license.
Copyright notices and the complete upstream license are retained in UPSTREAM_LICENSE
and embedded in the candidate wheel. This is not an official Hatchet release.

Local candidate version: 1.42.1+bridgewin1. Three source files change:
- Register SIGQUIT only when the platform exposes it.
- On Windows, register listener SIGINT/SIGTERM no-ops via signal.signal rather
  than unsupported asyncio loop.add_signal_handler, including legacy listener.
- Preserve POSIX registration, parent Event and STOP_LOOP protocols and errors.

This is a test-only dependency candidate, not an adopted Bridge runtime dependency.
It does not establish network engine integration, TLS, authorization, drain safety,
Windows console behavior or production readiness. Test Client/transport stubs are
in tests only; the wheel contains no runtime monkeypatch.
