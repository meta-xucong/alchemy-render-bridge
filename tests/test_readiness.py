"""Offline readiness and attention contract. No external tool or account probes."""
import unittest
from unittest.mock import patch

import test_agent_profiles as fixtures
from render_bridge.domain import BridgeStore, DomainError, SourceScope
from render_bridge.runtime import RuntimeConfig
from render_bridge.agent.heartbeat import HeartbeatLoop


class ReadinessTests(unittest.TestCase):
    setUp = fixtures.AgentProfilesTests.setUp
    tearDown = fixtures.AgentProfilesTests.tearDown
    create = fixtures.AgentProfilesTests.create

    def test_absent_fresh_busy_stale_and_scope(self):
        caps = self.store.capabilities(self.scope)
        self.assertEqual(caps['node_status']['state'], 'unknown')
        self.assertTrue(all(p['availability'] == 'unknown' for p in caps['profiles']))
        self.agent._worker_running = True
        self.assertTrue(self.agent.heartbeat())
        caps = self.store.capabilities(self.scope)
        self.assertEqual(caps['node_status']['state'], 'available')
        self.assertEqual({p['execution_profile_id']: p['availability'] for p in caps['profiles']},
                         {'fake-image': 'available', 'codex-image': 'attention', 'midjourney-manual': 'available'})
        self.assertFalse(caps['node_status']['login_verified'])
        self.assertFalse(caps['node_status']['real_generation_verified'])
        self.assertEqual(self.store.capabilities(SourceScope('other', 'workspace'))['node_status']['state'], 'unknown')
        self.agent._active = True
        self.agent.heartbeat()
        self.assertEqual(self.store.capabilities(self.scope)['node_status']['state'], 'busy')
        self.now += 90
        self.assertEqual(self.store.capabilities(self.scope)['node_status']['state'], 'offline')

    def test_heartbeat_is_metadata_only_and_scoped(self):
        with patch('render_bridge.executors.codex.probe_codex') as probe:
            self.agent.heartbeat()
            probe.assert_not_called()
        response = self.client.http.post('/bridge/node/v1/heartbeat',
            headers={'Authorization': 'Bearer source'}, json={'state': 'available', 'profiles': ['fake-image']})
        self.assertEqual(response.status_code, 401)
        for payload in ({'state': 'available', 'profiles': ['fake-image', 'fake-image']},
                        {'state': 'available', 'profiles': ['unknown']},
                        {'state': 'available', 'profiles': [], 'login_verified': True}):
            response = self.client.http.post('/bridge/node/v1/heartbeat', json=payload)
            self.assertIn(response.status_code, (400, 422))

    def test_prestart_attention_persists_and_same_task_resumes(self):
        task = self.create('fake-image')
        executor = self.registry.executors.pop('fake-image')
        result = self.agent.execute_task(task)
        self.assertTrue(result['attention_reported'])
        view = self.store.get_task(self.scope, task)
        self.assertEqual(view['state'], 'awaiting_user')
        self.assertIsNone(view['action_required'])
        self.assertEqual(view['action_required_detail']['code'], 'profile_not_enabled')
        reopened = BridgeStore(self.root/'ledger.db', self.root/'blobs', clock=lambda: self.now,
            enabled_profiles=['fake-image', 'codex-image', 'midjourney-manual'])
        self.assertEqual(reopened.get_task(self.scope, task)['action_required_detail'], view['action_required_detail'])
        self.registry.executors['fake-image'] = executor
        self.assertEqual(self.agent.execute_task(task)['outcome'], 'artifact_committed')
        self.assertIsNone(self.store.get_task(self.scope, task)['action_required_detail'])
        self.assertEqual(self.agent.execute_task(task)['outcome'], 'already_committed')
        self.assertEqual(self.store.capabilities(self.scope)['node_status']['state'], 'attention')

    def test_manual_resume_clears_prestart_attention(self):
        from render_bridge.fake import fixture_png
        task = self.create('midjourney-manual')
        attempt = self.client.task(task)['attempt_id']
        executor = self.registry.executors.pop('midjourney-manual')
        self.assertEqual(self.agent.execute_task(task)['code'], 'profile_not_enabled')
        self.registry.executors['midjourney-manual'] = executor
        exported = self.agent.execute_task(task)
        self.assertEqual(exported['outcome'], 'awaiting_user')
        view = self.client.task(task)
        self.assertEqual(view['attempt_id'], attempt)
        self.assertEqual(view['action_required'], 'import_original')
        self.assertEqual(view['action_required_detail']['code'], 'import_original')
        source = self.root / 'original.png'
        source.write_bytes(fixture_png(32, 24))
        result = self.agent.manual_import(task, source,
            confirmed_request_sha256=exported['request_sha256'], confirmed_single_original=True)
        self.assertEqual(result['outcome'], 'artifact_committed')
        self.assertEqual(self.client.task(task)['attempt_id'], attempt)

    def test_poststart_attention_never_reexecutes(self):
        task = self.create('fake-image')
        executor = self.registry.executors['fake-image']
        with patch.object(executor, 'execute', side_effect=RuntimeError('private text')) as execute:
            self.assertEqual(self.agent.execute_task(task)['outcome'], 'execution_unknown')
            view = self.store.get_task(self.scope, task)
            self.assertEqual(view['state'], 'execution_unknown')
            self.assertEqual(view['action_required_detail']['code'], 'execution_requires_reconciliation')
            self.assertNotIn('private text', str(view))
            self.agent.execute_task(task)
            self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.store.capabilities(self.scope)['node_status']['state'], 'busy')

    def test_attention_rejects_wrong_attempt_and_does_not_revive_cancel(self):
        task = self.create('fake-image')
        response = self.client.http.post(f'/bridge/node/v1/tasks/{task}/attention',
            json={'attempt_id': 'wrong', 'code': 'insufficient_disk'})
        self.assertEqual(response.status_code, 409)
        view = self.store.cancel_task(self.scope, task)
        self.client.attention(task, view['attempt_id'], 'insufficient_disk')
        self.assertEqual(self.store.get_task(self.scope, task)['state'], 'canceled')
        self.assertIsNone(self.store.get_task(self.scope, task)['action_required_detail'])

    def test_manual_legacy_action_is_preserved_with_detail(self):
        task = self.create('midjourney-manual')
        self.agent.execute_task(task)
        view = self.store.get_task(self.scope, task)
        self.assertEqual(view['action_required'], 'import_original')
        self.assertEqual(view['action_required_detail']['code'], 'import_original')

    def test_draining_blocks_only_new_tasks_not_replay_execution_or_ack(self):
        task = self.create('fake-image')
        payload = self.store.get_task(self.scope, task)['request']
        self.store.accepting_new_tasks = False
        self.assertEqual(self.store.capabilities(self.scope)['admission'],
            {'accepting_new_tasks': False, 'mode': 'draining'})
        self.assertEqual(self.store.create_task(self.scope, payload)['remote_task_id'], task)
        with self.assertRaises(DomainError) as error:
            self.create('fake-image', 'new-task')
        self.assertEqual(error.exception.code, 'bridge_draining')
        self.assertEqual(self.agent.execute_task(task)['outcome'], 'artifact_committed')
        view = self.store.get_task(self.scope, task)
        receipt = self.store.get_receipt(self.scope, view['receipt_id'])
        ack = self.store.ack_receipt(self.scope, receipt['receipt_id'], {
            'client_receipt_id': 'client', 'decision': 'accepted', 'reason_code': 'fixture',
            'original_hash': receipt['original_sha256']})
        self.assertEqual(ack['delivery_ack'], 'accepted')

    def test_failed_heartbeat_retries_quickly_and_success_restores_normal_interval(self):
        class StopAfterThreeWaits:
            def __init__(self):
                self.delays = []

            def wait(self, delay):
                self.delays.append(delay)
                return len(self.delays) == 3

        loop = HeartbeatLoop(self.agent)
        loop.stop = StopAfterThreeWaits()
        with patch.object(self.agent, 'heartbeat', side_effect=[False, True]):
            loop._run(5)
        self.assertEqual(loop.stop.delays, [5, 5, 30])
    def test_configuration_boolean_and_heartbeat_interval_are_bounded(self):
        cfg = RuntimeConfig(self.root, 'source', 'workspace', 'node', 'instance', accepting_new_tasks='false')
        with self.assertRaises(ValueError):
            cfg.validate()
        for interval in (0, 1000):
            with self.assertRaises(ValueError):
                HeartbeatLoop(self.agent, interval)
        with patch.object(self.agent, 'heartbeat') as heartbeat:
            with HeartbeatLoop(self.agent):
                heartbeat.assert_called_once()
