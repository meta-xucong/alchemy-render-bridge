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
                    image_event='imageGeneration', image_item_id='image-fixture',
                    generated_under_codex_home=False, configured_codex_home=None,
                    omit_turn_completed=False, execution_timeout=3,
                    completed_item_count=1):
        original_bytes = (fixture_jpeg if actual_format == 'jpeg' else fixture_png)()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'mock.py'
            if configured_codex_home is True:
                codex_home_value = str(root / 'configured-codex-home')
            elif configured_codex_home is not None:
                codex_home_value = str(configured_codex_home)
            else:
                codex_home_value = ''
            expected_codex_home = (Path(codex_home_value).expanduser().resolve()
                                   if codex_home_value else (root / '.codex').resolve())
            script.write_text('''import sys,json,pathlib,base64,os
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
stage=0
def emit(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
 r=json.loads(line); method=r['method']; p=r.get('params',{})
 if stage==0:
  assert os.environ.get('CODEX_HOME') == EXPECTED_CODEX_HOME
  assert method=='initialize' and p['clientInfo']['name']=='alchemy-render-bridge'
  emit({'id':r['id'],'result':{'userAgent':'mock'}})
 elif stage==1:
  assert method=='initialized' and 'id' not in r
 elif stage==2:
  assert method=='thread/start' and p['approvalPolicy']=='on-request'
  assert p['sandbox']=='workspace-write' and p['model']=='local-accepted-model'
  instructions=p.get('developerInstructions','').casefold()
  assert 'current working directory' not in instructions
  assert 'do not use shell commands to locate, copy, move, or convert the image' in instructions
  assert 'savedpath' in instructions
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
   if IMAGE_UNDER_CODEX_HOME:
    original=pathlib.Path(os.environ['CODEX_HOME'])/'generated_images'/'thread-fixture'/'image-fixture.png'
    original.parent.mkdir(parents=True,exist_ok=True)
   else:
    original=pathlib.Path.cwd()/'image-fixture.png'
   original.write_bytes(base64.b64decode(PNG))
   item={'type':IMAGE_EVENT_TYPE,'id':IMAGE_ITEM_ID,'status':'completed','result':'','savedPath':str(original)}
   if IMAGE_EVENT_TYPE=='Extension': item['kind']='image_gen.generation'
  else:
   item={'type':'agentMessage','id':'message-fixture','text':'private timeout payload'}
  for index in range(COMPLETED_ITEM_COUNT):
   emit({'method':'item/completed','params':{'threadId':'thread-fixture','turnId':'turn-fixture','item':item,'completedAtMs':index+1}})
  if not OMIT_TURN_COMPLETED:
   emit({'method':'turn/completed','params':{'threadId':'thread-fixture','turn':{'id':'turn-fixture','status':'completed','items':[item]}}})
 else: raise AssertionError('duplicate operation')
 stage+=1
'''.replace('OUTPUT_DELTA_COUNT', str(output_delta_count)).replace('COMPLETED_ITEM_COUNT', str(completed_item_count)).replace('IMAGE_EVENT_TYPE', repr(image_event)).replace('IMAGE_ITEM_ID', repr(image_item_id)).replace('if IMAGE:', 'if ' + repr(image) + ':').replace('IMAGE_UNDER_CODEX_HOME', repr(generated_under_codex_home)).replace('OMIT_TURN_COMPLETED', repr(omit_turn_completed)).replace('base64.b64decode(PNG)', 'base64.b64decode(' + repr(base64.b64encode(original_bytes).decode()) + ')').replace('INVOKE_TRANSPARENT', repr(transparent and cli_version == 'codex-cli 0.163.0')).replace('HAS_REFERENCE', repr(reference)).replace('REF_NAME', repr('reference.png' if reference else '')).replace('EXPECTED_CODEX_HOME', repr(str(expected_codex_home))), encoding='utf-8')
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
                                 image_capability_verified=True, tool_policy_verified=True, approval_policy='on-request', timeout=execution_timeout)
            executor = CodexExecutor(root / 'data', config)
            def launch(argv, cwd, timeout, env=None):
                self.assertEqual(argv, ['codex', '-c', 'mcp_servers={}',
                                        '-c', 'plugins={}', '--enable', 'image_generation',
                                        'app-server', '--stdio'])
                self.assertEqual(env['CODEX_HOME'], str(expected_codex_home))
                return StdioSession([sys.executable, '-u', str(script)], cwd, timeout=timeout, env=env)
            with patch('render_bridge.executors.codex.probe_codex', return_value={
                    'cli_version': cli_version,
                    'schema_version_matches': True, 'stdio_advertised': True}), patch(
                    'render_bridge.executors.codex.StdioSession',
                    side_effect=launch) as launched, patch.dict(
                    os.environ, {'CODEX_HOME': codex_home_value}), patch(
                    'pathlib.Path.home', return_value=root):
                result = executor.execute(request)
                self.assertEqual(launched.call_count, 1)
                timeout_evidence = None
                if omit_turn_completed:
                    self.assertEqual(result.status, 'terminal_failure')
                    self.assertEqual(result.code, 'codex_turn_timed_out')
                    timeout_evidence = json.loads((root / 'data/task/attempt/codex-timeout-failure.json').read_text())
                    self.assertEqual(timeout_evidence['completed_item_shapes'], {'agentMessage': completed_item_count})
                    self.assertNotIn('private timeout payload', json.dumps(timeout_evidence))
                elif image:
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
                completion_evidence = (json.loads((root / 'data/task/attempt/codex-completion.json').read_text())
                                       if not omit_turn_completed else None)
                if completion_evidence is not None:
                    self.assertEqual(completion_evidence['cli_version'], cli_version)
                if image and completion_evidence is not None:
                    expected_shape = ('Extension:image_gen.generation'
                                      if image_event == 'Extension' else image_event)
                    self.assertEqual(completion_evidence['event_summary']['completed_item_shapes'],
                                     {expected_shape: 1})
                if output_delta_count and completion_evidence is not None:
                    summary = completion_evidence['event_summary']
                    self.assertEqual(summary['event_counts']['item/commandExecution/outputDelta'], output_delta_count)
                    self.assertEqual(summary['captured_lifecycle_events'], 2)
                    self.assertEqual(summary['ignored_notifications'], output_delta_count)
                    self.assertNotIn('ImageGen skill output chunk', json.dumps(completion_evidence))
                if recover and completion_evidence is not None:
                    (root / 'data/task/attempt/artifact.json').unlink()
                with patch('render_bridge.executors.codex.probe_codex', side_effect=AssertionError('reprobe')):
                    replay = executor.execute(request)
                self.assertEqual(replay.status, result.status)
                self.assertEqual(replay.code, result.code)
                if image and image_item_id == 'image-fixture':
                    self.assertEqual(replay.artifact.path.read_bytes(), original_bytes)
                    cache = json.loads((root / 'data/task/attempt/artifact.json').read_text())
                    self.assertNotIn('sha256', cache)
                self.assertEqual(launched.call_count, 1)
                folder = root / 'data/task/attempt'
                self.assertEqual(json.loads((folder / 'codex-turn.json').read_text())['thread_id'], 'thread-fixture')

    def test_timeout_persists_allowlisted_completed_item_shapes_without_payload(self):
        self.run_fixture(False, omit_turn_completed=True, execution_timeout=0.5,
                         completed_item_count=2)

    def test_full_terminal_failed_turn_and_replay_without_new_cli(self):
        self.run_fixture(False, terminal_failure=True)

    def test_full_image_lifecycle_and_replay(self):
        self.run_fixture(True)

    def test_default_codex_home_output_is_collected_without_environment_configuration(self):
        self.run_fixture(True, generated_under_codex_home=True)

    def test_configured_codex_home_output_is_collected_and_shared_with_child(self):
        self.run_fixture(True, generated_under_codex_home=True, configured_codex_home=True)

    def test_relative_codex_home_is_shared_as_absolute_and_output_is_collected(self):
        with tempfile.TemporaryDirectory(prefix='.tmp-codex-home-', dir=Path.cwd()) as codex_home:
            self.run_fixture(True, generated_under_codex_home=True,
                             configured_codex_home=Path(codex_home).name)

    def test_relative_codex_home_is_resolved_before_use(self):
        configured = '.codex-test-home'
        with patch.dict(os.environ, {'CODEX_HOME': configured}):
            self.assertEqual(CodexExecutor._codex_home(), Path(configured).resolve())

    def test_large_command_output_delta_burst_does_not_abort_image_turn(self):
        self.run_fixture(True, output_delta_count=500)

    def test_total_execution_timeout_releases_only_after_context_confirms_local_process_exit(self):
        for timeout_at in ('initialize', 'thread/start', 'turn/start', 'turn_wait'):
          for process_exit_code, expected in ((137, 'terminal_failure'), (None, 'execution_unknown')):
            with self.subTest(timeout_at=timeout_at, process_exit_code=process_exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = ExecutionRequest('timeout-task', 'timeout-attempt', 'timeout prompt', {}, ())
                config = CodexConfig(enabled=True, model='local-accepted-model',
                    accepted_cli_version='codex-cli 0.163.0', image_capability_verified=True,
                    tool_policy_verified=True, approval_policy='on-request', timeout=0.01)
                executor = CodexExecutor(root / 'data', config)

                class TimedOutSession:
                    events = []
                    def __init__(self, *_args, **_kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self, *_): return False
                    def request(self, method, _params):
                        if method == timeout_at:
                            raise TimeoutError('fixture setup timeout')
                        if method == 'initialize': return {'result': {}}
                        if method == 'thread/start': return {'thread': {'id': 'timeout-thread'}}
                        if method == 'turn/start': return {'turn': {'id': 'timeout-turn'}}
                        raise AssertionError(method)
                    def send(self, _message): pass
                    def receive(self, *, timeout=None):
                        if timeout_at == 'turn_wait': raise TimeoutError('fixture timeout')
                        raise AssertionError('wait only for the configured timeout stage')
                    def capture_notification(self, _event): pass
                    def diagnostic_summary(self):
                        return {'process_exit_code': process_exit_code, 'event_counts': {},
                                'captured_lifecycle_events': 0}

                with patch('render_bridge.executors.codex.probe_codex', return_value={
                        'cli_version': 'codex-cli 0.163.0', 'schema_version_matches': True,
                        'stdio_advertised': True}), patch(
                        'render_bridge.executors.codex.StdioSession', TimedOutSession):
                    result = executor.execute(request)
                    self.assertEqual(expected, result.status)
                    self.assertEqual('codex_turn_timed_out' if expected == 'terminal_failure'
                        else 'codex_turn_timeout_unconfirmed', result.code)
                    if expected == 'terminal_failure':
                        self.assertEqual('terminal_failure', executor.recover_terminal_failure(request).status)

    def test_live_completion_accepts_verified_extension_image_item(self):
        self.run_fixture(True, image_event='Extension')

    def test_extension_image_saved_under_codex_home_completes_lifecycle(self):
        self.run_fixture(True, image_event='Extension', generated_under_codex_home=True)

    def test_completed_item_shape_diagnostics_are_allowlisted_and_payload_free(self):
        session = StdioSession.__new__(StdioSession)
        session.events = []
        session.event_counts = {'item/completed': 0, 'turn/completed': 0,
                                'item/commandExecution/outputDelta': 0,
                                'other_notification': 0}
        session.completed_item_shapes = {}
        session.ignored_notifications = 0
        session.bytes_read = 0
        session.structural_tokens = 0
        session.process = type('Process', (), {'poll': lambda _self: 0})()
        session.capture_notification({'method': 'item/completed', 'params': {'item': {
            'type': 'unexpected-private-type', 'kind': 'private-kind',
            'text': 'sensitive prompt payload'}}})
        session.capture_notification({'method': 'item/completed', 'params': {'item': {
            'type': ['malformed', 'private'], 'text': 'another sensitive payload'}}})
        summary = session.diagnostic_summary()
        self.assertEqual(summary['completed_item_shapes'], {'other': 2})
        self.assertNotIn('sensitive prompt payload', json.dumps(summary))
        self.assertNotIn('another sensitive payload', json.dumps(summary))
        self.assertNotIn('unexpected-private-type', json.dumps(summary))

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
            self.assertEqual(replay.artifact.path.read_bytes(), result.artifact.path.read_bytes())
            registry = json.loads((root / 'bridge-data/task/attempt/retention-files.json').read_text())
            self.assertEqual(registry, {'original': 'artifact.original'})

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
