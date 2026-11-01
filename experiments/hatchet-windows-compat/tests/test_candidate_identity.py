import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import unittest

class CandidateIdentityTests(unittest.TestCase):
    def test_imported_candidate_version_and_exact_patched_sources(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'expected_manifest.json').read_text())
        self.assertEqual(importlib.metadata.version('hatchet-sdk'),manifest['candidate_version'])
        for path,hashes in manifest['changed_sources'].items():
            spec=importlib.util.find_spec(path[:-3].replace('/','.'))
            self.assertIsNotNone(spec)
            self.assertEqual(hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest(),hashes['after_sha256'],path)
