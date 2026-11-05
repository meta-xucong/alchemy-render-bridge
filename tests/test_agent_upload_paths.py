"""Agent artifact upload path containment on platform-virtualized filesystems."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from render_bridge.agent import runner as agent_runner
from render_bridge.agent.runner import NodeAgent
from render_bridge.executors import base as executor_base
from render_bridge.agent.registry import ExecutorRegistry


class UploadPathTests(unittest.TestCase):
    def test_codex_artifact_ready_replays_local_original_without_sha_or_execution(self):
        class Client:
            def task(self, task_id):
                return {"task_id": task_id, "attempt_id": "attempt-fixture", "state": "uploading",
                        "request": {"execution_profile_id": "codex-image", "parameters": {
                            "width": 8, "height": 8, "seed": 0}, "references": []}}

            def create_upload(self, task_id, claim_id, payload):
                self.payload = payload
                return {"upload_id": "upload-fixture"}

            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.content = content

            def commit(self, upload_id, claim_id):
                return {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                        "receipt_id": "receipt-fixture"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "agent-state"
            artifact = root / "attempts" / "task-fixture" / "attempt-fixture" / "result.original"
            artifact.parent.mkdir(parents=True)
            output = io.BytesIO()
            Image.new("RGB", (8, 8), "white").save(output, format="PNG")
            content = output.getvalue()
            artifact.write_bytes(content)
            client = Client()
            agent = NodeAgent(client, root, registry=ExecutorRegistry({}))
            agent.journal.save({
                "task_id": "task-fixture", "attempt_id": "attempt-fixture", "claim_id": "claim-fixture",
                "profile_id": "codex-image", "state": "artifact_ready",
                "artifact": str(artifact.relative_to(agent.journal.root)), "request": {
                    "parameters": {"width": 8, "height": 8, "seed": 0}, "references": []},
                "size": len(content), "mime_type": "image/png",
            })
            with patch.object(agent, "_revalidate_codex_artifact", side_effect=AssertionError("must not recheck")), \
                    patch.object(agent_runner, "hashlib", object()), \
                    patch.object(executor_base, "hashlib", object()):
                result = agent.execute_task("task-fixture")

            self.assertEqual(result["outcome"], "artifact_committed")
            self.assertEqual(client.content, content)
            self.assertNotIn("original_sha256", client.payload)
            self.assertEqual(agent.journal.get("task-fixture")["state"], "complete")

    def test_upload_accepts_managed_artifact_when_resolve_maps_into_appcontainer(self):
        class Client:
            def create_upload(self, task_id, claim_id, payload):
                self.payload = payload
                return {"upload_id": "upload-fixture"}

            def put_upload(self, upload_id, claim_id, content, mime_type):
                self.content = content

            def commit(self, upload_id, claim_id):
                return {"task_id": "task-fixture", "attempt_id": "attempt-fixture",
                        "receipt_id": "receipt-fixture"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "agent-state"
            artifact = root / "attempts" / "task-fixture" / "attempt-fixture" / "result.original"
            artifact.parent.mkdir(parents=True)
            output = io.BytesIO()
            Image.new("RGB", (8, 8), "white").save(output, format="PNG")
            content = output.getvalue()
            artifact.write_bytes(content)

            client = Client()
            agent = NodeAgent(client, root, registry=ExecutorRegistry({}))
            record = {
                "task_id": "task-fixture",
                "attempt_id": "attempt-fixture",
                "claim_id": "claim-fixture",
                "profile_id": "fake-image",
                "state": "artifact_ready",
                "artifact": str(artifact.relative_to(agent.journal.root)),
                "request": {"parameters": {}, "references": []},
                "size": len(content),
                "mime_type": "image/png",
            }
            agent.journal.save(record)

            original_resolve = Path.resolve

            def appcontainer_resolve(path, *args, **kwargs):
                if path == artifact:
                    return Path(temp) / "Packages" / "AppContainer" / "LocalCache" / "result.original"
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", appcontainer_resolve):
                result = agent._upload(record)

            self.assertEqual(result["outcome"], "artifact_committed")
            self.assertEqual(client.content, content)
            self.assertNotIn("original_sha256", client.payload)


if __name__ == "__main__":
    unittest.main()
