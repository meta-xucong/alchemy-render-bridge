"""Verify outbox handoff shape without a Hatchet account or engine."""
import types
import unittest

from render_bridge.agent.hatchet_sidecar import TASK_NAME, dispatch_once, make_submitter


class Outbox:
    def __init__(self):
        self.items = [{'task_id': 'task_one', 'dispatch_key': 'dispatch_one'}]
        self.acks = []
    def pending(self):
        return self.items
    def acknowledge(self, key, run_id):
        self.acks.append((key, run_id))
        self.items = []


class DispatchTests(unittest.TestCase):
    def test_success_acknowledges_engine_run(self):
        outbox = Outbox()
        self.assertEqual(dispatch_once(outbox, lambda task, key: 'run_one'), 1)
        self.assertEqual(outbox.acks, [('dispatch_one', 'run_one')])
        self.assertEqual(dispatch_once(outbox, lambda *args: self.fail()), 0)

    def test_unknown_submit_keeps_same_dispatch_pending(self):
        outbox = Outbox()
        def fail(*args):
            raise ConnectionError()
        with self.assertRaises(ConnectionError):
            dispatch_once(outbox, fail)
        self.assertEqual(outbox.acks, [])
        self.assertEqual(outbox.items[0]['task_id'], 'task_one')

    def test_pinned_sdk_stub_interface_does_not_execute_a_callback(self):
        calls = []
        class Stub:
            def run(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(workflow_run_id='run_one')
        class Stubs:
            def task(self, **kwargs):
                self.options = kwargs
                return Stub()
        stubs = Stubs()
        submit = make_submitter(types.SimpleNamespace(stubs=stubs))
        self.assertEqual(submit('task_one', 'dispatch_one'), 'run_one')
        self.assertEqual(stubs.options['name'], TASK_NAME)
        self.assertFalse(calls[0]['wait_for_result'])
        self.assertEqual(calls[0]['input'].model_dump(), {'task_id': 'task_one'})
        self.assertEqual(calls[0]['additional_metadata'], {'bridge_dispatch_key': 'dispatch_one'})
