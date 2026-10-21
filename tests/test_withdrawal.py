import copy
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from render_bridge.domain import DomainError
import test_domain


class WithdrawalTests(unittest.TestCase):
    setUp = test_domain.DomainTests.setUp
    reopen = test_domain.DomainTests.reopen
    payload = test_domain.DomainTests.payload
    task = test_domain.DomainTests.task
    started = test_domain.DomainTests.started
    assert_error = test_domain.DomainTests.assert_error

    def binding(self, task, key='withdraw-one'):
        return dict(schema_version='1', withdrawal_key=key, origin_key=task['origin_key'],
                    attempt_id=task['attempt_id'], task_digest=task['task_digest'])

    def test_proof_replay_restart_and_late_dispatch(self):
        task = self.task(); tid = task['remote_task_id']; body = self.binding(task)
        claim = self.store.claim_task(self.scope, tid, node_id='node', executor_instance='instance')
        dispatch_key = self.store.pending_dispatches(self.scope)['items'][0]['dispatch_key']
        proof = self.store.withdraw_before_start(self.scope, tid, body)
        self.store = self.reopen()
        self.assertEqual(proof, self.store.withdraw_before_start(self.scope, tid, body))
        self.assertEqual(proof, self.store.get_withdrawal(self.scope, tid, body['withdrawal_key']))
        self.assertEqual('canceled', self.store.get_task(self.scope, tid)['state'])
        self.assertEqual([], self.store.pending_dispatches(self.scope)['items'])
        self.assert_error('task_not_executable', self.store.start_execution, self.scope, tid, claim['claim_id'])
        self.assert_error('task_not_executable', self.store.ack_dispatch, self.scope, dispatch_key, 'late-engine')
        self.assertEqual('canceled', self.store.create_task(self.scope, self.payload())['state'])
        self.assert_error('withdrawal_binding_mismatch', self.store.withdraw_before_start, self.scope, tid, self.binding(task, 'other'))
        self.assert_error('not_found', self.store.get_withdrawal, self.other_source, tid, body['withdrawal_key'])

    def test_started_unknown_and_plain_canceled_are_not_proof(self):
        task, claim, _ = self.started(); tid = task['remote_task_id']
        self.assert_error('withdrawal_not_safe', self.store.withdraw_before_start, self.scope, tid, self.binding(task))
        self.store.mark_execution_unknown(self.scope, tid, claim['claim_id'])
        self.assert_error('withdrawal_not_safe', self.store.withdraw_before_start, self.scope, tid, self.binding(task))
        other = self.task('other'); oid = other['remote_task_id']; self.store.cancel_task(self.scope, oid)
        self.assert_error('withdrawal_not_safe', self.store.withdraw_before_start, self.scope, oid, self.binding(other))
        self.assert_error('not_found', self.store.get_withdrawal, self.scope, oid, 'withdraw-one')

    def test_binding_mismatch(self):
        task = self.task()
        for field in ('origin_key', 'attempt_id', 'task_digest'):
            body = self.binding(task); body[field] = '0'*64
            self.assert_error('withdrawal_binding_mismatch', self.store.withdraw_before_start, self.scope, task['remote_task_id'], body)

    def test_start_race_exactly_one_wins(self):
        for index in range(15):
            task = self.task('race-'+str(index)); tid = task['remote_task_id']
            claim = self.store.claim_task(self.scope, tid, node_id='node', executor_instance='instance')
            barrier = threading.Barrier(2)
            def run(withdraw):
                barrier.wait()
                try:
                    if withdraw: return self.store.withdraw_before_start(self.scope, tid, self.binding(task, 'w'+str(index)))['status']
                    return self.store.start_execution(self.scope, tid, claim['claim_id'])['may_execute']
                except DomainError as error: return error.code
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(run, [True, False]))
            self.assertIn(results, [['withdrawn_before_start', 'task_not_executable'], ['withdrawal_not_safe', True]])
            if results[1] is True:
                # End isolated fixture occupancy, without making that task retractable.
                with self.store._transaction() as con: con.execute("UPDATE tasks SET state='failed' WHERE id=?", (tid,))

    def test_manual_grant_blocks_but_preflight_attention_allows(self):
        self.store.enabled_profiles = ('fake-image', 'midjourney-manual')
        for started in (False, True):
            payload = self.payload('manual'+str(started)); payload['execution_profile_id'] = 'midjourney-manual'
            task = self.store.create_task(self.scope, payload); tid = task['remote_task_id']
            claim = self.store.claim_task(self.scope, tid, node_id='node', executor_instance='instance')
            if started:
                self.store.begin_manual(self.scope, tid, claim['claim_id']); self.store.cancel_task(self.scope, tid)
                self.assert_error('withdrawal_not_safe', self.store.withdraw_before_start, self.scope, tid, self.binding(task, 'started'))
            else:
                self.store.report_attention(self.scope, tid, task['attempt_id'], 'node', 'instance', 'profile_not_enabled')
                self.assertEqual('withdrawn_before_start', self.store.withdraw_before_start(self.scope, tid, self.binding(task))['status'])

    def test_manual_ready_race_exactly_one_wins(self):
        self.store.enabled_profiles = ('midjourney-manual',)
        for index in range(8):
            body = self.payload('manual-race-'+str(index)); body['execution_profile_id'] = 'midjourney-manual'
            task = self.store.create_task(self.scope, body); tid = task['remote_task_id']
            claim = self.store.claim_task(self.scope, tid, node_id='node', executor_instance='instance')
            barrier = threading.Barrier(2)
            def run(withdraw):
                barrier.wait()
                try:
                    if withdraw: return self.store.withdraw_before_start(self.scope, tid, self.binding(task, 'wm'+str(index)))['status']
                    return self.store.begin_manual(self.scope, tid, claim['claim_id'])['state']
                except DomainError as error: return error.code
            with ThreadPoolExecutor(2) as pool: results = list(pool.map(run, [True, False]))
            self.assertIn(results, [['withdrawn_before_start', 'task_not_executable'], ['withdrawal_not_safe', 'awaiting_user']])
            self.store.cancel_task(self.scope, tid)
