# Codex timeout terminal release

## Correction model

Expected behavior: after the configured 600-second Codex turn timeout, once the
local Codex subprocess has been stopped and reaped, report a terminal timeout
error and release the Bridge execution slot. The upstream request may still
complete; duplicate upstream execution is an accepted risk. Alchemy must not
automatically retry through an API after this ambiguous post-admission timeout.

The prior rule treated every post-start uncertainty alike and held the slot
indefinitely. That remains correct for generic transport failures, incomplete
Codex evidence, and an unconfirmed local process exit, but does not meet the
operator-selected timeout policy.

## Authority and minimal correction

The Codex executor is authoritative for proving its local process has exited.
It may produce a timeout-specific terminal evidence record only after the
turn-wait deadline fires and the session context closes successfully with a
confirmed process exit code. The existing terminal-failure handshake remains
authoritative for releasing the server slot: Bridge persists the evidence,
reports it for the same task/attempt/thread/turn, and marks its local journal
`terminal_failed` only after server confirmation. Failed reporting stays
`failure_report_pending` and occupied until heartbeat retry succeeds.

Only `codex_turn_timed_out` gains terminal treatment. If process reaping cannot
be confirmed, Bridge reports `codex_turn_timeout_unconfirmed`; generic unknown
states and expired execution leases also remain occupied and require
reconciliation. The terminal failure projection retains the timeout reason so
Alchemy can show the correct message after the slot is released. Alchemy presents
the timeout as a terminal error with possible upstream uncertainty and does not
automatically route to the API. A later user-initiated generation may duplicate
an upstream request.

## Acceptance

Regression tests cover confirmed process exit, rejected/missing exit evidence,
report retry and restart, server-side capacity release, local journal release,
and continued occupancy for generic unknowns. Existing generation and fallback
contracts remain unchanged outside this explicitly selected post-start timeout.
