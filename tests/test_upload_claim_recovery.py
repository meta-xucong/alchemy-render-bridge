import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from render_bridge.agent.registry import ExecutorRegistry
from render_bridge.agent.runner import NodeAgent


class UploadClaimRecoveryTests(unittest.TestCase):
    def test_unknown_artifact_replay_uses_upload_only_and_commits_same_attempt(self):
        class Client:
            def __init__(self):
                self.claim_mode = None
                self.upload_claim_id = None
                self.uploaded_content = None

            def claim(self, task_id, mode):
                self.claim_mode = mode
                return {"claim_id": "upload-claim", "attempt_id": "attempt-fixture", "mode": mode}

            def create_upload(self, task_id, claim_id, payload):
                self.upload_claim_id = claim_id
                self.payload = payload
                return {"upload_id": "upload-fixture"}

            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.uploaded_content = content

            def commit(self, upload_id, claim_id):
                return {"receipt_id": "receipt-fixture", "task_id": "task-fixture",
                    "attempt_id": "attempt-fixture", "original_sha256": self.payload["original_sha256"]}

        class FixedDigest:
            def hexdigest(self):
                return "fixture-digest"

        with tempfile.TemporaryDirectory() as temp:
            client = Client()
            agent = NodeAgent(client, Path(temp) / "agent", registry=ExecutorRegistry({}))
            artifact = agent.journal.root / "attempts" / "task-fixture" / "attempt-fixture" / "result.original"
            artifact.parent.mkdir(parents=True)
            content = b"synthetic artifact bytes"
            artifact.write_bytes(content)
            record = {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                "state": "artifact_ready", "claim_id": "execute-claim", "claim_mode": "execute",
                "profile_id": "codex-image", "artifact": str(artifact.relative_to(agent.journal.root)),
                "sha256": "fixture-digest", "size": len(content), "mime_type": "image/png",
                "request": {"parameters": {}}}

            with patch("render_bridge.agent.runner.hashlib.sha256", return_value=FixedDigest()), \
                 patch("render_bridge.agent.runner.inspect_image",
                       return_value=SimpleNamespace(mime_type="image/png")), \
                 patch("render_bridge.agent.runner.requested_output_mime", return_value=None):
                result = agent._upload(record, task_state="execution_unknown")

        self.assertEqual(result["outcome"], "artifact_committed")
        self.assertEqual(client.claim_mode, "upload_only")
        self.assertEqual(client.upload_claim_id, "upload-claim")
        self.assertEqual(client.uploaded_content, content)
        self.assertEqual(record["state"], "complete")
        self.assertEqual(record["receipt_id"], "receipt-fixture")

    def test_execute_passes_bridge_task_state_to_artifact_replay(self):
        class Client:
            def task(self, task_id):
                return {"attempt_id": "attempt-fixture", "state": "execution_unknown",
                    "receipt_id": None,
                    "request": {"execution_profile_id": "codex-image", "parameters": {}, "references": []}}

        with tempfile.TemporaryDirectory() as temp:
            agent = NodeAgent(Client(), Path(temp) / "agent", registry=ExecutorRegistry({}))
            record = {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                "state": "artifact_ready", "profile_id": "codex-image"}
            agent.journal.save(record)
            stored = agent.journal.get("task-fixture")
            agent._revalidate_codex_artifact = Mock(return_value=True)
            agent._upload = Mock(return_value={"outcome": "artifact_committed", "task_id": "task-fixture"})

            with patch("render_bridge.agent.runner.validate_image_request"):
                result = agent._execute("task-fixture", None)

        self.assertEqual(result["outcome"], "artifact_committed")
        agent._upload.assert_called_once_with(stored, task_state="execution_unknown")

    def test_first_completed_recovery_upload_uses_unknown_task_state(self):
        class Executor:
            def recover_completed(self, request):
                return SimpleNamespace(status="ready", artifact=object(), details={})

        class Client:
            def task(self, task_id):
                return {"attempt_id": "attempt-fixture", "state": "execution_unknown",
                    "receipt_id": None,
                    "request": {"execution_profile_id": "codex-image", "parameters": {}, "references": []}}

        with tempfile.TemporaryDirectory() as temp:
            executor = Executor()
            agent = NodeAgent(Client(), Path(temp) / "agent",
                registry=ExecutorRegistry({"codex-image": executor}))
            record = {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                "state": "execution_unknown", "profile_id": "codex-image",
                "request": {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                    "prompt": "fixture prompt", "parameters": {}, "references": []}}
            agent.journal.save(record)
            stored = agent.journal.get("task-fixture")
            agent._save_artifact = Mock()
            agent._upload = Mock(return_value={"outcome": "artifact_committed", "task_id": "task-fixture"})

            with patch("render_bridge.agent.runner.validate_image_request"):
                result = agent._execute("task-fixture", None)

        self.assertEqual(result["outcome"], "artifact_committed")
        agent._save_artifact.assert_called_once()
        agent._upload.assert_called_once_with(stored, task_state="execution_unknown")


if __name__ == "__main__":
    unittest.main()
