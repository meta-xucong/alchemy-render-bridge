"""Node Agent HTTP adapter for assignment-scoped shared-pool APIs."""
from __future__ import annotations

from .client import HttpNodeClient


class PoolNodeHTTPClient(HttpNodeClient):
    prefix = "/bridge/node/v2"

    def __init__(self, url: str, token: str, *, transport=None):
        super().__init__(url, token, transport=transport)
        self._assignments: dict[str, str] = {}
        self._uploads: dict[str, str] = {}

    def _assignment(self, task_id):
        assignment_id = self._assignments.get(task_id)
        if assignment_id:
            return assignment_id
        task = self.request("GET", "/assignments/by-task/" + self.key(task_id)).json()
        assignment_id = task.get("assignment_id")
        if not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("Bridge task has no persisted node assignment")
        self._assignments[task_id] = assignment_id
        return assignment_id

    def heartbeat(self, state, profiles, *, profile_health=None):
        payload = {"state": state, "profiles": profiles}
        if profile_health is not None:
            payload["profile_health"] = profile_health
        return self.request("POST", "/heartbeat", json=payload, timeout=5).json()

    def status(self):
        """Fetch the status projection scoped to this node bearer credential."""
        return self.request("GET", "/status", timeout=5).json()

    def next_task(self):
        assignment = self.request("GET", "/tasks/next", timeout=5).json().get("assignment")
        if not assignment:
            return None
        task_id, assignment_id = assignment.get("task_id"), assignment.get("assignment_id")
        if not isinstance(task_id, str) or not isinstance(assignment_id, str):
            raise ValueError("Bridge returned an invalid assignment")
        self._assignments[task_id] = assignment_id
        return task_id

    def task(self, task_id):
        task = self.request("GET", "/assignments/by-task/" + self.key(task_id)).json()
        assignment_id = task.get("assignment_id")
        if not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("Bridge task has no persisted node assignment")
        self._assignments[task_id] = assignment_id
        return task

    def claim(self, task_id, mode="execute"):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/claim",
                            json={"mode": mode}).json()

    def manual_ready(self, task_id, claim_id):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/manual-ready",
                            json={"claim_id": claim_id}).json()

    def start(self, task_id, claim_id):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/start",
                            json={"claim_id": claim_id}).json()

    def attention(self, task_id, attempt_id, code):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/attention",
                            json={"attempt_id": attempt_id, "code": code}).json()

    def terminal_failure(self, task_id, attempt_id, evidence):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/terminal-failure",
                            json={"attempt_id": attempt_id, "evidence": evidence}).json()

    def cancel_completed(self, task_id, attempt_id):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/cancel-completed",
                            json={"attempt_id": attempt_id}).json()

    def unknown(self, task_id, claim_id):
        return self.request("POST", f"/assignments/{self.key(self._assignment(task_id))}/unknown",
                            json={"claim_id": claim_id}).json()

    def input_content(self, task_id, input_id, claim_id):
        assignment_id = self._assignment(task_id)
        path = (f"/assignments/{self.key(assignment_id)}/tasks/{self.key(task_id)}"
                f"/inputs/{self.key(input_id)}/content")
        return self.request("GET", path, headers={"X-Bridge-Claim": claim_id}).content

    def create_upload(self, task_id, claim_id, payload):
        assignment_id = self._assignment(task_id)
        result = self.request("POST", f"/assignments/{self.key(assignment_id)}"
                              f"/tasks/{self.key(task_id)}/uploads",
                              json={"claim_id": claim_id, "payload": payload}).json()
        upload_id = result.get("upload_id")
        if isinstance(upload_id, str):
            self._uploads[upload_id] = assignment_id
        return result

    def put_upload(self, upload_id, claim_id, content, mime_type):
        assignment_id = self._uploads.get(upload_id)
        if not assignment_id:
            raise ValueError("Upload has no local assignment binding; reconcile the original attempt")
        return self.request("PUT", f"/assignments/{self.key(assignment_id)}"
                            f"/uploads/{self.key(upload_id)}/content", content=content,
                            headers={"X-Bridge-Claim": claim_id, "Content-Type": mime_type}).json()

    def commit(self, upload_id, claim_id):
        assignment_id = self._uploads.get(upload_id)
        if not assignment_id:
            raise ValueError("Upload has no local assignment binding; reconcile the original attempt")
        return self.request("POST", f"/assignments/{self.key(assignment_id)}"
                            f"/uploads/{self.key(upload_id)}/commit",
                            json={"claim_id": claim_id}).json()
