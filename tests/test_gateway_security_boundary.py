"""Offline negative checks for account and published-file boundaries.

These fixtures never read credentials, contact a listener, or launch Android.
They verify source behavior, not host sandbox or live network isolation.
"""
from email.message import Message
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import gateway


class GatewayBoundaryTest(unittest.TestCase):
    SID = 'a' * 32

    def setUp(self):
        self.sessions = {}
        self.hardware = Mock()
        self.hardware.proc = Mock()
        self.patches = [patch.object(gateway, 'sessions', self.sessions),
                        patch.object(gateway, 'idle_screen', Mock()),
                        patch.object(gateway, 'ensure_android', Mock()),
                        patch.object(gateway, 'adb', Mock()),
                        patch.object(gateway, 'VIDEO_BACKEND', 'videotoolbox'),
                        patch.object(gateway, 'HostHardwareSession', Mock(return_value=self.hardware)),
                        patch.object(gateway.threading, 'Timer', Mock())]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def request(self, path, account='owner', accepted=True, settings=None):
        handler = gateway.Handler.__new__(gateway.Handler)
        handler.path, handler.headers = path, Message()
        body = json.dumps(settings or {}).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.connection = Mock()
        handler.responses, handler.response_headers = [], []
        handler.reply = lambda status, data: handler.responses.append((status, data))
        handler.send_response = lambda status, *rest: handler.responses.append((status, None))
        handler.send_header = lambda key, value: handler.response_headers.append((key, value))
        handler.end_headers = Mock()
        def auth():
            if not accepted:
                handler.reply(401, {'error': 'fake_auth_rejected'})
                return False
            handler.account = account
            return True
        handler.auth = auth
        return handler

    def owned_session(self):
        record = {'account': 'owner', 'hardware': self.hardware,
                  'roles': [], 'sockets': [], 'created': 0}
        self.sessions[self.SID] = record
        return record

    def test_new_session_records_authenticated_owner(self):
        request = self.request('/session')
        request.do_POST()
        self.assertEqual(request.responses[0][0], 200)
        self.assertEqual(self.sessions[request.responses[0][1]['session']]['account'], 'owner')

    def test_other_account_cannot_displace_an_existing_session(self):
        original = self.owned_session()
        request = self.request('/session', account='other')
        request.do_POST()
        self.assertEqual(request.responses[0][0], 409)
        self.assertIs(self.sessions[self.SID], original)
        self.hardware.close.assert_not_called()
        gateway.ensure_android.assert_not_called()
        gateway.HostHardwareSession.assert_not_called()

    def test_other_account_with_known_sid_cannot_open_any_channel(self):
        original = self.owned_session()
        for role in ('video', 'audio', 'control'):
            request = self.request('/stream/' + self.SID + '/' + role, account='other')
            request.do_CONNECT()
            self.assertEqual(request.responses[0][0], 404)
            self.assertEqual(original['roles'], [])
            self.assertEqual(original['sockets'], [])
        self.hardware.channel.assert_not_called()
        self.hardware.close.assert_not_called()

    def test_owner_can_open_the_existing_channel_order(self):
        self.owned_session()
        upstream = self.hardware.channel.return_value
        upstream.recv.return_value = b''
        request = self.request('/stream/' + self.SID + '/video')
        with patch.object(gateway.threading, 'Thread', Mock()):
            request.do_CONNECT()
        self.assertEqual(request.responses[0][0], 200)
        self.hardware.channel.assert_called_once_with('video')
        self.hardware.close.assert_called_once()

    def test_delete_requires_the_owner_and_closes_only_once(self):
        original = self.owned_session()
        request = self.request('/session/' + self.SID, account='other')
        request.do_DELETE()
        self.assertEqual(request.responses[0][0], 404)
        self.assertIs(self.sessions[self.SID], original)
        self.hardware.close.assert_not_called()
        request = self.request('/session/' + self.SID)
        request.do_DELETE()
        self.assertEqual(request.responses, [(200, {'closed': True})])
        self.hardware.close.assert_called_once()
        request = self.request('/session/' + self.SID)
        request.do_DELETE()
        self.assertEqual(request.responses[0][0], 404)
        self.hardware.close.assert_called_once()

    def test_rejected_auth_and_malformed_delete_do_not_touch_sessions(self):
        original = self.owned_session()
        request = self.request('/session/' + self.SID, accepted=False)
        request.do_DELETE()
        self.assertEqual(request.responses[0][0], 401)
        for path in ('/session/../' + self.SID, '/session/' + self.SID + '/control',
                     '/session/' + self.SID + '?other=1', '/files/' + self.SID):
            request = self.request(path)
            request.do_DELETE()
            self.assertEqual(request.responses[0][0], 404)
        self.assertIs(self.sessions[self.SID], original)
        self.hardware.close.assert_not_called()

    def read_update(self, directory, name='update.json'):
        request = self.request('/updates/' + name)
        with patch.dict(os.environ, {'DIRECT_UPDATE_DIR': str(directory)}):
            request.do_GET()
        return request

    def test_update_symlink_cannot_serve_an_unrelated_host_file(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'updates'
            directory.mkdir()
            unrelated = Path(root) / 'unrelated-fixture.txt'
            unrelated.write_bytes(b'not a published update')
            (directory / 'update.json').symlink_to(unrelated)
            request = self.read_update(directory)
            self.assertEqual(request.responses[0][0], 503)
            self.assertEqual(request.wfile.getvalue(), b'')

    def test_fifo_directory_and_empty_update_are_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            target = directory / 'update.json'
            os.mkfifo(target)
            self.assertEqual(self.read_update(directory).responses[0][0], 503)
            target.unlink()
            target.mkdir()
            self.assertEqual(self.read_update(directory).responses[0][0], 503)
            target.rmdir()
            target.touch()
            self.assertEqual(self.read_update(directory).responses[0][0], 503)

    def test_update_sizes_are_bounded_before_headers_or_payload(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for name, limit in (('update.json', 65536),
                                ('HuoguoAndroid-v1.30.apk', 64 * 1024 * 1024)):
                with (directory / name).open('wb') as target:
                    target.truncate(limit + 1)
                request = self.read_update(directory, name)
                self.assertEqual(request.responses[0][0], 503)
                self.assertEqual(request.wfile.getvalue(), b'')

    def test_regular_update_is_served_and_path_traversal_is_not(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            payload = b'{"version":"fixture"}'
            (directory / 'update.json').write_bytes(payload)
            request = self.read_update(directory)
            self.assertEqual(request.responses[0][0], 200)
            self.assertEqual(request.wfile.getvalue(), payload)
            self.assertIn(('Content-Length', str(len(payload))), request.response_headers)
            for name in ('../update.json', '%2e%2e/update.json', 'sub/update.json',
                         'update.json?anything=1', 'auth.json'):
                request = self.read_update(directory, name)
                self.assertEqual(request.responses[0][0], 404)
                self.assertEqual(request.wfile.getvalue(), b'')


if __name__ == '__main__':
    unittest.main()
