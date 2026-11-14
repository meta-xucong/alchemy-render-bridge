"""Synthetic reward-claim possession proof tests; no external Bridge or Alchemy API."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore


NODE_CONFIRM = "/bridge/node/v2/reward-claims/confirm"
APPROVAL_LOOKUP = "/bridge/rewards/v1/claim-approval"
REWARD_TOKEN = "reward-reader-fixture"
CHALLENGE = "Q" * 43


class Fixture:
    def __init__(self, tmp_path):
        self.now = 1_800_000_000.0
        self.store = BridgeStore(
            tmp_path / "ledger.db", tmp_path / "objects", clock=lambda: self.now,
            dispatch_mode="node_pull", enabled_profiles=("fake-image", "codex-image"),
            max_queued_tasks=20, shared_pool_enabled=True,
        )
        pool = self.store.create_shared_pool("reward proof fixture")
        self.pool_id = pool["pool_id"]
        self.nodes = {}
        for name in ("one", "two"):
            invite = self.store.create_shared_invite(
                self.pool_id, "node", profiles=["codex-image"])
            enrolled = self.store.enroll_shared_node(
                invite["invite_code"], f"node-{name}", f"executor-{name}",
                ["codex-image"], accept_shared_data=True)
            identity = self.store.shared_node_from_token(enrolled["node_token"])
            self.nodes[name] = (identity, enrolled["node_token"])
        self.app = create_app(
            store=self.store, source_tokens={}, shared_pool_enabled=True,
            control_token="operator-fixture", reward_read_token=REWARD_TOKEN,
            store_concurrency=8,
        )

    def confirm_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def lookup_headers(self, claim_digest):
        return {"Authorization": f"Bearer {REWARD_TOKEN}",
                "X-Reward-Claim-Digest": claim_digest}

    def add_count(self, name, count):
        identity, _ = self.nodes[name]
        with self.store._transaction() as con:
            con.execute(
                "INSERT INTO node_execution_ledger "
                "(attempt_key,pool_id,node_id,executor,profile,state,started_at,updated_at,"
                "completed_at,artifact_count) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (hashlib.sha256(f"attempt-{name}".encode()).hexdigest(), identity.pool_id,
                 identity.node_id, identity.executor_instance, "codex-image", "succeeded",
                 self.now - 2, self.now - 1, self.now - 1, count),
            )


def challenge_digest(challenge=CHALLENGE):
    return hashlib.sha256(challenge.encode("utf-8")).hexdigest()


def test_confirmation_uses_authenticated_node_and_cannot_be_rebound(tmp_path):
    asyncio.run(_test_confirmation_uses_authenticated_node_and_cannot_be_rebound(tmp_path))


async def _test_confirmation_uses_authenticated_node_and_cannot_be_rebound(tmp_path):
    fixture = Fixture(tmp_path)
    identity_one, token_one = fixture.nodes["one"]
    _, token_two = fixture.nodes["two"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app),
        base_url="http://127.0.0.1",
    ) as client:
        forged = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token_one),
            json={"challenge": CHALLENGE, "pool_id": "attacker-pool",
                  "node_id": "attacker-node", "executor_instance": "attacker-executor"},
        )
        assert forged.status_code == 422
        assert "attacker-node" not in forged.text

        fixture.store.set_shared_node_state(
            identity_one.pool_id, identity_one.node_id, identity_one.executor_instance, "draining")
        first = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token_one),
            json={"challenge": CHALLENGE},
        )
        assert first.status_code == 200, first.text
        assert first.json() == {"confirmed": True}
        assert CHALLENGE not in first.text

        cross_use = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token_two),
            json={"challenge": CHALLENGE},
        )
        assert cross_use.status_code == 409

        wrong_reader = await client.get(
            APPROVAL_LOOKUP,
            headers={"Authorization": f"Bearer {token_one}",
                     "X-Reward-Claim-Digest": challenge_digest()},
        )
        assert wrong_reader.status_code == 401
        lookup = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(challenge_digest()))
        assert lookup.status_code == 200, lookup.text
        assert "X-Reward-Claim-Digest" in lookup.headers.get("vary", "")
        assert lookup.json() == {
            "pool_id": identity_one.pool_id,
            "node_id": identity_one.node_id,
            "executor_instance": identity_one.executor_instance,
            "node_state": "draining",
            "rewardable_images": 0,
            "pool_name": "reward proof fixture",
        }

    with fixture.store._connection() as con:
        row = con.execute("SELECT * FROM shared_reward_claim_approvals").fetchone()
        assert row is not None
        assert row["challenge_digest"] == challenge_digest()
        assert CHALLENGE not in json.dumps(dict(row))
        assert row["pool_id"] == identity_one.pool_id
        assert row["node_id"] == identity_one.node_id
        assert row["executor"] == identity_one.executor_instance


def test_lookup_is_read_only_snapshot_with_real_rewardable_count_and_unavailable_cases(tmp_path):
    asyncio.run(_test_lookup_is_read_only_snapshot_with_real_rewardable_count_and_unavailable_cases(tmp_path))


async def _test_lookup_is_read_only_snapshot_with_real_rewardable_count_and_unavailable_cases(tmp_path):
    fixture = Fixture(tmp_path)
    identity, token = fixture.nodes["one"]
    fixture.add_count("one", 3)
    claim_digest = challenge_digest("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app),
        base_url="http://127.0.0.1",
    ) as client:
        unconfirmed = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(claim_digest))
        assert unconfirmed.status_code == 404
        assert unconfirmed.json() == {"available": False}
        assert "node_id" not in unconfirmed.json()

        confirmed = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": "A" * 43},
        )
        assert confirmed.status_code == 200
        available = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(claim_digest))
        assert available.status_code == 200
        assert available.json()["rewardable_images"] == 3
        assert available.json()["node_id"] == identity.node_id

        wrong_digest = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers("0" * 64))
        assert wrong_digest.status_code == 404
        assert wrong_digest.json() == {"available": False}

        with fixture.store._transaction() as con:
            con.execute("UPDATE shared_reward_claim_approvals SET expires=? WHERE challenge_digest=?",
                        (fixture.now - 1, claim_digest))
        expired = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(claim_digest))
        assert expired.status_code == 404
        assert expired.json() == {"available": False}

        revoked_challenge = "R" * 43
        revoked_digest = challenge_digest(revoked_challenge)
        assert (await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": revoked_challenge},
        )).status_code == 200
        revoke = fixture.store.set_shared_node_state(
            identity.pool_id, identity.node_id, identity.executor_instance, "revoked")
        assert revoke["state"] == "revoked"
        unavailable = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(revoked_digest))
        assert unavailable.status_code == 404
        assert unavailable.json() == {"available": False}
        rejected = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": "N" * 43},
        )
        assert rejected.status_code == 403


def test_confirmation_retry_keeps_expiry_and_invalid_or_inactive_nodes_cannot_confirm(tmp_path):
    asyncio.run(_test_confirmation_retry_keeps_expiry_and_invalid_or_inactive_nodes_cannot_confirm(tmp_path))


async def _test_confirmation_retry_keeps_expiry_and_invalid_or_inactive_nodes_cannot_confirm(tmp_path):
    fixture = Fixture(tmp_path)
    identity, token = fixture.nodes["one"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app),
        base_url="http://127.0.0.1",
    ) as client:
        no_auth = await client.post(NODE_CONFIRM, json={"challenge": CHALLENGE})
        assert no_auth.status_code == 401
        malformed = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token), json={"challenge": "short"})
        assert malformed.status_code == 422
        bad_token = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers("not-a-node-token"),
            json={"challenge": CHALLENGE},
        )
        assert bad_token.status_code == 401
        invalid_digest = await client.get(
            APPROVAL_LOOKUP,
            headers={"Authorization": f"Bearer {REWARD_TOKEN}",
                     "X-Reward-Claim-Digest": "UPPERCASE"},
        )
        assert invalid_digest.status_code == 404
        assert invalid_digest.json() == {"available": False}
        assert (await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(challenge_digest()))).status_code == 404

        first = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": CHALLENGE},
        )
        assert first.status_code == 200
        with fixture.store._connection() as con:
            original_expiry = con.execute(
                "SELECT expires FROM shared_reward_claim_approvals WHERE challenge_digest=?",
                (challenge_digest(),),
            ).fetchone()[0]
        assert original_expiry == fixture.now + 600

        fixture.now += 120
        retry = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": CHALLENGE},
        )
        assert retry.status_code == 200
        with fixture.store._connection() as con:
            retry_expiry = con.execute(
                "SELECT expires FROM shared_reward_claim_approvals WHERE challenge_digest=?",
                (challenge_digest(),),
            ).fetchone()[0]
        assert retry_expiry == original_expiry

        fixture.now += 481
        expired = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": CHALLENGE},
        )
        assert expired.status_code == 410

        inactive_challenge = "I" * 43
        inactive_digest = challenge_digest(inactive_challenge)
        assert (await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": inactive_challenge},
        )).status_code == 200
        with fixture.store._transaction() as con:
            con.execute("UPDATE shared_nodes SET state='inactive' WHERE pool_id=? AND node_id=? AND executor=?",
                        (identity.pool_id, identity.node_id, identity.executor_instance))
        inactive = await client.post(
            NODE_CONFIRM, headers=fixture.confirm_headers(token),
            json={"challenge": "X" * 43},
        )
        assert inactive.status_code == 403
        lookup = await client.get(
            APPROVAL_LOOKUP, headers=fixture.lookup_headers(inactive_digest))
        assert lookup.status_code == 404
        assert lookup.json() == {"available": False}


def test_nginx_reward_claim_routes_are_exact_and_method_limited():
    config = (Path(__file__).parents[1] / "config" / "source-https.nginx.example.conf").read_text(encoding="utf-8")
    assert "location = /bridge/node/v2/reward-claims/confirm" in config
    assert "location = /bridge/rewards/v1/claim-approval" in config
    assert "if ($request_method != POST) { return 405; }" in config
    assert "if ($request_method != GET) { return 405; }" in config
    assert "location /bridge/rewards/v1/claim-approval" not in config
    assert "location /bridge/node/v2/reward-claims" not in config