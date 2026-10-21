import unittest
import test_agent_profiles


class RoutingWireTests(unittest.TestCase):
    setUp = test_agent_profiles.AgentProfilesTests.setUp
    tearDown = test_agent_profiles.AgentProfilesTests.tearDown
    create = test_agent_profiles.AgentProfilesTests.create

    def test_withdrawal_api_scope_strictness_and_query(self):
        tid = self.create('fake-image'); task = self.store.get_task(self.scope, tid)
        url = '/bridge/v1/tasks/'+tid+'/withdraw-before-start'
        body = dict(schema_version='1', withdrawal_key='w1', origin_key=task['origin_key'],
                    attempt_id=task['attempt_id'], task_digest=task['task_digest'])
        http = self.client.http; headers = {'Authorization': 'Bearer source'}
        self.assertEqual(401, http.post(url, json=body).status_code)
        self.assertEqual(422, http.post(url, json={**body, 'extra': True}, headers=headers).status_code)
        proof = http.post(url, json=body, headers=headers)
        self.assertEqual(200, proof.status_code, proof.text)
        from render_bridge.schemas import WithdrawalProof
        WithdrawalProof.model_validate(proof.json())
        self.assertEqual(proof.json(), http.post(url, json=body, headers=headers).json())
        self.assertEqual(proof.json(), http.get(url+'/w1', headers=headers).json())
        self.assertEqual(404, http.get(url+'/missing', headers=headers).status_code)
        self.assertEqual('canceled', http.get('/bridge/v1/tasks/'+tid, headers=headers).json()['state'])

    def test_health_api_bounds_and_legacy_heartbeat(self):
        http = self.client.http
        item = dict(profile_id='fake-image', profile_revision='1', report_revision=1,
                    health='available', reason_code='ready', evidence_kind='local_probe')
        body = dict(state='available', profiles=['fake-image'], profile_health=[item])
        self.assertEqual(200, http.post('/bridge/node/v1/heartbeat', json=body).status_code)
        caps = http.get('/bridge/v1/capabilities', headers={'Authorization':'Bearer source'}).json()
        self.assertEqual('available', caps['routing_availability_v1']['profiles'][0]['health'])
        from render_bridge.schemas import RoutingAvailability
        RoutingAvailability.model_validate(caps['routing_availability_v1'])
        for update in ({'report_revision':True}, {'reason_code':'raw secret'}, {'extra':'not allowed'}):
            response = http.post('/bridge/node/v1/heartbeat', json={**body,'profile_health':[{**item,**update}]})
            self.assertEqual(422, response.status_code, response.text)
        self.assertEqual(200, http.post('/bridge/node/v1/heartbeat', json={'state':'available','profiles':['fake-image']}).status_code)
        self.assertEqual('unknown', self.store.routing_availability(self.scope)['profiles'][0]['health'])
