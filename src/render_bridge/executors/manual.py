"""Midjourney manual task export/import. No Discord request or /imagine call."""
import hashlib
import json
from pathlib import Path
from render_bridge.image_options import validate_image_request
from .base import AttemptFiles, ExecutionRequest, ExecutionResult, MAX_ARTIFACT_BYTES


class MidjourneyManualExecutor:
    def __init__(self, root: Path):
        self.root = Path(root)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        validate_image_request(request.parameters, request.references, 'midjourney-manual')
        files = AttemptFiles(self.root, request, 'midjourney-manual')
        cached = files.cached()
        if cached:
            return cached
        record = request.payload() | {'prompt_sha256': hashlib.sha256(request.prompt.encode('utf-8')).hexdigest(),
                                      'request_sha256': request.digest, 'requires_human': True,
                                      'warning': 'This is a manual task, not a Midjourney submission.'}
        for name, content in [('task.json', json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')),
                              ('prompt.txt', request.prompt.encode('utf-8'))]:
            path = files.path / name
            try:
                files.write_exclusive(path, content)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError('Existing manual export differs')
        return ExecutionResult('needs_attention', 'awaiting_user_submission', details={
            'task_file': str(files.path / 'task.json'), 'prompt_file': str(files.path / 'prompt.txt'),
            'request_sha256': request.digest})

    def import_original(self, request: ExecutionRequest, source: Path, *,
                        confirmed_request_sha256: str, confirmed_single_original: bool) -> ExecutionResult:
        """Caller chooses task and file explicitly; provenance is a human declaration.

        The Agent must check the server still accepts this attempt before upload.
        This function cannot prove a picture came from Midjourney or distinguish
        a composite grid automatically; the caller must explicitly confirm it.
        """
        validate_image_request(request.parameters, request.references, 'midjourney-manual')
        if confirmed_request_sha256 != request.digest or confirmed_single_original is not True:
            raise ValueError('Explicit matching task and single-original confirmation required')
        files = AttemptFiles(self.root, request, 'midjourney-manual')
        if not (files.path / 'task.json').is_file():
            raise ValueError('Export the manual task before importing')
        source = Path(source)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError('Expected a regular bounded original image')
        return files.save(source.read_bytes(), 'user_declared_midjourney_original')
