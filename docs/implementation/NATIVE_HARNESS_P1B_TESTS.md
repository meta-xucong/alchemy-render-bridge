# Native Harness P1b verification record

Scope: local implementation on base
`cf1e72ec3830854fa0983eb9acb92fad62a604c8`, before publication or independent review.
No real model invocation, deployment, production ledger access or merge occurred.

## Executed

| Command | Result |
| --- | --- |
| `PYTHONPATH=src python -m unittest discover -s tests -p test_harness.py -q` | 40 tests, PASS |
| `PYTHONPATH=src python -m unittest discover -s tests -q` | 354 tests, PASS; 314 existing + 40 Harness tests |
| `git diff --check` | PASS |

Execution used Python 3.12 in an existing dependency environment, with `PYTHONPATH`
set to **this checkout's src**. No implementation was imported from an older
Bridge checkout. Starlette/httpx deprecation warnings appeared; there were no
failed or skipped tests in the above final runs.

New coverage includes strict typed HTTP authentication/claim/start/result ACK;
100 concurrent identical creates; persisted identity pin; replay after binding
removal, drain and offline heartbeat; malformed inputs/results; stale node,
claim/grant receipt refusal; expired-lease result recovery without re-start;
pending observation vs terminal result; unknown liveness capacity isolation;
dedicated-instance rejection of mixed image/Harness heartbeats and ledgers; cancellation before and after start;
retention tombstones; irreversible observed pre-start expiry; storage bounds and
runtime opt-in. It does not implement a second fake task ledger for these tests.

## Frozen implementation fingerprints for this run

```text
5f563b02211035a10611c592de3161c630bd7272e16029d41f00263a0f77c36c  src/render_bridge/harness.py
7e3f71b97527c94b8b765f9b583c5096f952b5242dbeac1d57e4ba5039a142d2  src/render_bridge/domain.py
cce8689847801b1885b7e63b0f2d48011aa2730397565f0a74ae402068cb0112  src/render_bridge/api.py
37e248e8925541896cab1bf78960bf2a4e7cb8a852595f3a9ea147cd03310fe7  src/render_bridge/node_api.py
b2c7e9813f92b6f8d749a7fd883f7d8f1f3c1f91b9b79f996c10419de8e36a0f  src/render_bridge/availability.py
a74488da4598db07cbbe05dd7f5518cf814d98fee9f014b9b3ba48d07082dad5  src/render_bridge/operational_store.py
b2a9e73fe20e789f1bbadace29d1e4664889e7a686652fabad49c518314ef93b  src/render_bridge/runtime.py
b1d43cbe77d7d701b2443f574474ecaef119a902b0e1fc5e7e2ebe4235474982  tests/test_harness.py
```

## Not verified here

- Native project's socket-based cross-repository end-to-end runs are maintained
  separately in that project, not included in the 354 tests above.
- Real Sub2API user-key/authentication/route/billing integration.
- WorkBuddy model selection/readback, real native execution or cancellation.
- OS/supervisor proofs that asynchronous native children and stale launch
  consumers are fenced and stopped; no force-release API is supplied.
- Windows filesystem power-loss guarantees, multi-tenant isolation, production
  TLS deployment, disaster recovery from rolled-back whole databases.
- Commercial or shared-account authorization, pricing and paid usage.

This record is a scoped implementation test result, not production acceptance.

Audit correction: mixed image/Harness execution is not supported. Image heartbeat
then Harness heartbeat on a normal image instance is rejected (Harness disabled);
Harness then image heartbeat on a dedicated Harness instance is rejected. Separate
instances must not share an execution resource. Full suite log: `bridge-p1b-isolated-final.log`.

Mode-pin correction: reproduced the empty-ledger old-image-object race, then
added atomic image/Harness mode pin and 8 concurrent constructor races. Final
full suite: 351 PASS (15.780s); typed suite: 37 PASS. Log:
`bridge-p1b-mode-pin-final.log`. SQLite existing WAL/schema initialization
can fail closed with `database is locked`; the concurrency test retries only
that constructor error, bounded to 20 attempts, before checking exactly one mode
won. This does not fence already-running older binaries; stop them offline.

Bounded admission correction: final suite 354 PASS (16.960s), typed 40 PASS.
Log `bridge-p1b-admission-shield-final.log`. Harness-only gate=2 plus
limiter=1 admits one running store operation and one HTTP waiter. Tests cover
held source read plus waiting Node start, third-call 503, canceled waiting start
with no late grant, and cancellation of a running read preserving serialization
until actual completion. Raw asyncio cancellation previously released the AnyIO
limiter early; this was reproduced and fixed with a shielded worker task. Its
completion callback consumes background exceptions. Image gate remains 1.
