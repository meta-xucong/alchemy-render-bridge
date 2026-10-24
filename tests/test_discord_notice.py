"""Mock-only Discord notices; no live service, credential or paid generation."""
import hashlib
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

import test_agent_profiles as fixtures
from render_bridge.agent import NodeAgent
from render_bridge.agent.discord_notice import DiscordTaskNotifier
from render_bridge.agent.__main__ import main
from render_bridge.executors.base import ExecutionRequest
from render_bridge.fake import fixture_png

URL = 'https://discord.com/api/webhooks/123456/mock-only-token'


class DiscordNoticeTests(unittest.TestCase):
    setUp = fixtures.AgentProfilesTests.setUp
    tearDown = fixtures.AgentProfilesTests.tearDown
    create = fixtures.AgentProfilesTests.create

    def prepare(self):
        task = self.create('midjourney-manual')
        exported = self.agent.execute_task(task)
        return task, exported['request_sha256']

    def notifier(self, handler):
        return DiscordTaskNotifier(URL, transport=httpx.MockTransport(handler))

    def notify(self, task, digest, notifier, agent=None):
        return (agent or self.agent).manual_notify(task, notifier, confirmed_request_sha256=digest)

    def test_full_attachments_mentions_and_restart_replay(self):
        task, digest = self.prepare()
        seen = []
        def send(request):
            seen.append(request)
            saved = self.agent.journal.get(task)
            self.assertEqual(saved['discord_notification']['outcome'], 'notification_unknown')
            self.assertEqual(saved['state'], 'awaiting_user')
            self.assertEqual(str(request.url), URL + '?wait=true')
            mime = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + request.headers['Content-Type'] + '\r\n\r\n').encode() + request.content)
            parts = {p.get_param('name', header='content-disposition'): p.get_payload(decode=True)
                     for p in mime.iter_parts()}
            payload = json.loads(parts['payload_json'])
            self.assertLessEqual(len(payload['content']), 2000)
            self.assertEqual(payload['allowed_mentions'], {'parse': [], 'users': [], 'roles': []})
            frozen = json.loads(parts['files[1]'])
            self.assertEqual(parts['files[0]'], saved['request']['prompt'].encode())
            self.assertEqual(frozen['parameters'], saved['request']['parameters'])
            self.assertEqual(frozen['request_sha256'], digest)
            self.assertEqual(frozen['prompt_sha256'], hashlib.sha256(parts['files[0]']).hexdigest())
            return httpx.Response(200, json={'id': '987654321'})
        notifier = self.notifier(send)
        first = self.notify(task, digest, notifier)
        self.assertEqual(first['outcome'], 'notification_delivered')
        reopened = NodeAgent(self.client, self.state, registry=self.registry)
        self.assertEqual(self.notify(task, digest, notifier, reopened), first)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('mock-only-token', json.dumps(reopened.journal.get(task)))
        self.assertFalse(self.agent.journal.occupied_by_other('other'))
        source = self.root / 'chosen.png'
        source.write_bytes(fixture_png(32, 24))
        self.assertEqual(reopened.manual_import(task, source, confirmed_request_sha256=digest,
            confirmed_single_original=True)['outcome'], 'artifact_committed')

    def test_failed_unknown_responses_never_resend(self):
        for index, response in enumerate([
            httpx.Response(429, headers={'Retry-After': '2.5'}),
            httpx.Response(403, text='private body'),
            httpx.Response(500), httpx.Response(200, json={}),
            httpx.Response(200, text='bad json'), httpx.Response(204),
            httpx.Response(302, headers={'Location': 'https://example.com'}),
        ]):
            with self.subTest(status=response.status_code):
                task = self.create('midjourney-manual', origin='failure-'+str(index))
                digest = self.agent.execute_task(task)['request_sha256']
                calls = []
                notifier = self.notifier(lambda request: calls.append(request) or response)
                result = self.notify(task, digest, notifier)
                expected = 'notification_failed' if response.status_code in (403, 429) else 'notification_unknown'
                self.assertEqual(result['outcome'], expected)
                if response.status_code == 429:
                    self.assertEqual(result['retry_after_seconds'], 2.5)
                self.assertEqual(self.notify(task, digest, notifier), result)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('private body', json.dumps(result))
                self.store.cancel_task(self.scope, task)

    def test_timeout_and_crash_latch(self):
        task, digest = self.prepare()
        def timeout(request):
            raise httpx.ReadTimeout('secret URL ' + URL)
        result = self.notify(task, digest, self.notifier(timeout))
        self.assertEqual(result['outcome'], 'notification_unknown')
        reopened = NodeAgent(self.client, self.state, registry=self.registry)
        with patch('httpx.Client.post', side_effect=AssertionError('must not resend')) as post:
            self.assertEqual(self.notify(task, digest, self.notifier(timeout), reopened), result)
            post.assert_not_called()
        self.assertNotIn(URL, json.dumps(reopened.journal.get(task)))

    def test_failed_latch_write_never_sends(self):
        task, digest = self.prepare()
        with patch.object(self.agent.journal, 'save', side_effect=OSError('disk full')), \
             patch('httpx.Client.post') as post:
            with self.assertRaises(OSError):
                self.notify(task, digest, self.notifier(lambda _: self.fail('not sent')))
            post.assert_not_called()

    def test_confirmation_change_and_missing_export_block(self):
        task = self.create('midjourney-manual')
        notifier = self.notifier(lambda _: self.fail('not sent'))
        with self.assertRaises(ValueError):
            self.notify(task, 'hash', notifier)
        digest = self.agent.execute_task(task)['request_sha256']
        saved = self.agent.journal.get(task)
        saved['request']['prompt'] = 'changed'
        self.agent.journal.save(saved)
        with self.assertRaises(ValueError):
            self.notify(task, digest, notifier)

    def test_process_exit_after_latch_keeps_unknown(self):
        task, digest = self.prepare()
        def interrupted(request):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.notify(task, digest, self.notifier(interrupted))
        reopened = NodeAgent(self.client, self.state, registry=self.registry)
        with patch('httpx.Client.post') as post:
            self.assertEqual(self.notify(task, digest, self.notifier(interrupted), reopened)['outcome'],
                             'notification_unknown')
            post.assert_not_called()

    def test_wrong_task_confirmation_cancel_and_disabled_profile_block(self):
        task, digest = self.prepare()
        notifier = self.notifier(lambda _: self.fail('No HTTP authorized'))
        with self.assertRaises(ValueError):
            self.notify(task, 'wrong', notifier)
        self.registry.executors.pop('midjourney-manual')
        self.assertEqual(self.notify(task, digest, notifier)['code'], 'profile_not_enabled')
        self.store.cancel_task(self.scope, task)
        self.assertEqual(self.notify(task, digest, notifier)['outcome'], 'not_executable')

    def test_local_export_import_never_notifies(self):
        with patch.dict('os.environ', {'BRIDGE_DISCORD_WEBHOOK_URL': URL}), \
             patch('httpx.Client.post', side_effect=AssertionError('unexpected Discord')) as post:
            task, digest = self.prepare()
            source = self.root / 'chosen.png'
            source.write_bytes(fixture_png(32, 24))
            self.assertEqual(self.agent.manual_import(task, source, confirmed_request_sha256=digest,
                confirmed_single_original=True)['outcome'], 'artifact_committed')
            post.assert_not_called()

    def test_cli_requires_explicit_enable(self):
        with self.assertRaises(SystemExit) as result:
            main(['--state-dir', str(self.state), 'manual-notify', 'task', '--request-sha256', 'hash'])
        self.assertEqual(result.exception.code, 2)

    def test_long_unicode_prompt_not_truncated_and_reference_sharing_rejected(self):
        for size in (1999, 2000, 2001, 12000):
            request = ExecutionRequest('task', 'attempt', '@everyone 中文🚀' + '字' * (size - len('@everyone 中文🚀')), {'width': 32, 'height': 24})
            prompt, attachment = DiscordTaskNotifier.attachments(request)
            self.assertEqual(prompt, request.prompt.encode())
            self.assertEqual(json.loads(attachment)['prompt'], request.prompt)
        with self.assertRaises(ValueError):
            DiscordTaskNotifier.attachments(ExecutionRequest('task', 'attempt', 'prompt', references=({'path': '/private'},)))
        with self.assertRaises(ValueError):
            DiscordTaskNotifier.attachments(ExecutionRequest('task', 'attempt', '字' * 400000))

    def test_url_restrictions_do_not_echo_credentials(self):
        for value in (None, '', 'http://discord.com/api/webhooks/1/secret',
                      URL + '?wait=false', URL + '/messages/123',
                      'https://evil.example/api/webhooks/1/secret',
                      'https://discord.com@evil.example/api/webhooks/1/secret'):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                DiscordTaskNotifier(value)
            self.assertNotIn('secret', str(error.exception))
