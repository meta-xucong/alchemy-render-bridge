"""Synthetic temporary fixtures only. Never operates on configured user data."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.runtime import RuntimeConfig
import test_domain


class RetentionTests(unittest.TestCase):
    setUp = test_domain.DomainTests.setUp
    reopen = test_domain.DomainTests.reopen
    payload = test_domain.DomainTests.payload
    png = staticmethod(test_domain.DomainTests.png)
    metadata = staticmethod(test_domain.DomainTests.metadata)
    input = test_domain.DomainTests.input
    task = test_domain.DomainTests.task
    started = test_domain.DomainTests.started
    ready_upload = test_domain.DomainTests.ready_upload
    assert_error = test_domain.DomainTests.assert_error

    def maintain(self, **kwargs):
        return self.store.maintain_retention(self.scope, retention_seconds=10, **kwargs)

    def acknowledged(self, key='one', decision='accepted', references=None):
        request = self.payload(key, references)
        task = self.store.create_task(self.scope, request)
        tid = task['remote_task_id']
        claim = self.store.claim_task(self.scope, tid, node_id='node', executor_instance='instance')
        grant = self.store.start_execution(self.scope, tid, claim['claim_id'])
        data = self.png()
        upload = self.store.create_upload(self.scope, tid, claim['claim_id'],
            {'attempt_id': grant['attempt_id'], 'slot_id': 'primary', **self.metadata(data)})
        self.store.put_upload_content(self.scope, upload['upload_id'], claim['claim_id'], data)
        receipt = self.store.commit_upload(self.scope, upload['upload_id'], claim['claim_id'])
        ack = {'client_receipt_id': key, 'decision': decision, 'reason_code': 'fixture',
               'original_hash': receipt['original_sha256']}
        self.store.ack_receipt(self.scope, receipt['receipt_id'], ack)
        return request, task, receipt, ack

    def test_acknowledged_expiry_preserves_identity_and_metadata(self):
        for decision in ('accepted', 'rejected'):
            request, task, receipt, ack = self.acknowledged(decision, decision)
            self.assertEqual(self.maintain()['tasks_reclaimed'], 0)
            self.now += 9
            self.assertEqual(self.maintain()['tasks_reclaimed'], 0)
            self.assertEqual(self.store.get_artifact_content(self.scope, receipt['artifact_id'])[0], self.png())
            self.now += 1
            self.assertEqual(self.maintain()['tasks_reclaimed'], 1)
            self.store = self.reopen()
            self.assertEqual(self.assert_error('artifact_expired', self.store.get_artifact_content,
                             self.scope, receipt['artifact_id']).status, 410)
            self.assertEqual(self.store.create_task(self.scope, request)['remote_task_id'], task['remote_task_id'])
            self.assertEqual(self.store.ack_receipt(self.scope, receipt['receipt_id'], ack)['delivery_ack'], decision)
            self.assertEqual(self.store.get_receipt(self.scope, receipt['receipt_id'])['original_sha256'], receipt['original_sha256'])

    def test_shared_input_and_unknown_are_protected(self):
        inp, data, _ = self.input()
        refs = [{'input_ref': inp['input_id'], 'role': 'reference', 'hash': inp['original_sha256']}]
        self.acknowledged(references=refs)
        task = self.store.create_task(self.scope, self.payload('unknown', refs))
        claim = self.store.claim_task(self.scope, task['remote_task_id'], node_id='node', executor_instance='instance')
        self.store.start_execution(self.scope, task['remote_task_id'], claim['claim_id'])
        self.maintain()
        self.now += 1000000
        self.store.cancel_task(self.scope, task['remote_task_id'])
        self.maintain()
        self.assertEqual(self.store.get_input(self.scope, inp['input_id'])['state'], 'ready')
        self.assertTrue((self.store.root / (inp['input_id'] + '.blob')).exists())

    def test_unacknowledged_output_is_never_deleted(self):
        _, claim, upload, data = self.ready_upload()
        receipt = self.store.commit_upload(self.scope, upload['upload_id'], claim['claim_id'])
        self.now += 10000000
        self.maintain()
        self.now += 10000000
        self.maintain()
        self.assertEqual(self.store.get_artifact_content(self.scope, receipt['artifact_id'])[0], data)

    def test_terminal_unstarted_releases_pins_but_queued_keeps_them(self):
        inp, _, _ = self.input()
        refs = [{'input_ref': inp['input_id'], 'role': 'reference', 'hash': inp['original_sha256']}]
        task = self.store.create_task(self.scope, self.payload('canceled', refs))
        queued = self.store.create_task(self.scope, self.payload('queued', refs))
        self.store.cancel_task(self.scope, task['remote_task_id'])
        self.maintain()
        self.now += 4000
        self.maintain()
        self.assertEqual(self.store.get_input(self.scope, inp['input_id'])['state'], 'ready')
        self.store.cancel_task(self.scope, queued['remote_task_id'])
        self.maintain()
        self.now += 10
        self.maintain()
        self.assertEqual(self.store.get_input(self.scope, inp['input_id'])['state'], 'expired')

    def test_deletion_failure_and_restart_are_retryable(self):
        _, _, receipt, _ = self.acknowledged()
        self.maintain()
        self.now += 10
        with patch.object(Path, 'unlink', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError): self.maintain()
        self.assert_error('artifact_expired', self.store.get_artifact_content, self.scope, receipt['artifact_id'])
        self.assertEqual(len(list(self.store.root.glob('*.blob'))), 1)
        self.store = self.reopen()
        self.assertEqual(self.maintain()['files_deleted'], 1)
        self.assertFalse(list(self.store.root.glob('*.blob')))

    def test_repeated_use_reclaims_byte_quota_and_active_session_capacity(self):
        # Each two-object cycle would exceed this quota without retention.
        with patch('render_bridge.domain.MAX_STORAGE_BYTES', 2 * len(self.png())):
            for index in range(105):
                inp, _, payload = self.input(str(index))
                refs = [{'input_ref': inp['input_id'], 'role': 'reference', 'hash': inp['original_sha256']}]
                self.acknowledged(str(index), references=refs)
                self.maintain()
                self.now += 3601
                self.maintain()
                self.assertFalse(list(self.store.root.glob('*.blob')))
            self.assertEqual(self.store.create_input(self.scope, payload)['input_id'], inp['input_id'])
            self.assertEqual(self.store.create_input(self.scope, payload)['state'], 'expired')

    def test_batch_scope_and_orphan_protection(self):
        for key in ('a', 'b', 'c'): self.acknowledged(key)
        orphan = self.store.root / 'unknown-original.png'
        orphan.write_bytes(b'preserve')
        self.maintain(batch_size=1)
        self.now += 10
        self.assertEqual(self.store.maintain_retention(self.other_source, retention_seconds=1)['files_deleted'], 0)
        self.assertEqual(self.maintain(batch_size=1)['tasks_reclaimed'], 1)
        self.assertEqual(len(list(self.store.root.glob('*.blob'))), 2)
        self.assertEqual(orphan.read_bytes(), b'preserve')


class RuntimeRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_lifespan_runs_maintenance_and_stops(self):
        with tempfile.TemporaryDirectory() as root:
            config = RuntimeConfig(Path(root), 's', 'w', 'n', 'e', retention_enabled=True,
                                   retention_seconds=12, maintenance_batch_size=3)
            app = config.build_app({'BRIDGE_SOURCE_TOKEN': 's', 'BRIDGE_NODE_TOKEN': 'n', 'BRIDGE_DISPATCH_TOKEN': 'd'})
            with patch.object(BridgeStore, 'maintain_retention', return_value={}) as call:
                async with app.router.lifespan_context(app):
                    for _ in range(100):
                        if call.called: break
                        await asyncio.sleep(.01)
                    call.assert_called_once_with(SourceScope('s', 'w'), retention_seconds=12, batch_size=3)
                count = call.call_count
                await asyncio.sleep(.02)
                self.assertEqual(call.call_count, count)

    async def test_defaults_disabled_and_configuration_bounded(self):
        self.assertFalse(RuntimeConfig(Path('unused'), 's', 'w', 'n', 'e').retention_enabled)
        for field, value in (('retention_enabled', 'true'), ('retention_seconds', 0),
                             ('maintenance_interval_seconds', 0), ('maintenance_batch_size', 101)):
            with self.assertRaises(ValueError):
                RuntimeConfig(Path('unused'), 's', 'w', 'n', 'e', **{field: value}).validate()
