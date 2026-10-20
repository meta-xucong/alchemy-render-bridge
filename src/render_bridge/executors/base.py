"""Local executor contracts. No scheduler, credentials, or remote calls."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Literal

from PIL import Image

_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_PIXELS = 40_000_000


@dataclass(frozen=True)
class ExecutionRequest:
    task_id: str
    attempt_id: str
    prompt: str
    parameters: dict = field(default_factory=dict)
    references: tuple[dict, ...] = ()

    def payload(self) -> dict:
        if not _KEY.fullmatch(self.task_id) or not _KEY.fullmatch(self.attempt_id):
            raise ValueError('Invalid task or attempt identifier')
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError('A frozen prompt is required')
        return {'task_id': self.task_id, 'attempt_id': self.attempt_id,
                'prompt': self.prompt, 'parameters': self.parameters,
                'references': list(self.references)}

    @property
    def digest(self) -> str:
        raw = json.dumps(self.payload(), sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    size: int
    mime_type: str
    width: int
    height: int
    provenance: str


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal['ready', 'needs_attention', 'execution_unknown']
    code: str
    artifact: Artifact | None = None
    details: dict = field(default_factory=dict)


def inspect_image(content: bytes, *, provenance: str, path: Path) -> Artifact:
    if not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise ValueError('Image size exceeds local executor limit')
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in ('PNG', 'JPEG'):
            raise ValueError('Expected a PNG or JPEG original')
        if image.width * image.height > MAX_ARTIFACT_PIXELS:
            raise ValueError('Image pixel count exceeds local executor limit')
        width, height, fmt = image.width, image.height, image.format
        image.verify()
    return Artifact(path, hashlib.sha256(content).hexdigest(), len(content),
                    'image/png' if fmt == 'PNG' else 'image/jpeg', width, height, provenance)


class AttemptFiles:
    """One explicitly selected task/attempt. Existing starts never trigger a rerun.

    The Agent still owns dispatch and start grants. These files are local executor
    evidence, not a second waiting queue or a server authorization substitute.
    """
    def __init__(self, root: Path, request: ExecutionRequest, executor: str):
        self.request = request
        self.digest = request.digest
        self.path = Path(root) / request.task_id / request.attempt_id
        self.path.mkdir(parents=True, exist_ok=True)
        binding = {'request_sha256': self.digest, 'executor': executor}
        target = self.path / 'binding.json'
        try:
            self.write_exclusive(target, json.dumps(binding).encode())
        except FileExistsError:
            if json.loads(target.read_text(encoding='utf-8')) != binding:
                raise ValueError('Attempt is already bound to another request or executor')

    @staticmethod
    def write_exclusive(path: Path, content: bytes):
        with path.open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def begin(self) -> bool:
        try:
            self.write_exclusive(self.path / 'started', b'Possible external execution; never auto-restart.\n')
            return True
        except FileExistsError:
            return False

    def cached(self) -> ExecutionResult | None:
        manifest = self.path / 'artifact.json'
        if not manifest.exists():
            return None
        data = json.loads(manifest.read_text(encoding='utf-8'))
        content_path = self.path / 'artifact.original'
        artifact = inspect_image(content_path.read_bytes(), provenance=data['provenance'], path=content_path)
        if artifact.sha256 != data['sha256'] or data['request_sha256'] != self.digest:
            raise ValueError('Cached artifact does not match attempt')
        return ExecutionResult('ready', 'artifact_ready', artifact)

    def save(self, content: bytes, provenance: str) -> ExecutionResult:
        path = self.path / 'artifact.original'
        artifact = inspect_image(content, provenance=provenance, path=path)
        cached = self.cached()
        if cached:
            if cached.artifact.sha256 != artifact.sha256:
                raise ValueError('Attempt already contains a different original')
            return cached
        try:
            self.write_exclusive(path, content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError('Uncommitted artifact differs; reconcile this attempt')
        record = {'request_sha256': self.digest, 'sha256': artifact.sha256,
                  'size': artifact.size, 'mime_type': artifact.mime_type,
                  'width': artifact.width, 'height': artifact.height, 'provenance': provenance}
        try:
            self.write_exclusive(self.path / 'artifact.json', json.dumps(record).encode())
        except FileExistsError:
            return self.cached()
        return ExecutionResult('ready', 'artifact_ready', artifact)
