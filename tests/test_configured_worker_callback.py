"""Real configured callback/Agent/HTTP with a fixture engine, not Hatchet transport."""
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_agent_profiles as fixtures
from render_bridge.agent import NodeAgent
from render_bridge.agent.hatchet_sidecar import (
    TASK_NAME, DispatchClient, dispatch_once, make_submitter, make_worker)


class FixtureEngine:
    def __init__(self):
        self.deliveries = []
        self.stubs = types.SimpleNamespace(task=self.stub)

    def stub(self, *, name, input_validator):
        if name != TASK_NAME:
            raise AssertionError('Unexpected task name')
        def run(*, input, wait_for_result, additional_metadata):
            if wait_for_result:
                raise AssertionError('Dispatch must not wait for execution')
            self.deliveries.append(input.model_dump())
            return types.SimpleNamespace(workflow_run_id='fixture-engine-run')
        return types.SimpleNamespace(run=run)

    def task(self, **options):
        self.options = options
        return lambda callback: callback

    def worker(self, name, *, slots, workflows):
        if slots != 1 or len(workflows) != 1:
            raise AssertionError('Expected configured single-slot worker')
        self.callback = workflows[0]
        return self

    def deliver(self, item):
        return self.callback(self.options['input_validator'](**item), object())


class ConfiguredWorkerCallbackTests(unittest.TestCase):
    setUp = fixtures.AgentProfilesTests.setUp
    tearDown = fixtures.AgentProfilesTests.tearDown
    create = fixtures.AgentProfilesTests.create

    def worker(self, engine, node=None):
        with patch.dict('sys.modules', {'hatchet_sdk': types.SimpleNamespace(Context=object)}):
            return make_worker(engine, node or self.agent)

    def dispatch(self, engine):
        dispatcher = DispatchClient('http://127.0.0.1:8787', 'dispatch')
        dispatcher.close()
        dispatcher.http = TestClient(self.client.http.app,
            base_url='http://127.0.0.1:8787', headers={'Authorization': 'Bearer dispatch'})
        try:
            self.assertEqual(dispatch_once(dispatcher, make_submitter(engine)), 1)
            self.assertEqual(dispatcher.pending(), [])
        finally:
            dispatcher.close()

    def test_configured_dispatch_callback_http_receipt_and_restart_replay(self):
        task = self.create('fake-image')
        engine = self.worker(FixtureEngine())
        self.dispatch(engine)
        self.assertEqual(engine.deliveries, [{'task_id': task}])
        self.assertEqual(engine.options['name'], TASK_NAME)
        self.assertEqual(engine.options['retries'], 2)
        executor = self.registry.executors['fake-image']
        with patch.object(executor, 'execute', wraps=executor.execute) as execute:
            first = engine.deliver(engine.deliveries[0])
            self.assertEqual(first['outcome'], 'artifact_committed')
            reopened = NodeAgent(self.client, self.state, registry=self.registry)
            self.worker(engine, reopened)
            self.assertEqual(engine.deliver(engine.deliveries[0])['receipt_id'], first['receipt_id'])
            execute.assert_called_once()
        view = self.client.task(task)
        self.assertEqual(view['receipt_id'], first['receipt_id'])
        self.assertEqual(view['state'], 'artifacts_ready')

    def test_configured_callback_requests_bounded_retry_then_reuses_original(self):
        task = self.create('fake-image')
        engine = self.worker(FixtureEngine())
        self.dispatch(engine)
        executor = self.registry.executors['fake-image']
        with patch.object(executor, 'execute', wraps=executor.execute) as execute:
            with patch.object(self.client, 'put_upload', side_effect=ConnectionError('fixture offline')):
                with self.assertRaisesRegex(RuntimeError, 'bounded retry'):
                    engine.deliver(engine.deliveries[0])
            record = self.agent.journal.get(task)
            self.assertEqual(record['state'], 'artifact_ready')
            reopened = NodeAgent(self.client, self.state, registry=self.registry)
            self.worker(engine, reopened)
            self.assertEqual(engine.deliver(engine.deliveries[0])['outcome'], 'artifact_committed')
            self.assertEqual(reopened.journal.get(task)['sha256'], record['sha256'])
            execute.assert_called_once()

    def test_manual_callback_returns_attention_then_explicit_same_attempt_resume(self):
        task = self.create('midjourney-manual')
        attempt = self.client.task(task)['attempt_id']
        executor = self.registry.executors.pop('midjourney-manual')
        engine = self.worker(FixtureEngine())
        self.dispatch(engine)
        result = engine.deliver(engine.deliveries[0])
        self.assertEqual(result['outcome'], 'needs_attention')
        self.assertTrue(result['attention_reported'])
        self.assertEqual(self.client.task(task)['action_required_detail']['code'], 'profile_not_enabled')
        self.registry.executors['midjourney-manual'] = executor
        # Operator redelivery only, not a new task or an automatic retry policy.
        result = engine.deliver(engine.deliveries[0])
        self.assertEqual(result['outcome'], 'awaiting_user')
        self.assertEqual(self.client.task(task)['attempt_id'], attempt)
        self.assertEqual(self.client.task(task)['action_required_detail']['code'], 'import_original')
        self.assertFalse(self.agent.journal.occupied_by_other('other'))
