"""Full lifecycle through a fixed local Python fixture, never the real CLI."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.executors import CodexConfig, CodexExecutor, ExecutionRequest, StdioSession
from render_bridge.fake import fixture_png


class CodexLifecycleTests(unittest.TestCase):
    def run_fixture(self, image):
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
  assert method=='thread/start' and p['approvalPolicy']=='untrusted'
  assert p['sandbox']=='workspace-write' and p['model']=='local-accepted-model'
  emit({'id':r['id'],'result':{'thread':{'id':'thread-fixture'}}})
 elif stage==3:
  assert method=='turn/start' and p['threadId']=='thread-fixture'
  assert p['input'][0]['text']=='原始完整 prompt'
  assert p['sandboxPolicy']['networkAccess'] is False
  assert p['sandboxPolicy']['writableRoots']==[str(pathlib.Path.cwd())]
  emit({'id':r['id'],'result':{'turn':{'id':'turn-fixture','status':'inProgress'}}})
  if IMAGE:
   original=pathlib.Path.cwd()/'generated.png'
   original.write_bytes(base64.b64decode(PNG))
   item={'type':'imageGeneration','id':'image-fixture','status':'completed','result':'','savedPath':str(original)}
  else:
   item={'type':'agentMessage','id':'message-fixture','text':'Image generated!'}
  emit({'method':'item/completed','params':{'threadId':'thread-fixture','turnId':'turn-fixture','item':item,'completedAtMs':1}})
  emit({'method':'turn/completed','params':{'threadId':'thread-fixture','turn':{'id':'turn-fixture','status':'completed','items':[item]}}})
 else: raise AssertionError('duplicate operation')
 stage+=1
'''.replace('if IMAGE:', 'if ' + repr(image) + ':').replace('base64.b64decode(PNG)', 'base64.b64decode(' + repr(base64.b64encode(fixture_png()).decode()) + ')'), encoding='utf-8')
            request = ExecutionRequest('task', 'attempt', '原始完整 prompt')
            config = CodexConfig(enabled=True, model='local-accepted-model',
                                 image_capability_verified=True, tool_policy_verified=True, timeout=3)
            executor = CodexExecutor(root / 'data', config)
            with patch('render_bridge.executors.codex.probe_codex', return_value={
                    'schema_version_matches': True, 'stdio_advertised': True}), patch(
                    'render_bridge.executors.codex.StdioSession',
                    side_effect=lambda argv, cwd, timeout: StdioSession(
                        [sys.executable, '-u', str(script)], cwd, timeout=timeout)) as launched:
                result = executor.execute(request)
                self.assertEqual(launched.call_count, 1)
                if image:
                    self.assertEqual(result.status, 'ready')
                    self.assertEqual(result.artifact.path.read_bytes(), fixture_png())
                    self.assertEqual(result.details['turn_id'], 'turn-fixture')
                else:
                    self.assertEqual(result.status, 'needs_attention')
                    self.assertEqual(result.code, 'codex_single_image_event_required')
                replay = executor.execute(request)
                self.assertEqual(replay.status, result.status)
                self.assertEqual(launched.call_count, 1)
                folder = root / 'data/task/attempt'
                self.assertEqual(json.loads((folder / 'codex-turn.json').read_text())['thread_id'], 'thread-fixture')

    def test_full_image_lifecycle_and_replay(self):
        self.run_fixture(True)

    def test_full_text_only_lifecycle_is_not_image_success(self):
        self.run_fixture(False)

    def test_enabled_without_capability_attestation_stays_disabled(self):
        with tempfile.TemporaryDirectory() as directory, patch('subprocess.Popen', side_effect=AssertionError('launched')):
            result = CodexExecutor(Path(directory), CodexConfig(enabled=True, model='x')).execute(
                ExecutionRequest('task', 'attempt', 'test'))
            self.assertEqual(result.status, 'needs_attention')
