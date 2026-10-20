# Native Windows node → Bridge ledger HTTP trial

## Scope and status

This increment extends the isolated Windows + WSL1 Hatchet experiment. It is not
an installed Windows Agent, production node API, Linux Bridge server deployment,
source HTTP acceptance, or Alchemy integration. Actual CI runtime acceptance must
be recorded separately with an exact implementation SHA and run/job evidence.
Local tests alone do not establish native Windows or real-engine acceptance.

## Process and ownership boundary

- WSL1 owns the existing temporary Linux Hatchet engine and PostgreSQL processes.
- A separate native Windows Python process, using the unchanged Bridge dependency
  lock, owns the temporary Bridge SQLite ledger, immutable artifacts and HTTP
  adapter. Its source-side create/download/hash/ack operations use public domain
  methods as an explicitly in-process source simulator, not network source API.
- The native Windows Hatchet SDK worker stays in its SDK-pinned interpreter. Only
  the separately hash-locked Pillow dependency is added for existing
  `render_bridge.fake.fixture_png`. No API/Pydantic lock is silently replaced.
- Worker calls carry an opaque fixture task key only. Node cannot select scope,
  claim, node identity, upload ID, filesystem paths, source acknowledgement or
  registration. Node never constructs BridgeStore or accesses server DB/blobs.
- Hatchet is the only dispatcher. Claims are pre-created once by the isolated
  fixture setup, not a new polling queue or production scheduling API.

## Explicit test-only node seam

`node_trial_server.py` binds only 127.0.0.1 on an ephemeral port. Host controller
must verify exact native loopback listener and full listener delta before passing
its independent temporary node bearer token into the worker's private stdin.
No token is reused from Hatchet; there is no source HTTP credential because the
source simulator is in-process. Private handshake output must not be artifacted.

Only POST `/test-only/node/v1/{fixture_key}/{operation}` is supported:

- start: same pre-bound claim receives at most one `may_execute=true`; the frozen
  UTF-8 prompt, parameters and empty reference list are read from the ledger and
  returned. Node checks exact fixture identity before rendering; sanitized worker
  evidence includes only the verified prompt SHA-256 and reference count zero.
- artifact: bounded PNG bytes enter the existing validated upload operations;
  upload ID and task/claim binding stay exclusively server-side.
- commit: existing upload commit produces an immutable receipt.
- unknown: existing grant moves to execution_unknown without freeing capacity.

Auth headers and framing are unambiguous; body max 16 KiB for fixed 32x32 test
media, socket timeout five seconds, serialized HTTP service, no arbitrary routes,
redirect following, scope selection, raw errors or request logging. This is not
production TLS, pairing, revocation, multi-tenant authorization or admission
control acceptance. It must not be publicly exposed.

## Bounded acceptance sequence

Five independent Hatchet submissions, one SDK slot:

1. happy: granted start, fsynced node-owned journal, existing FakeRenderer PNG,
   HTTP upload and commit.
2. happy again: same claim denies another start; no second renderer invocation.
3. unknown: start grant then deliberate unknown transition, without invoking a
   real external process. This is fault injection, not crash-detection proof.
4. unknown again: same claim still denies another start.
5. different task: physical_slot_busy despite freed Hatchet callback capacity.

Worker requires exactly one FakeRenderer journal entry. Server independently
requires start grant counts happy=1, unknown=1, blocked=0, exactly one receipt,
verified downloaded artifact hash and accepted source-simulator acknowledgement.
The independent submissions are deliberate duplicates, not proven broker retry
or network redelivery. The unknown fixture grants permission but never starts a
provider; actual provider termination/reconciliation remains unverified.

Worker orderly shutdown, listener child exit, node-journal deletion, HTTP process
shutdown, server temp directory deletion, engine/PG normal shutdown and zero final
listener delta are separate required gates. Failure cleanup attempts every owned
resource; forced cleanup cannot be reported as successful orderly acceptance.

## Tests and outstanding gates

`experiments/hatchet-wsl1-integration/tests/test_node_trial.py` exercises real
loopback HTTP and a separate server subprocess, replay/lost start response,
unknown slot retention, wrong token/task/action/claim selection, duplicate auth
and framing, oversize or bad media, conflicting digest, cancellation before
commit, and initialization/cleanup failure paths. Existing Bridge API regression
remains a distinct suite, not evidence that this source simulator used HTTP.

Still unverified: TLS, production node/source authorization, Linux-hosted Bridge,
reference-image propagation (this fixture has references=[]), remote network
partitions, actual Hatchet redelivery, active-renderer crash/lease
recovery, persistent operational deployment, real providers and real Alchemy.

## Verified runtime result — 2026-10-20

**Bounded trial PASS; none of the outstanding production gates above are waived.**

Implementation: `f5fda5e2b8f350db9bee8b03977fadf5515f038d`.
[Run 39385273426](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39385273426),
job `118503502523`, completed successfully on GitHub Windows2025 image
`20261007.264.1`. All 72 experiment tests passed in the Bridge interpreter
(13.206 seconds); both isolated dependency checks passed.

The real native Windows SDK worker PID 8692 returned these five results:

1. artifact_committed
2. duplicate_suppressed
3. execution_unknown
4. duplicate_suppressed
5. physical_slot_busy

The node-owned execution journal recorded exactly one FakeRenderer start and was
removed. Frozen UTF-8 prompt digest:
`cfdc2217a30328caae330b9120feb5a53791022bff5e343ba5e9e774d5ff2351`;
reference count remained zero. Ledger grants were happy=1, unknown=1, blocked=0.
The source domain simulator read and verified the 290-byte artifact with SHA256
`5833a7f298963d0ce0e91b6aade58ba3fbbd1aff90d2160358939e76b61202d2`,
and recorded accepted receipt acknowledgement.

Only three new listeners appeared, all IPv4 localhost: PostgreSQL 63371,
Hatchet gRPC 63373 and test HTTP 63409. Listener child 8772 exited 0; worker had no
remaining children. The HTTP server, engine and database stopped normally;
owned runtime directories, journal and WSL distro were removed. Final listener
delta and cleanup error lists were empty. No forced cleanup was counted as pass.

Independent evidence review confirmed the exact commit, outcomes, hash/ack,
listener checks and cleanup. [Durable sanitized JSON](evidence/20261020-node-ledger-http.json)
was parsed from this job's stdout; it is not the original artifact ZIP bytes.

The same implementation's [Bridge contract matrix](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39385273500)
and [SDK lifecycle matrix](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39385273458)
also passed on Linux and Windows. These remain separate scopes.

An earlier run 39384885266 at dee017e failed workflow validation before creating
any job, not in the node runtime. The job-level env incorrectly used runner.temp;
GitHub's documented context availability excludes runner there. The corrected
workflow derives the path at step runtime from RUNNER_TEMP and persists it through
GITHUB_ENV. No exact annotation from that failed run was obtained; the source
restriction and corrected successful execution are the recorded evidence.
