"""Deterministic local fixture executor; never calls a model."""
from pathlib import Path
import hashlib
import json
from render_bridge.fake import fixture_png, fixture_jpeg
from render_bridge.image_options import validate_image_request
from .base import AttemptFiles, ExecutionRequest, ExecutionResult


class FakeExecutor:
    def __init__(self, root: Path):
        self.root = Path(root)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        options = validate_image_request(request.parameters, request.references, 'fake-image')
        references = []
        for reference in request.references:
            path = Path(reference['path'])
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Invalid staged reference')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != reference['hash']:
                raise ValueError('Staged reference hash differs')
            references.append({key: reference[key] for key in ('input_ref', 'role', 'hash')})
        files = AttemptFiles(self.root, request, 'fake-image')
        cached = files.cached()
        if cached:
            return cached
        # Validate fixture dimensions before acquiring a possible-execution latch.
        render = fixture_jpeg if options and options['output_format'] == 'jpeg' else fixture_png
        content = render(request.parameters.get('width', 32), request.parameters.get('height', 32))
        if not files.begin():
            return ExecutionResult('execution_unknown', 'attempt_already_started')
        if references:
            files.write_exclusive(files.path / 'references.json', json.dumps(references).encode())
        provenance = 'synthetic_not_conditioned_on_references' if references else 'synthetic_fixture'
        return files.save(content, provenance)
