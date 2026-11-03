# Codex timeout terminal release

## Correction model

Expected behavior: one 600-second deadline covers Codex execution from
app-server initialization through the image turn. Once the local Codex
subprocess has been stopped and reaped, report a terminal timeout error and
release the local Bridge execution slot. The upstream request may still
complete; duplicate upstream execution is an accepted risk. Alchemy must not
automatically retry through an API after this ambiguous post-admission timeout.

The prior rule treated every post-start uncertainty alike and held the slot
indefinitely. That remains correct for generic transport failures, incomplete
Codex evidence, and an unconfirmed local process exit, but does not meet the
operator-selected timeout policy.

## Authority and minimal correction

The Codex executor is authoritative for proving its local process has exited.
It may produce a timeout-specific terminal evidence record only after the
execution deadline fires and the session context closes successfully with a
confirmed process exit code. The existing terminal-failure handshake remains
authoritative for releasing the server slot: Bridge persists the evidence and
reports it for the same task/attempt. Once process exit is confirmed, the local
journal enters `timeout_report_pending`, which is recoverable but does not
occupy the local physical slot. The server-side slot is freed when it confirms
the terminal failure; failed reporting is retried by heartbeat. Until then,
the server can reject another execution while the local node is idle.

Only the 600-second Codex execution timeout gains terminal treatment. A timeout before a thread
or turn identity is returned remains bound to its Bridge task/attempt, so timeout
evidence may omit those optional Codex IDs. If process reaping cannot be
confirmed, Bridge reports `codex_turn_timeout_unconfirmed`; generic unknown
states and expired execution leases remain occupied and require reconciliation.
The terminal failure projection retains the timeout reason so
Alchemy can show the correct message after the slot is released. Alchemy presents
the timeout as a terminal error with possible upstream uncertainty and does not
automatically route to the API. A later user-initiated generation may duplicate
an upstream request.

## Acceptance

Regression tests cover confirmed process exit, rejected/missing exit evidence,
report retry and restart, server-side capacity release, local journal release,
and continued occupancy for generic unknowns. Existing generation and fallback
contracts remain unchanged outside this explicitly selected post-start timeout.
