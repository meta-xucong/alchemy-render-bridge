"""Frozen legacy contracts survive reopen; native defaults require explicit opt-in."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.executors.base import AttemptFiles, ExecutionRequest
from render_bridge.executors.codex import CodexExecutor
from render_bridge.fake import fixture_png
from render_bridge.image_options import CODEX_ADAPTER, validate_image_request
from render_bridge.schemas import TaskCreate


class CodexContractUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = SourceScope('source', 'workspace')
        self.store = self.open_store()
        self.addCleanup(lambda: self.store.close())
        self.png = fixture_png()
        self.sha = hashlib.sha256(self.png).hexdigest()

    def open_store(self):
        return BridgeStore(self.root/'db', self.root/'objects', clock=lambda: 1800000000,
                           enabled_profiles=('codex-image',))

    def payload(self, parameters=None, references=None):
        return dict(schema_version='1', origin_key='legacy', binding_id='binding',
                    operation='image.generate', execution_profile_id='codex-image',
                    execution_profile_revision='1', prompt='frozen', parameters=parameters or {},
                    references=references or [], output_slot={'slot_id':'primary','count':1},
                    routing='bridge_only', deadline='2027-01-15T09:00:00+00:00')

    def legacy_cache(self, request):
        # Exact PR1 on-disk format: no adapter version, no rewrite on reopen.
        files = AttemptFiles(self.root/'local', request, 'codex-cli')
        files.write_exclusive(files.path/'artifact.original', self.png)
        files.write_exclusive(files.path/'artifact.json', json.dumps({
            'request_sha256': request.digest, 'sha256': self.sha,
            'size': len(self.png), 'mime_type':'image/png', 'width':32, 'height':32,
            'provenance':'codex_image_event_locally_unverified'}).encode())
        return files

    def test_legacy_jpeg_cache_and_pending_upload_remain_blocked_after_reopen(self):
        parameters = {'image_options':{'schema_version':'1','quality':'auto','output_format':'jpeg'}}
        request = ExecutionRequest('task','attempt','frozen',parameters)
        files = self.legacy_cache(request)
        frozen = (files.path/'artifact.json').read_bytes()
        with patch('render_bridge.executors.codex.StdioSession', side_effect=AssertionError('no new turn')):
            result = CodexExecutor(self.root/'local').execute(request)
        self.assertEqual((result.status,result.code), ('needs_attention','output_format_mismatch'))
        self.assertEqual(result.artifact.sha256,self.sha)
        self.assertEqual((files.path/'artifact.json').read_bytes(),frozen)
        task = self.store.create_task(self.scope,self.payload(parameters))
        claim = self.store.claim_task(self.scope,task['remote_task_id'],node_id='node',executor_instance='exec')
        self.store.start_execution(self.scope,task['remote_task_id'],claim['claim_id'])
        upload = self.store.create_upload(self.scope,task['remote_task_id'],claim['claim_id'],{
            'attempt_id':claim['attempt_id'],'slot_id':'primary','original_sha256':self.sha,
            'size':len(self.png),'mime_type':'image/png'})
        self.store.close(); self.store = self.open_store()
        with self.assertRaises(DomainError) as error:
            self.store.put_upload_content(self.scope,upload['upload_id'],claim['claim_id'],self.png)
        self.assertEqual(error.exception.code,'output_format_mismatch')
        self.assertIsNone(self.store.get_task(self.scope,task['remote_task_id'])['receipt_id'])
        self.assertFalse(self.store.start_execution(self.scope,task['remote_task_id'],claim['claim_id'])['may_execute'])

    def test_legacy_eight_references_replay_and_cached_recovery_survive_reopen(self):
        item = self.store.create_input(self.scope,{'input_key':'ref','original_sha256':self.sha,
                                                 'size':len(self.png),'mime_type':'image/png'})
        self.store.put_input_content(self.scope,item['input_id'],self.png)
        refs = [{'input_ref':item['input_id'],'role':'reference','hash':self.sha} for _ in range(8)]
        payload = self.payload(references=refs)
        task = self.store.create_task(self.scope,payload)
        self.store.close(); self.store = self.open_store()
        self.assertEqual(self.store.create_task(self.scope,payload)['remote_task_id'],task['remote_task_id'])
        self.assertEqual(TaskCreate.model_validate(payload).model_dump(exclude_unset=True),payload)
        request = ExecutionRequest('task','attempt','frozen',{},tuple(refs))
        self.legacy_cache(request)
        with patch('render_bridge.executors.codex.StdioSession', side_effect=AssertionError('no new turn')):
            result = CodexExecutor(self.root/'local').recover_completed(request)
        self.assertEqual(result.status,'ready')
        self.assertEqual(result.artifact.sha256,self.sha)
        with self.assertRaises(ValueError):
            validate_image_request({'codex_adapter':CODEX_ADAPTER},refs,'codex-image')

    def test_adapter_is_explicit_validated_and_part_of_frozen_identity(self):
        request = ExecutionRequest('task','attempt','frozen',{})
        self.legacy_cache(request)
        adapted = ExecutionRequest('task','attempt','frozen',{'codex_adapter':CODEX_ADAPTER})
        self.assertNotEqual(request.digest,adapted.digest)
        with self.assertRaises(ValueError):
            AttemptFiles(self.root/'local',adapted,'codex-cli')
        for value in (None,False,'unknown'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TaskCreate.model_validate(self.payload({'codex_adapter':value}))
        with self.assertRaises(ValueError):
            validate_image_request({'codex_adapter':CODEX_ADAPTER},(),'fake-image')
        # Unversioned PR2 extended options are ambiguous; do not infer an adapter.
        with self.assertRaises(ValueError):
            validate_image_request({'image_options':{'schema_version':'1','quality':'high'}},(),'codex-image')
        contract = self.store.capabilities(self.scope)['profiles'][0]['parameter_contract']
        self.assertEqual(contract['codex_adapter']['values'],[CODEX_ADAPTER])
        self.assertEqual(contract['codex_adapter']['image_options_contract']['references']['max_count'],5)
