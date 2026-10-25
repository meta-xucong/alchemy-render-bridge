"""Bounded typed options: no external CLI, credentials, or provider calls."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.executors import CodexExecutor, ExecutionRequest, FakeExecutor, MidjourneyManualExecutor
from render_bridge.executors.base import AttemptFiles
from render_bridge.fake import fixture_png, fixture_jpeg, run_fake_once
from render_bridge.api import create_app
from render_bridge.schemas import TaskCreate
from render_bridge.image_options import codex_imagegen_arguments, requested_output_mime, validate_image_request


def options(fmt='png'):
    return {'schema_version': '1', 'quality': 'auto', 'output_format': fmt}


class ImageOptionsContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000
        self.scope = SourceScope('source', 'workspace')
        self.store = BridgeStore(self.root / 'db', self.root / 'artifacts', clock=lambda: self.now,
            enabled_profiles=('fake-image', 'codex-image', 'midjourney-manual'))
        self.addCleanup(self.store.close)
        self.client = TestClient(create_app(store=self.store,
            source_tokens={'source': self.scope}),
            headers={'Authorization': 'Bearer source'})
        self.addCleanup(self.client.close)

    def payload(self, profile='fake-image', fmt='png', key='one'):
        return {'schema_version': '1', 'origin_key': key, 'binding_id': 'binding',
            'operation': 'image.generate', 'execution_profile_id': profile,
            'execution_profile_revision': '1', 'prompt': 'Frozen prompt',
            'parameters': {'width': 32, 'height': 24, 'image_options': options(fmt)},
            'references': [], 'output_slot': {'slot_id': 'primary', 'count': 1},
            'routing': 'bridge_only', 'deadline': datetime.fromtimestamp(self.now+3600, timezone.utc).isoformat()}

    def test_typed_options_http_domain_match_and_no_normalization(self):
        for profile in ('fake-image', 'codex-image', 'midjourney-manual'):
            for fmt in ('png', 'jpeg'):
                with self.subTest(profile=profile, fmt=fmt):
                    payload = self.payload(profile, fmt, profile + fmt)
                    frozen = copy.deepcopy(payload)
                    self.assertEqual(TaskCreate.model_validate(payload).model_dump(exclude_unset=True), frozen)
                    response = self.client.post('/bridge/v1/tasks', json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    direct = self.store.create_task(self.scope, payload)
                    self.assertEqual(response.json()['remote_task_id'], direct['remote_task_id'])
                    self.assertEqual(self.store.get_task(self.scope, direct['remote_task_id'])['request'], frozen)

    def test_invalid_options_rejected_by_http_domain_and_executors_before_start(self):
        invalid = [None, {}, [], 'png', options() | {'schema_version': '2'},
            options() | {'schema_version': 1},
            options() | {'output_format': None}]
        invalid += [options() | {'quality': value} for value in (None, True, [])]
        invalid += [options() | {'input_fidelity': value} for value in (None, False)]
        invalid += [options() | {'unknown': True}]
        for index, value in enumerate(invalid):
            with self.subTest(value=value):
                payload = self.payload(key=str(index))
                payload['parameters']['image_options'] = value
                self.assertEqual(self.client.post('/bridge/v1/tasks', json=payload).status_code, 422)
                with self.assertRaises(DomainError):
                    self.store.create_task(self.scope, payload)
                request = ExecutionRequest('task-'+str(index), 'attempt', 'test', payload['parameters'])
                for cls in (FakeExecutor, CodexExecutor, MidjourneyManualExecutor):
                    with patch('render_bridge.executors.codex.probe_codex') as probe, \
                         patch('render_bridge.executors.codex.StdioSession') as launch:
                        executor = cls(self.root / cls.__name__)
                        with self.assertRaises(ValueError):
                            executor.execute(request)
                        if hasattr(executor, 'preflight'):
                            with self.assertRaises(ValueError):
                                executor.preflight(request)
                        probe.assert_not_called()
                        launch.assert_not_called()
                    self.assertFalse((executor.root / request.task_id).exists())

    def test_top_level_controls_remain_rejected(self):
        for field in ('quality', 'output_format', 'input_fidelity'):
            payload = self.payload()
            payload['parameters'][field] = 'auto'
            self.assertEqual(self.client.post('/bridge/v1/tasks', json=payload).status_code, 422)
            with self.assertRaises(DomainError):
                self.store.create_task(self.scope, payload)
            with self.assertRaises(ValueError):
                FakeExecutor(self.root).execute(ExecutionRequest('task', 'attempt', 'test', payload['parameters']))

    def test_codex_adapter_defaults_api_only_controls_and_preserves_native_transparency(self):
        parameters = {'width': 1536, 'height': 1024, 'seed': 41,
            'image_options': {'schema_version': '1', 'quality': 'high', 'output_format': 'webp',
                'input_fidelity': 'high', 'transparent_background': True}}
        validate_image_request(parameters, (), 'codex-image')
        self.assertEqual(codex_imagegen_arguments(parameters, 'codex-cli 0.163.0'), {
            'transparent_background': True, 'transparent_background_mapping': 'native_tool_argument'})
        self.assertEqual(codex_imagegen_arguments(parameters, 'codex-cli 0.157.1'), {
            'transparent_background': False, 'transparent_background_mapping': 'codex_cli_default_auto'})
        self.assertIsNone(requested_output_mime(parameters, 'codex-image'))
        payload = self.payload('codex-image', key='normalized')
        payload['parameters'] = parameters
        response = self.client.post('/bridge/v1/tasks', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(TaskCreate.model_validate(payload).parameters.model_dump(exclude_unset=True), parameters)

    def test_codex_reference_limit_matches_native_tool_limit(self):
        refs = [{'input_ref': f'ref-{index}', 'role': 'reference', 'hash': '0' * 64} for index in range(5)]
        validate_image_request({}, refs, 'codex-image')
        with self.assertRaises(ValueError):
            validate_image_request({}, refs + [{'input_ref': 'ref-5', 'role': 'reference', 'hash': '1' * 64}], 'codex-image')

    def test_manual_references_rejected_before_export_or_claim(self):
        payload = self.payload('midjourney-manual')
        payload['references'] = [{'input_ref': 'ref', 'role': 'reference', 'hash': '0'*64}]
        self.assertEqual(self.client.post('/bridge/v1/tasks', json=payload).status_code, 422)
        with self.assertRaises(DomainError):
            self.store.create_task(self.scope, payload)
        request = ExecutionRequest('task', 'attempt', 'test', payload['parameters'], tuple(payload['references']))
        executor = MidjourneyManualExecutor(self.root / 'manual')
        with self.assertRaises(ValueError):
            executor.execute(request)
        with self.assertRaises(ValueError):
            executor.import_original(request, self.root / 'missing', confirmed_request_sha256=request.digest,
                                     confirmed_single_original=True)
        self.assertFalse(executor.root.exists())

    def test_reference_limit_also_applies_to_direct_execution(self):
        request = ExecutionRequest('task', 'attempt', 'test', {}, tuple({} for _ in range(9)))
        for cls in (FakeExecutor, CodexExecutor):
            with self.assertRaises(ValueError):
                cls(self.root / cls.__name__).execute(request)

    def test_legacy_wire_digest_and_attempt_binding_stay_unchanged(self):
        payload = self.payload()
        payload['parameters'] = {}
        self.assertEqual(TaskCreate.model_validate(payload).model_dump(exclude_unset=True), payload)
        first = self.store.create_task(self.scope, payload)
        response = self.client.post('/bridge/v1/tasks', json=payload)
        self.assertEqual(response.json()['remote_task_id'], first['remote_task_id'])
        request = ExecutionRequest('task', 'attempt', 'test')
        legacy = {'task_id': 'task', 'attempt_id': 'attempt', 'prompt': 'test', 'parameters': {}, 'references': []}
        digest = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        path = self.root / 'local/task/attempt'
        path.mkdir(parents=True)
        (path / 'binding.json').write_text(json.dumps({'request_sha256': digest, 'executor': 'fake-image'}))
        self.assertEqual(request.payload(), legacy)
        self.assertEqual(request.digest, digest)
        result = FakeExecutor(self.root / 'local').execute(request)
        self.assertEqual(result.artifact.path.read_bytes(), fixture_png())
        with patch('render_bridge.executors.fake.fixture_png', side_effect=AssertionError('rerender')):
            self.assertEqual(FakeExecutor(self.root / 'local').execute(request), result)

    def test_fake_formats_are_deterministic_originals_and_replay_without_render(self):
        for fmt, fixture in (('png', fixture_png), ('jpeg', fixture_jpeg)):
            request = ExecutionRequest('task-'+fmt, 'attempt', 'test', {'width': 32, 'height': 24, 'image_options': options(fmt)})
            executor = FakeExecutor(self.root / 'local')
            result = executor.execute(request)
            self.assertEqual(result.artifact.path.read_bytes(), fixture(32, 24))
            self.assertEqual(result.artifact.mime_type, 'image/'+fmt)
            with patch('render_bridge.executors.fake.fixture_png', side_effect=AssertionError('rerender')), \
                 patch('render_bridge.executors.fake.fixture_jpeg', side_effect=AssertionError('rerender')):
                self.assertEqual(executor.execute(request), result)
            task = self.store.create_task(self.scope, self.payload(fmt=fmt, key=fmt))
            outcome = run_fake_once(self.store, self.scope, task['remote_task_id'], node_id='node',
                executor_instance='executor', start_log=self.root / (fmt+'.log'))
            self.assertEqual(outcome['receipt']['mime_type'], 'image/'+fmt)

    def test_server_preserves_codex_original_even_when_source_format_was_requested(self):
        payload = self.payload('codex-image', 'jpeg')
        task = self.store.create_task(self.scope, payload)['remote_task_id']
        claim = self.store.claim_task(self.scope, task, node_id='node', executor_instance='executor')
        self.store.start_execution(self.scope, task, claim['claim_id'])
        content = fixture_png(32, 24)
        upload = self.store.create_upload(self.scope, task, claim['claim_id'], {
            'attempt_id': claim['attempt_id'], 'slot_id': 'primary', 'original_sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content), 'mime_type': 'image/png'})
        for _ in range(2):
            result = self.store.put_upload_content(self.scope, upload['upload_id'], claim['claim_id'], content)
            self.assertEqual(result['state'], 'ready')
        committed = self.store.commit_upload(self.scope, upload['upload_id'], claim['claim_id'])
        self.assertEqual(committed['mime_type'], 'image/png')
        self.assertTrue(committed['receipt_id'])
        self.assertEqual(self.store.get_task(self.scope, task)['receipt_id'], committed['receipt_id'])

    def test_codex_default_format_keeps_original_bytes_and_cannot_replace_them(self):
        request = ExecutionRequest('task', 'attempt', 'test', {'image_options': options('jpeg')})
        files = AttemptFiles(self.root / 'local', request, 'codex-cli')
        result = files.save(fixture_png(), 'fixture')
        self.assertEqual((result.status, result.code), ('ready', 'artifact_ready'))
        self.assertEqual(result.artifact.path.read_bytes(), fixture_png())
        manifest = json.loads((files.path / 'artifact.json').read_text())
        manifest['mime_type'] = 'image/jpeg'  # Cached metadata is not decoded format evidence.
        (files.path / 'artifact.json').write_text(json.dumps(manifest))
        self.assertEqual(files.cached(), result)
        with self.assertRaises(ValueError):
            files.save(fixture_jpeg(), 'converted')

    def test_manual_export_and_import_respect_frozen_options(self):
        for fmt, fixture in (('png', fixture_png), ('jpeg', fixture_jpeg)):
            request = ExecutionRequest('task-'+fmt, 'attempt', 'test', {'image_options': options(fmt)})
            executor = MidjourneyManualExecutor(self.root / 'manual')
            exported = executor.execute(request)
            self.assertEqual(json.loads(Path(exported.details['task_file']).read_text())['parameters'], request.parameters)
            source = self.root / ('misleading.' + ('jpeg' if fmt == 'png' else 'png'))
            source.write_bytes(fixture())
            result = executor.import_original(request, source, confirmed_request_sha256=request.digest,
                                              confirmed_single_original=True)
            self.assertEqual(result.status, 'ready')
            self.assertEqual(result.artifact.mime_type, 'image/'+fmt)
            self.assertEqual(executor.execute(request), result)
        request = ExecutionRequest('mismatch', 'attempt', 'test', {'image_options': options('jpeg')})
        executor.execute(request)
        source = self.root / 'wrong.jpeg'
        source.write_bytes(fixture_png())
        result = executor.import_original(request, source, confirmed_request_sha256=request.digest,
                                          confirmed_single_original=True)
        self.assertEqual(result.code, 'output_format_mismatch')
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(result.artifact.path.read_bytes(), source.read_bytes())

    def test_descriptor_states_limits_without_native_control_claims(self):
        report = self.store.capabilities(self.scope)
        self.assertEqual(report['schema_version'], '1')
        for item in report['profiles']:
            contract = item['image_options_contract']
            self.assertEqual(contract['schema_version'], '1')
            if item['execution_profile_id'] == 'codex-image':
                self.assertEqual(contract['quality'], {'values': ['auto'], 'semantics': 'unsupported_values_normalized_to_auto'})
                self.assertEqual(contract['output_format'], {'values': [], 'semantics': 'native_default_original_artifact'})
                self.assertEqual(contract['input_fidelity'], {'values': [], 'semantics': 'gpt_image_2_default_high'})
                self.assertEqual(contract['transparent_background'], {'values': [False, True], 'semantics':
                    '0.163.0 native argument; 0.157.1 requested true normalizes to CLI default auto'})
                self.assertEqual(contract['references']['max_count'], 5)
            else:
                self.assertEqual(contract['quality'], {'values': ['auto'], 'semantics': 'unspecified_only'})
                self.assertEqual(contract['output_format'], {'values': ['png', 'jpeg'], 'semantics': 'original_artifact_postcondition'})
                self.assertEqual(contract['input_fidelity'], {'values': [], 'semantics': 'omitted_only'})
                self.assertEqual(contract['references']['max_count'], 0 if item['requires_human'] else 8)
            self.assertFalse(item['production_verified'])
