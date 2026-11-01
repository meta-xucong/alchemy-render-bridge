"""Explicit in-process test executor; never a production dispatcher or real provider."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw

from .domain import BridgeStore, SourceScope
from .image_options import validate_image_request


def fixture_png(width: int = 32, height: int = 32) -> bytes:
    return _fixture_image(width, height, 'PNG')


def fixture_jpeg(width: int = 32, height: int = 32) -> bytes:
    return _fixture_image(width, height, 'JPEG')


def _fixture_image(width: int, height: int, output_format: str) -> bytes:
    """Return a visible synthetic fixture; no model or external service is used."""
    if type(width) is not int or type(height) is not int:
        raise ValueError("fixture dimensions must be integers")
    if not 1 <= width <= 1024 or not 1 <= height <= 1024:
        raise ValueError("fixture dimensions exceed the contract harness limit")
    image = Image.new("RGB", (width, height), (45, 75, 125))
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, width - 1, height - 1), fill=(255, 190, 40), width=1)
    draw.line((0, height - 1, width - 1, 0), fill=(255, 190, 40), width=1)
    out = io.BytesIO()
    image.save(out, format=output_format)
    return out.getvalue()


def run_fake_once(
    store: BridgeStore,
    scope: SourceScope,
    task_id: str,
    *,
    node_id: str,
    executor_instance: str,
    start_log: Path,
) -> dict:
    """Drive one test attempt. Redelivery cannot silently rerun image creation.

    start_log is a trusted test-owned path, not a path supplied by an API request.
    This helper intentionally does not implement automatic retries or recovery.
    If it crashes after start permission, the domain must remain fail-closed.
    """
    claim = store.claim_task(
        scope, task_id, node_id=node_id, executor_instance=executor_instance
    )
    grant = store.start_execution(scope, task_id, claim["claim_id"])
    if not grant["may_execute"]:
        return {"executed": False, "task": store.get_task(scope, task_id)}
    with start_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"task_id": task_id, "attempt_id": grant["attempt_id"]}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    task = store.get_task(scope, task_id)
    params = task["request"]["parameters"]
    options = validate_image_request(params, task['request']['references'], 'fake-image')
    output_format = options['output_format'] if options else 'png'
    render = fixture_jpeg if output_format == 'jpeg' else fixture_png
    content = render(params.get("width", 32), params.get("height", 32))
    upload = store.create_upload(
        scope,
        task_id,
        claim["claim_id"],
        {
            "attempt_id": grant["attempt_id"],
            "slot_id": "primary",
            "original_sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mime_type": "image/" + output_format,
        },
    )
    store.put_upload_content(scope, upload["upload_id"], claim["claim_id"], content)
    receipt = store.commit_upload(scope, upload["upload_id"], claim["claim_id"])
    return {"executed": True, "receipt": receipt}
