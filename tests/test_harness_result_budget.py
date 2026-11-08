"""Separate decoded text semantics from bounded JSON escape/framing overhead."""
import unittest
from pydantic import ValidationError
from render_bridge.harness import (HarnessResult, MAX_REQUEST_BYTES, MAX_RESULT_BYTES,
                                  MAX_RESULT_WIRE_BYTES, canonical)
import test_harness as fixtures


def result(text):
    value = fixtures.result()
    value['output_blocks'] = [{'type': 'text', 'text': text}]
    return value


class ResultBudgetTests(unittest.TestCase):
    def test_exact_decoded_budget_ascii_escaped_and_multibyte(self):
        for text in ['a' * MAX_RESULT_BYTES, '\x00' * MAX_RESULT_BYTES,
                     '😀' * (MAX_RESULT_BYTES // 4), '汉' * (MAX_RESULT_BYTES // 3) + 'a']:
            with self.subTest(prefix=repr(text[:1])):
                self.assertEqual(len(text.encode()), MAX_RESULT_BYTES)
                validated = HarnessResult.model_validate(result(text))
                self.assertEqual(validated.output_blocks[0].text, text)
                self.assertLessEqual(len(canonical(validated.model_dump(exclude_none=True)).encode()), MAX_RESULT_WIRE_BYTES)
        self.assertEqual(MAX_REQUEST_BYTES, 65536)

    def test_aggregate_text_limit_across_text_and_refusal(self):
        value = result('a' * (MAX_RESULT_BYTES // 2))
        value['output_blocks'].append({'type': 'refusal', 'text': '\x00' * (MAX_RESULT_BYTES // 2)})
        HarnessResult.model_validate(value)
        value['output_blocks'][1]['text'] += 'a'
        with self.assertRaises(ValidationError): HarnessResult.model_validate(value)
        for text in ['a' * (MAX_RESULT_BYTES + 1), '😀' * (MAX_RESULT_BYTES // 4) + 'a']:
            with self.assertRaises(ValidationError): HarnessResult.model_validate(result(text))


class ResultBudgetHTTPTests(unittest.TestCase):
    setUp = fixtures.HarnessHTTPTests.setUp
    new_store = fixtures.HarnessTests.new_store
    heartbeat = fixtures.HarnessTests.heartbeat
    request = fixtures.HarnessHTTPTests.request

    def begin(self):
        task = self.request('POST', '/bridge/v1/tasks', fixtures.envelope()).json()
        prefix = '/bridge/node/v1/tasks/' + task['remote_task_id']
        claim = self.request('POST', prefix + '/claim', {'mode': 'execute'}, True).json()
        grant = self.request('POST', prefix + '/start', {'claim_id': claim['claim_id']}, True).json()
        return task, prefix, {'claim_id': claim['claim_id'], 'start_grant_id': grant['start_grant_id']}

    def test_max_escaped_result_submission_and_source_readback(self):
        task, prefix, payload = self.begin()
        payload['result'] = result('\x00' * MAX_RESULT_BYTES)
        body = canonical(payload).encode()
        self.assertGreater(len(body), MAX_RESULT_BYTES + 4096)
        response = self.http.post(prefix + '/harness-result', content=body,
            headers={'Authorization': 'Bearer node-test', 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertEqual(response.json()['result'], payload['result'])
        replay = self.request('POST', prefix + '/harness-result', payload, True)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), response.json())
        fetched = self.request('GET', '/bridge/v1/tasks/' + task['remote_task_id'])
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()['result'], payload['result'])
        self.assertEqual(int(fetched.headers['content-length']), len(fetched.content))
        self.assertLessEqual(len(fetched.content), MAX_RESULT_WIRE_BYTES + 128 * 1024)

    def test_http_wire_overlimit_and_semantic_overlimit_do_not_commit(self):
        task, prefix, payload = self.begin()
        payload['result'] = result('😀' * (MAX_RESULT_BYTES // 4) + 'a')
        response = self.request('POST', prefix + '/harness-result', payload, True)
        self.assertEqual(response.status_code, 422, response.text[:300])
        oversized = b' ' * (MAX_RESULT_WIRE_BYTES + 4097)
        response = self.http.post(prefix + '/harness-result', content=oversized,
            headers={'Authorization': 'Bearer node-test', 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 413, response.text[:300])
        self.assertIsNone(self.request('GET', '/bridge/v1/tasks/' + task['remote_task_id']).json()['result'])

    def test_admission_reserves_wire_not_decoded_text_budget(self):
        from render_bridge.domain import DomainError
        store = self.new_store(max_result_storage_bytes=MAX_RESULT_WIRE_BYTES - 1)
        with self.assertRaises(DomainError) as caught:
            store.create_task(fixtures.SOURCE, fixtures.envelope())
        self.assertEqual(caught.exception.code, 'harness_storage_capacity')
        store = self.new_store(max_result_storage_bytes=MAX_RESULT_WIRE_BYTES)
        self.assertIn('remote_task_id', store.create_task(fixtures.SOURCE, fixtures.envelope()))
