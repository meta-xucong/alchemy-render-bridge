# Running the isolated contract slice

Python 3.12+ is required. Install the project and test extras in an isolated virtual
environment, then run:

```
python -m pip install --require-hashes -r requirements-test.txt
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/export_contracts.py
git diff --check
```

`uv.lock` pins the resolved dependency graph; `uv sync --locked --extra test` is
an alternative for an environment with uv installed. Tests create only temporary
SQLite databases and bounded synthetic files. `test_fake_e2e.py` drives HTTP
requests through ASGI and invokes the FakeRenderer explicitly; there is no
production server startup command and no automatic execution scheduler.

Test credentials in fixtures are literal isolated scope selectors. Do not reuse
them or the fixture authentication map as production access control. Production
TLS, node credentials, provisioning and revocation are outside this slice.

An accepted artifact/ack is not an Alchemy integration test. The original Alchemy
repository is not modified. Consult `ALCHEMY_READ_ONLY_PREFLIGHT.md` for the
existing import boundary and unresolved integration work.
