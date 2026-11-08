"""Real Bridge ledger admission, never native inference."""
from dataclasses import replace
import copy
import tempfile
from pathlib import Path
import unittest
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.harness import HarnessBinding, HarnessConfig, digest
from test_harness import config, envelope

class LocalTrialTests(unittest.TestCase):
    def setUp(self):
        self.binding=HarnessBinding(binding_id='wb',revision=1,model_alias='workbuddy-test',native_model='test-model')
        self.config=config(bindings=(self.binding,),execution_mode='workbuddy_local_trial')
    def test_explicit_mode_single_owner(self):
        with self.assertRaises(ValueError): replace(self.config,execution_mode='synthetic')
        with self.assertRaises(ValueError): replace(self.config,execution_mode='production')
        with self.assertRaises(ValueError): replace(self.config,allowed_owners=(('a','b'),('c','d')))
        with self.assertRaises(ValueError): replace(config(),execution_mode='workbuddy_local_trial')
    def payload(self):
        p=envelope(); p['request']['binding']=self.binding.wire()
        p['request']['client_request']['model']=self.binding.model_alias
        p['request_digest']=digest(p['request']['client_request']); return p
    def test_single_user_admission_and_replay(self):
        with tempfile.TemporaryDirectory() as root:
            store=BridgeStore(Path(root)/'ledger.db',Path(root)/'objects',dispatch_mode='node_pull',harness=self.config)
            scope=SourceScope('source','workspace'); store.provision_node(scope,'node','executor')
            store.report_harness_heartbeat(scope,'node','executor',{'state':'available','binding_digests':[digest(self.binding.wire())]})
            p=self.payload()
            # Explicit multi-message request rejected before task creation.
            bad=copy.deepcopy(p); bad['request']['client_request']['messages'].insert(0,{'role':'assistant','content':'old'})
            bad['request']['messages']=copy.deepcopy(bad['request']['client_request']['messages'])
            bad['request_digest']=digest(bad['request']['client_request'])
            with self.assertRaises(DomainError): store.create_harness_task(scope,bad)
            accepted=store.create_harness_task(scope,p)
            store.harness=replace(self.config,accepting_new=False,bindings=())
            replay=store.create_harness_task(scope,p)
            self.assertEqual(accepted['remote_task_id'],replay['remote_task_id'])

if __name__=='__main__': unittest.main()
