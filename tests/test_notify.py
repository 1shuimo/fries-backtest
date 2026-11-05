import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


spec = importlib.util.spec_from_file_location('notify', Path(__file__).resolve().parents[1] / 'tools' / 'notify.py')
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)


class NotifyTests(unittest.TestCase):
    def run_main(self, env, responses=(), smtp_refused=None, smtp_error=None, extra_args=()):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.md'
            summary = Path(directory) / 'summary.txt'
            report.write_text('Report body', encoding='utf-8')
            summary.write_text('Daily summary', encoding='utf-8')
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(N.sys, 'argv', ['notify.py', str(report), str(summary), *extra_args]), \
                    patch.object(N.urllib.request, 'urlopen') as urlopen, \
                    patch.object(N.smtplib, 'SMTP_SSL') as smtp, contextlib.redirect_stdout(output):
                replies = []
                for response in responses:
                    if isinstance(response, Exception):
                        replies.append(response)
                        continue
                    status, body = response
                    reply = MagicMock()
                    reply.__enter__.return_value = reply
                    reply.status = status
                    reply.read.side_effect = lambda size=-1, body=body: body.encode()[:size] if size >= 0 else body.encode()
                    replies.append(reply)
                urlopen.side_effect = replies
                client = smtp.return_value.__enter__.return_value
                client.sendmail.return_value = smtp_refused or {}
                if smtp_error:
                    client.sendmail.side_effect = smtp_error
                code = 0
                try:
                    N.main()
                except SystemExit as exc:
                    code = exc.code
                return code, output.getvalue(), urlopen, client

    def test_provider_success_and_failure_contracts(self):
        channels = [
            ({'SERVERCHAN_KEY': 'secret'}, 'code', 0),
            ({'PUSHPLUS_TOKEN': 'secret'}, 'code', 200),
            ({'BARK_URL': 'https://example.invalid/secret'}, 'code', 200),
            ({'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHAT_ID': '123'}, 'ok', True),
        ]
        for env, field, expected in channels:
            for payload, success in [({field: expected}, True), ({field: -1}, False),
                                     ({}, False), ([], False), ({field: 'secret'}, False),
                                     ({field: False}, False)]:
                with self.subTest(env=list(env), payload=payload):
                    code, output, _, _ = self.run_main(env, [(200, json.dumps(payload))])
                    self.assertEqual(code, 0 if success else 1)
                    self.assertNotIn('secret', output)

    def test_telegram_requires_boolean_true(self):
        code, _, _, _ = self.run_main(
            {'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHAT_ID': '123'}, [(200, '{"ok": 1}')])
        self.assertEqual(code, 1)

    def test_transport_and_malformed_responses_fail_without_secrets(self):
        for response in [(500, 'secret'), (200, 'secret invalid json'),
                         urllib.error.HTTPError('https://example.invalid/secret', 403, 'secret', {}, None),
                         TimeoutError('secret timeout')]:
            with self.subTest(response=type(response).__name__):
                code, output, _, _ = self.run_main({'PUSHPLUS_TOKEN': 'secret'}, [response])
                self.assertEqual(code, 1)
                self.assertNotIn('secret', output)

    def test_partial_failure_tries_remaining_channels_and_exits_nonzero(self):
        code, output, urlopen, _ = self.run_main(
            {'SERVERCHAN_KEY': 'secret', 'PUSHPLUS_TOKEN': 'secret', 'BARK_URL': 'https://example.invalid/secret'},
            [(200, '{"code": 0}'), (200, '{"code": 500, "msg": "secret"}'), (200, '{"code": 200}')])
        self.assertEqual(code, 1)
        self.assertEqual(urlopen.call_count, 3)
        self.assertIn("sent: ['serverchan', 'bark'] failed: ['pushplus']", output)
        self.assertNotIn('secret', output)

    def test_long_success_response_is_not_truncated_at_300_bytes(self):
        body = json.dumps({'message': 'x' * 500, 'code': 200})
        code, _, urlopen, _ = self.run_main({'PUSHPLUS_TOKEN': 'secret'}, [(200, body)])
        self.assertEqual(code, 0)

    def test_no_configured_channels_warns_and_fails(self):
        for env in [{}, {'TELEGRAM_BOT_TOKEN': 'secret'}, {'SMTP_HOST': 'example.invalid'}]:
            with self.subTest(env=list(env)):
                code, output, urlopen, client = self.run_main(env)
                self.assertEqual(code, 1)
                self.assertIn('::warning::', output)
                urlopen.assert_not_called()
                client.sendmail.assert_not_called()

    def test_smtp_acceptance_refusals_and_exceptions(self):
        env = {'SMTP_HOST': 'example.invalid', 'SMTP_USER': 'sender@example.invalid',
               'SMTP_PASS': 'secret', 'MAIL_TO': 'a@example.invalid,b@example.invalid'}
        for refused, error, expected in [({}, None, 0),
                                         ({'b@example.invalid': (550, b'secret')}, None, 1),
                                         ({}, N.smtplib.SMTPException('secret'), 1)]:
            with self.subTest(expected=expected, refused=bool(refused)):
                code, output, _, client = self.run_main(env, smtp_refused=refused, smtp_error=error)
                self.assertEqual(code, expected)
                self.assertNotIn('secret', output)
                self.assertNotIn('example.invalid', output)
                self.assertEqual(client.sendmail.call_args.args[1], ['a@example.invalid', 'b@example.invalid'])


if __name__ == '__main__':
    unittest.main()
