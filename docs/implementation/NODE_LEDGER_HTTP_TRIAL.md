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
