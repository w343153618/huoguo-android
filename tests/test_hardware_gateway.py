import io
import json
import unittest
from email.message import Message
from unittest.mock import Mock, patch

import gateway


class HardwareGatewayTest(unittest.TestCase):
    def setUp(self):
        self.worker = Mock()
        self.factory = Mock(return_value=self.worker)
        self.patches = [patch.object(gateway, 'VIDEO_BACKEND', 'videotoolbox'),
                        patch.object(gateway, 'sessions', {}),
                        patch.object(gateway, 'HostHardwareSession', self.factory),
                        patch.object(gateway, 'idle_screen', Mock()),
                        patch.object(gateway, 'ensure_android', Mock()),
                        patch.object(gateway, 'adb', Mock()),
                        patch.object(gateway.threading, 'Timer', Mock())]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def request(self, fps=60):
        request = gateway.Handler.__new__(gateway.Handler)
        request.path, request.headers = '/session', Message()
        body = json.dumps({'max_size': 1200, 'max_fps': fps,
                           'video_bit_rate': 4000000, 'bitrate_mode': 'ADAPTIVE_VBR'}).encode()
        request.headers['Content-Length'] = str(len(body))
        request.rfile, request.connection = io.BytesIO(body), Mock()
        request.auth, request.reply = Mock(return_value=True), Mock()
        request.start_guest_video = Mock()
        request.do_POST()
        return request

    def test_selected_hardware_backend_cannot_silently_fall_back_to_guest_software(self):
        self.factory.side_effect = RuntimeError('hardware unavailable')
        request = self.request()
        self.assertEqual(request.reply.call_args.args[0], 503)
        request.start_guest_video.assert_not_called()
        self.assertEqual(gateway.sessions, {})

    def test_hardware_session_preserves_explicit_client_fps_and_adaptive_mode(self):
        for fps in (30, 60, 120):
            request = self.request(fps)
            status, response = request.reply.call_args.args
            self.assertEqual(status, 200)
            self.assertTrue(response['hardware_required'])
            self.assertTrue(response['adaptive_vbr'])
            self.assertEqual(response['video_backend'], 'videotoolbox')
            self.assertEqual(response['max_fps'], fps)
            self.assertEqual(self.factory.call_args.args[-2:], (fps, 'ADAPTIVE_VBR'))
            gateway.close_session(response['session'])

    def test_120_on_guest_backend_is_rejected_before_closing_current_session(self):
        gateway.sessions['already_connected'] = {'hardware': self.worker}
        with patch.object(gateway, 'VIDEO_BACKEND', 'guest'):
            request = self.request(120)
        self.assertEqual(request.reply.call_args.args[0], 400)
        self.factory.assert_not_called()
        self.worker.close.assert_not_called()
        self.assertIn('already_connected', gateway.sessions)

    def test_incomplete_session_expiry_closes_owned_worker_once(self):
        gateway.sessions['owned'] = {'hardware': self.worker, 'sockets': [], 'roles': ['video']}
        gateway.Handler.expire('owned')
        gateway.Handler.expire('owned')
        self.worker.close.assert_called_once()
        gateway.adb.assert_not_called()
        self.assertEqual(gateway.sessions, {})


if __name__ == '__main__':
    unittest.main()
