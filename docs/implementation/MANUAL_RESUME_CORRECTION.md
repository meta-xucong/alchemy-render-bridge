# Manual resume correction and configured callback coverage

Baseline: 2bee019. Intended behavior: repairing a pre-start manual prerequisite
and resuming the same attempt changes the next action to importing its original.
Observed: the manual export succeeds, but `action_required_detail` still says
`profile_not_enabled`, contradicting `action_required: import_original`.

Authority: Bridge's manual-ready transaction owns this transition. Automatic
start already clears pre-start attention; manual-ready omitted the equivalent
cleanup. Minimal correction: clear that attention after successful manual-ready,
without changing identities, execution grants, notification or retry behavior.
Regression: disabled manual profile, restore profile, same-task export, verify
import action and unchanged attempt, then import and confirm the receipt.

Configured worker tests additionally exercise the real make_worker callback,
make_submitter and dispatch_once with a fixture engine and HTTP TestClient-backed
NodeAgent. They verify persistent replay and same-original upload retry, not real
Hatchet transport, live tools, Windows or deployment acceptance. Existing separate
process HTTP E2E remains complementary, not proof of live Hatchet scheduling.

Validation: 186 offline unittest cases passed (182 baseline plus four new cases),
compileall and git diff --check passed, exported v1 contracts unchanged. The
configured callback cases use HTTP TestClient transport, not an external engine;
the existing configured-process E2E also passed within the full suite. No real
credentials, provider, message, node revocation or deployment was exercised.
