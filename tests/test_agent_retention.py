"""Temporary synthetic fixtures exercise local acknowledgement-aware cleanup."""
import unittest
from unittest.mock import patch
from pathlib import Path

import test_agent_runtime
from render_bridge.agent import NodeAgent
from render_bridge.agent.maintenance import maintain_agent
from render_bridge.executors.base import ExecutionRequest


class AgentRetentionTests(unittest.TestCase):
    setUp = test_agent_runtime.AgentRuntimeTests.setUp
    tearDown = test_agent_runtime.AgentRuntimeTests.tearDown
    create = test_agent_runtime.AgentRuntimeTests.create

    def maintain(self, **kwargs):
        return maintain_agent(self.agent, retention_seconds=10, clock=lambda: self.now, **kwargs)

    def complete(self, key='task', ack=True):
        tid = self.create(key)
        self.agent.execute_task(tid, self.fake)
        record = self.agent.journal.get(tid)
        if ack:
            self.store.ack_receipt(self.scope, record['receipt_id'], {
                'client_receipt_id': key, 'decision': 'accepted', 'reason_code': 'fixture',
                'original_hash': record['sha256']})
        return tid, record

    def test_reclaims_only_managed_copies_and_never_rerenders(self):
        tid, record = self.complete()
        artifact = self.agent.journal.root / record['artifact']
        executor_dir = self.agent.journal.root / 'executors' / tid / record['attempt_id']
        unrelated = executor_dir / 'user-original.png'
        unrelated.write_bytes(b'preserve')
        self.assertEqual(self.maintain()['reclaimed'], 0)
        self.now += 10
        self.assertEqual(self.maintain()['reclaimed'], 1)
        self.assertFalse(artifact.exists())
        self.assertFalse((executor_dir / 'artifact.original').exists())
        self.assertTrue((executor_dir / 'started').exists())
        self.assertTrue((executor_dir / 'artifact.json').exists())
        self.assertEqual(unrelated.read_bytes(), b'preserve')
        self.agent = NodeAgent(self.client, self.root / 'agent')
        self.assertEqual(self.agent.execute_task(tid, self.fake)['outcome'], 'already_committed')
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(self.fake.execute(ExecutionRequest(**record['request'])).code, 'artifact_expired')

    def test_unacknowledged_and_unreachable_preserve_originals(self):
        tid, record = self.complete(ack=False)
        artifact = self.agent.journal.root / record['artifact']
        self.maintain()
        self.now += 100000
        self.assertEqual(self.maintain()['reclaimed'], 0)
        self.assertTrue(artifact.exists())
        with patch.object(self.client, 'task', side_effect=ConnectionError):
            self.assertEqual(self.maintain()['skipped'], 1)
        self.assertTrue(artifact.exists())

    def test_late_ack_recheck_failure_skips_and_unlink_failure_retries(self):
        tid, record = self.complete()
        artifact = self.agent.journal.root / record['artifact']
        self.maintain()
        self.now += 10
        with patch.object(self.client, 'task', side_effect=ConnectionError):
            self.assertEqual(self.maintain()['skipped'], 1)
        self.assertTrue(artifact.exists())
        with patch.object(Path, 'unlink', side_effect=OSError):
            self.assertEqual(self.maintain()['skipped'], 1)
        self.assertTrue(artifact.exists())
        self.assertEqual(self.agent.journal.get(tid)['state'], 'complete')
        self.assertEqual(self.maintain()['reclaimed'], 1)
        self.assertFalse(artifact.exists())
        self.assertEqual(self.agent.execute_task(tid, self.fake)['outcome'], 'already_committed')
        self.assertEqual(self.fake.calls, 1)

    def test_bad_binding_and_symlink_do_not_delete_external_file(self):
        tid, record = self.complete()
        self.maintain()
        self.now += 10
        artifact = self.agent.journal.root / record['artifact']
        outside = self.root / 'import-source.png'
        outside.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(outside)
        self.assertEqual(self.maintain()['skipped'], 1)
        self.assertTrue(outside.exists())
        self.assertTrue(artifact.is_symlink())

    def test_scan_bounded_and_unknown_excluded(self):
        one, first = self.complete('one')
        self.now += 1
        two, second = self.complete('two')
        second['state'] = 'execution_unknown'
        self.agent.journal.save(second)
        self.assertEqual(self.maintain(batch_size=1)['checked'], 1)
        self.now += 10
        self.assertEqual(self.maintain(batch_size=1)['reclaimed'], 1)
        self.assertEqual(self.maintain(batch_size=1)['checked'], 0)
        self.assertTrue((self.agent.journal.root / second['artifact']).exists())

    def test_registered_generated_original_is_reclaimed(self):
        import json
        tid, record = self.complete()
        directory = self.agent.journal.root / 'executors' / tid / record['attempt_id']
        generated = directory / 'generated-image.png'
        generated.write_bytes((directory / 'artifact.original').read_bytes())
        (directory / 'retention-files.json').write_text(json.dumps({
            'original': generated.name, 'sha256': record['sha256']}))
        self.maintain()
        self.now += 10
        self.assertEqual(self.maintain()['reclaimed'], 1)
        self.assertFalse(generated.exists())
        self.assertTrue((directory / 'retention-files.json').exists())


import test_agent_profiles
from render_bridge.fake import fixture_png


class ManualRetentionTests(unittest.TestCase):
    setUp = test_agent_profiles.AgentProfilesTests.setUp
    tearDown = test_agent_profiles.AgentProfilesTests.tearDown
    create = test_agent_profiles.AgentProfilesTests.create

    def test_manual_user_original_is_never_deleted(self):
        tid = self.create('midjourney-manual')
        exported = self.agent.execute_task(tid)
        original = self.root / 'user-import.png'
        original.write_bytes(fixture_png(32, 24))
        self.agent.manual_import(tid, original, confirmed_request_sha256=exported['request_sha256'],
                                 confirmed_single_original=True)
        record = self.agent.journal.get(tid)
        self.store.ack_receipt(self.scope, record['receipt_id'], {
            'client_receipt_id': tid, 'decision': 'rejected', 'reason_code': 'fixture',
            'original_hash': record['sha256']})
        maintain_agent(self.agent, retention_seconds=10, clock=lambda: self.now)
        self.now += 10
        self.assertEqual(maintain_agent(self.agent, retention_seconds=10, clock=lambda: self.now)['reclaimed'], 1)
        self.assertEqual(original.read_bytes(), fixture_png(32, 24))
        self.assertFalse((self.agent.journal.root / record['artifact']).exists())
        self.assertEqual(self.agent.execute_task(tid)['outcome'], 'already_committed')
