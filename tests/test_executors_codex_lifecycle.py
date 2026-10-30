"""Full lifecycle through a fixed local Python fixture, never the real CLI."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.executors import CodexConfig, CodexExecutor, ExecutionRequest, StdioSession
from render_bridge.executors.codex import SUPPORTED_CLI_VERSIONS
from render_bridge.fake import fixture_png, fixture_jpeg


class CodexLifecycleTests(unittest.TestCase):
    def run_fixture(self, image, requested_format=None, actual_format='png', recover=False, transparent=False,
                    reference=False,
                    terminal_failure=False, cli_version='codex-cli 0.157.1', output_delta_count=0,
                    image_event='imageGeneration', image_item_id='image-fixture'):
        original_bytes = (fixture_jpeg if actual_format == 'jpeg' else fixture_png)()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'mock.py'
            script.write_text('''import sys,json,pathlib,base64
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
stage=0
def emit(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
 r=json.loads(line); method=r['method']; p=r.get('params',{})
 if stage==0:
  assert method=='initialize' and p['clientInfo']['name']=='alchemy-render-bridge'
  emit({'id':r['id'],'result':{'userAgent':'mock'}})
 elif stage==1:
  assert method=='initialized' and 'id' not in r
 elif stage==2:
  assert method=='thread/start' and p['approvalPolicy']=='on-request'
  assert p['sandbox']=='workspace-write' and p['model']=='local-accepted-model'
  assert ('transparent_background=true' in p.get('developerInstructions','')) is INVOKE_TRANSPARENT
  assert ('referenced_image_paths set to' in p.get('developerInstructions','')) is HAS_REFERENCE
  assert REF_NAME in p.get('developerInstructions','') if HAS_REFERENCE else True
  assert ('Do not use num_last_images_to_include' in p.get('developerInstructions','')) is HAS_REFERENCE
  emit({'id':r['id'],'result':{'thread':{'id':'thread-fixture'}}})
 elif stage==3:
  assert method=='turn/start' and p['threadId']=='thread-fixture'
  assert p['input'][0]['text']=='原始完整 prompt'
  assert not {'quality','output_format','input_fidelity'} & p.keys()
  assert p['sandboxPolicy']['networkAccess'] is False
  assert p['sandboxPolicy']['writableRoots']==[str(pathlib.Path.cwd())]
  emit({'id':r['id'],'result':{'turn':{'id':'turn-fixture','status':'inProgress'}}})
  for index in range(OUTPUT_DELTA_COUNT):
   emit({'method':'item/commandExecution/outputDelta','params':{'delta':'ImageGen skill output chunk'}})
  if IMAGE:
   original=pathlib.Path.cwd()/'image-fixture.png'
   original.write_bytes(base64.b64decode(PNG))
   item={'type':IMAGE_EVENT_TYPE,'id':IMAGE_ITEM_ID,'status':'completed','result':'','savedPath':str(original)}
   if IMAGE_EVENT_TYPE=='Extension': item['kind']='image_gen.generation'
  else:
   item={'type':'agentMessage','id':'message-fixture','text':'Image generated!'}
  emit({'method':'item/completed','params':{'threadId':'thread-fixture','turnId':'turn-fixture','item':item,'completedAtMs':1}})
  emit({'method':'turn/completed','params':{'threadId':'thread-fixture','turn':{'id':'turn-fixture','status':'completed','items':[item]}}})
 else: raise AssertionError('duplicate operation')
 stage+=1
'''.replace('OUTPUT_DELTA_COUNT', str(output_delta_count)).replace('IMAGE_EVENT_TYPE', repr(image_event)).replace('IMAGE_ITEM_ID', repr(image_item_id)).replace('if IMAGE:', 'if ' + repr(image) + ':').replace('base64.b64decode(PNG)', 'base64.b64decode(' + repr(base64.b64encode(original_bytes).decode()) + ')').replace('INVOKE_TRANSPARENT', repr(transparent and cli_version == 'codex-cli 0.163.0')).replace('HAS_REFERENCE', repr(reference)).replace('REF_NAME', repr('reference.png' if reference else '')), encoding='utf-8')
            reference_path = root / 'reference.png'
            references = ()
            if reference:
                reference_path.write_bytes(fixture_png())
                references = ({'path': str(reference_path), 'hash': hashlib.sha256(reference_path.read_bytes()).hexdigest()},)
            if terminal_failure:
                script.write_text(script.read_text(encoding='utf-8').replace("'status':'completed','items':[item]", "'status':'failed','items':[item],'error':{'codexErrorInfo':'usageLimitExceeded'}"), encoding='utf-8')
            parameters = {'codex_adapter': 'codex_cli_imagegen_v1'}
            if requested_format is not None or transparent:
                parameters['image_options'] = {'schema_version': '1'}
                if requested_format is not None:
                    parameters['image_options'].update({'quality': 'high', 'output_format': requested_format})
                if transparent:
                    parameters['image_options']['transparent_background'] = True
            request = ExecutionRequest('task', 'attempt', '原始完整 prompt', parameters, references)
            config = CodexConfig(enabled=True, model='local-accepted-model',
                                 accepted_cli_version=cli_version,
                                 image_capability_verified=True, tool_policy_verified=True, approval_policy='on-request', timeout=3)
            executor = CodexExecutor(root / 'data', config)
            def launch(argv, cwd, timeout):
                self.assertEqual(argv, ['codex', '-c', 'mcp_servers={}',
                                        '-c', 'plugins={}', '--enable', 'image_generation',
                                        'app-server', '--stdio'])
                return StdioSession([sys.executable, '-u', str(script)], cwd, timeout=timeout)
            with patch('render_bridge.executors.codex.probe_codex', return_value={
                    'cli_version': cli_version,
                    'schema_version_matches': True, 'stdio_advertised': True}), patch(
                    'render_bridge.executors.codex.StdioSession',
                    side_effect=launch) as launched:
                result = executor.execute(request)
                self.assertEqual(launched.call_count, 1)
                if image:
                    if image_item_id != 'image-fixture':
                        self.assertEqual(result.status, 'needs_attention')
                        self.assertEqual(result.code, 'codex_image_item_identity_mismatch')
                    else:
                        self.assertEqual(result.status, 'ready', f'{result.code}: {result.details}')
                        self.assertEqual(result.code, 'artifact_ready')
                        self.assertEqual(result.artifact.path.read_bytes(), original_bytes)
                        self.assertEqual(result.details['turn_id'], 'turn-fixture')
                        self.assertEqual(result.details['cli_version'], cli_version)
                        self.assertEqual(result.details['parameter_adaptation']['transparent_background'],
                            'native_tool_argument' if cli_version == 'codex-cli 0.163.0' and transparent else
                            'codex_cli_default_auto' if transparent else 'native_default')
                elif terminal_failure:
                    self.assertEqual(result.status, 'terminal_failure')
                    self.assertEqual(result.details['terminal_failure']['reason_code'], 'quota_exhausted')
                else:
                    self.assertEqual(result.status, 'needs_attention')
                    self.assertEqual(result.code, 'codex_single_image_event_required')
                completion_evidence = json.loads((root / 'data/task/attempt/codex-completion.json').read_text())
                self.assertEqual(completion_evidence['cli_version'], cli_version)
                if output_delta_count:
                    summary = completion_evidence['event_summary']
                    self.assertEqual(summary['event_counts']['item/commandExecution/outputDelta'], output_delta_count)
                    self.assertEqual(summary['captured_lifecycle_events'], 2)
                    self.assertEqual(summary['ignored_notifications'], output_delta_count)
                    self.assertNotIn('ImageGen skill output chunk', json.dumps(completion_evidence))
                if recover:
                    (root / 'data/task/attempt/artifact.json').unlink()
                with patch('render_bridge.executors.codex.probe_codex', side_effect=AssertionError('reprobe')):
                    replay = executor.execute(request)
                self.assertEqual(replay.status, result.status)
                self.assertEqual(replay.code, result.code)
                if image and image_item_id == 'image-fixture':
                    self.assertEqual(replay.artifact.path.read_bytes(), original_bytes)
                self.assertEqual(launched.call_count, 1)
                folder = root / 'data/task/attempt'
                self.assertEqual(json.loads((folder / 'codex-turn.json').read_text())['thread_id'], 'thread-fixture')

    def test_full_terminal_failed_turn_and_replay_without_new_cli(self):
        self.run_fixture(False, terminal_failure=True)

    def test_full_image_lifecycle_and_replay(self):
        self.run_fixture(True)

    def test_large_command_output_delta_burst_does_not_abort_image_turn(self):
        self.run_fixture(True, output_delta_count=500)

    def test_live_completion_accepts_verified_extension_image_item(self):
        self.run_fixture(True, image_event='Extension')

    def test_live_completion_rejects_item_path_identity_mismatch(self):
        self.run_fixture(True, image_item_id='another-image')

    def test_full_image_lifecycle_for_each_supported_cli_version(self):
        for version in sorted(SUPPORTED_CLI_VERSIONS):
            with self.subTest(version=version):
                self.run_fixture(True, cli_version=version)

    def test_typed_png_and_jpeg_original_lifecycles(self):
        for fmt in ('png', 'jpeg'):
            with self.subTest(fmt=fmt):
                self.run_fixture(True, requested_format=fmt, actual_format=fmt)

    def test_unsupported_format_is_defaulted_and_original_is_retained_without_second_turn(self):
        self.run_fixture(True, requested_format='jpeg', actual_format='png')

    def test_transparency_is_version_gated_and_older_cli_uses_its_default(self):
        self.run_fixture(True, transparent=True, cli_version='codex-cli 0.157.1')
        self.run_fixture(True, transparent=True, cli_version='codex-cli 0.163.0')

    def test_explicit_reference_paths_map_to_codex_native_arguments(self):
        self.run_fixture(True, reference=True)

    def test_completed_original_recovers_missing_manifest_without_execution(self):
        self.run_fixture(True, requested_format='jpeg', actual_format='jpeg', recover=True)
        self.run_fixture(True, requested_format='jpeg', actual_format='png', recover=True)

    def test_typed_refusal_is_replayed_without_fresh_turn(self):
        self.run_fixture(False, requested_format='jpeg')

    def test_full_text_only_lifecycle_is_not_image_success(self):
        self.run_fixture(False)

    def test_codex_generated_images_thread_folder_is_copied_into_bridge_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / 'codex-home'
            source = codex_home / 'generated_images' / 'thread-fixture' / 'image-fixture.png'
            source.parent.mkdir(parents=True)
            original = fixture_png()
            source.write_bytes(original)
            request = ExecutionRequest('task', 'attempt', 'prompt')
            executor = CodexExecutor(root / 'bridge-data')
            events = [
                {'method': 'item/completed', 'params': {
                    'threadId': 'thread-fixture', 'turnId': 'turn-fixture',
                    'item': {'type': 'imageGeneration', 'id': 'image-fixture',
                             'status': 'completed', 'savedPath': str(source)}}},
                {'method': 'turn/completed', 'params': {
                    'threadId': 'thread-fixture',
                    'turn': {'id': 'turn-fixture', 'status': 'completed'}}},
            ]
            with patch.dict(os.environ, {'CODEX_HOME': str(codex_home)}):
                result = executor.collect_completed(request, thread_id='thread-fixture',
                    turn_id='turn-fixture', events=events, cli_version='codex-cli 0.163.0')
                replay = executor.recover_completed(request)
            self.assertEqual(result.status, 'ready')
            self.assertEqual(result.artifact.path, root / 'bridge-data/task/attempt/artifact.original')
            self.assertEqual(result.artifact.path.read_bytes(), original)
            self.assertEqual(result.artifact.sha256, replay.artifact.sha256)
            self.assertEqual(json.loads((root / 'bridge-data/task/attempt/retention-files.json').read_text()),
                             {'original': 'artifact.original', 'sha256': result.artifact.sha256})

    def test_codex_generated_image_from_another_thread_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / 'codex-home'
            source = codex_home / 'generated_images' / 'other-thread' / 'image-fixture.png'
            source.parent.mkdir(parents=True)
            source.write_bytes(fixture_png())
            request = ExecutionRequest('task', 'attempt', 'prompt')
            executor = CodexExecutor(root / 'bridge-data')
            events = [
                {'method': 'item/completed', 'params': {
                    'threadId': 'thread-fixture', 'turnId': 'turn-fixture',
                    'item': {'type': 'imageGeneration', 'id': 'image-fixture',
                             'status': 'completed', 'savedPath': str(source)}}},
                {'method': 'turn/completed', 'params': {
                    'threadId': 'thread-fixture',
                    'turn': {'id': 'turn-fixture', 'status': 'completed'}}},
            ]
            with patch.dict(os.environ, {'CODEX_HOME': str(codex_home)}):
                result = executor.collect_completed(request, thread_id='thread-fixture',
                    turn_id='turn-fixture', events=events)
            self.assertEqual(result.status, 'needs_attention')
            self.assertEqual(result.code, 'codex_original_path_outside_owned_scope')
            self.assertFalse((root / 'bridge-data/task/attempt/artifact.original').exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO behavior only')
    def test_fifo_saved_path_fails_closed_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fifo = root / 'fifo.png'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                CodexExecutor._read_scoped_original(
                    fifo, root.resolve(), 'thread-fixture', fifo)

    def test_enabled_without_capability_attestation_stays_disabled(self):
        with tempfile.TemporaryDirectory() as directory, patch('subprocess.Popen', side_effect=AssertionError('launched')):
            result = CodexExecutor(Path(directory), CodexConfig(enabled=True, model='x',
                accepted_cli_version='codex-cli 0.163.0')).execute(
                ExecutionRequest('task', 'attempt', 'test'))
            self.assertEqual(result.status, 'needs_attention')

    def test_alpha_cli_is_rejected_before_start_even_if_probe_claims_protocol_match(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('subprocess.Popen', side_effect=AssertionError('launched')):
            executor = CodexExecutor(Path(directory), CodexConfig(enabled=True, model='x',
                accepted_cli_version='codex-cli 0.163.0',
                image_capability_verified=True, tool_policy_verified=True, approval_policy='on-request'))
            request = ExecutionRequest('task', 'attempt', 'test')
            with patch('render_bridge.executors.codex.probe_codex', return_value={
                    'cli_version': 'codex-cli 0.163.0-alpha.11',
                    'schema_version_matches': True, 'stdio_advertised': True}):
                result = executor.execute(request)
                preflight = executor.preflight(request)
            self.assertEqual(result.code, 'codex_version_not_validated')
            self.assertEqual(preflight.code, 'codex_version_not_validated')

    def test_supported_but_unaccepted_cli_version_is_rejected_before_start(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('subprocess.Popen', side_effect=AssertionError('launched')):
            executor = CodexExecutor(Path(directory), CodexConfig(enabled=True, model='x',
                accepted_cli_version='codex-cli 0.163.0', image_capability_verified=True,
                tool_policy_verified=True, approval_policy='on-request'))
            request = ExecutionRequest('task', 'attempt', 'test')
            with patch('render_bridge.executors.codex.probe_codex', return_value={
                    'cli_version': 'codex-cli 0.157.1',
                    'schema_version_matches': True, 'stdio_advertised': True}):
                result = executor.execute(request)
                preflight = executor.preflight(request)
            self.assertEqual(result.code, 'codex_version_not_validated')
            self.assertEqual(preflight.code, 'codex_version_not_validated')

    def test_allowlist_is_exactly_the_two_reviewed_tags(self):
        self.assertEqual(SUPPORTED_CLI_VERSIONS, {'codex-cli 0.157.1', 'codex-cli 0.163.0'})
