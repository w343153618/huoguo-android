"""30 FPS UDP contracts through real registry and host launch code, offline.

All network/process/worker handles below are fake. These checks do not build an
encoder or exercise real Android, VideoToolbox, a phone panel or a media path.
"""
import base64
import io
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from hardware_stream import (FrameRateBudget, HostHardwareSession,
                             raw_submit_budget_configuration)
from udp_lan_sessions import SessionError, UdpLanSessions, parse_udp_settings
from udp_lan_worker import LanMediaWorker
from udp_nps_profile import PUBLIC_HOST, owner_profile


class FakeWorker:
    def __init__(self): self.starts = self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


def registry(scope, node='m1'):
    if scope == 'nps_owner':
        profile = owner_profile(node, allow_m5_owner_trial=node == 'm5')
        return UdpLanSessions(PUBLIC_HOST, profile.public_media.port,
            network_scope=scope, node=node, scope_guard=lambda: True,
            allow_m5_owner_trial=node == 'm5', guest_serial=profile.guest_serial,
            guest_avd=profile.guest_avd)
    if scope == 'tailnet':
        return UdpLanSessions('100.65.0.2', network_scope=scope,
                              scope_guard=lambda: True)
    return UdpLanSessions('192.168.9.128')


def settings(scope, fps, node='m1', **changes):
    result = {'network_scope': scope, 'max_fps': fps, **changes}
    if scope == 'nps_owner':
        result['node'] = node
    return result


class Udp30FpsSettingsChecks(unittest.TestCase):
    def test_exact_three_integer_caps_and_default_across_network_scopes(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            for fps in (30, 60, 120):
                with self.subTest(scope=scope, fps=fps):
                    actual = parse_udp_settings(settings(scope, fps))
                    self.assertEqual((actual['fps'], actual['max_fps']), (fps, fps))
                    self.assertEqual((actual['seconds'], actual['buffer_ms'],
                                      actual['surface_submit_lead_ms']), (120, 80, 0))
            default = {'network_scope': scope}
            if scope == 'nps_owner': default['node'] = 'm1'
            self.assertEqual(parse_udp_settings(default)['fps'], 60)

    def test_non_integer_and_unlisted_caps_are_rejected_before_worker(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            for fps in (None, True, False, 30.0, '30', 0, 15, 24, 29, 31, 90, 121):
                with self.subTest(scope=scope, fps=fps):
                    factory = Mock()
                    candidate = registry(scope)
                    with self.assertRaises(SessionError) as raised:
                        candidate.create('wyw', settings(scope, fps), factory)
                    self.assertEqual((raised.exception.status, raised.exception.code),
                                     (400, 'invalid_udp_settings'))
                    factory.assert_not_called()
                    candidate.close()

    def test_media_30_uses_soft_display_hint_without_changing_other_caps(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            for fps in (30, 60, 120):
                with self.subTest(scope=scope, fps=fps):
                    candidate, worker = registry(scope), FakeWorker()
                    configs = []
                    def factory(config):
                        configs.append(config)
                        return worker
                    descriptor = candidate.create('wyw', settings(scope, fps), factory)
                    self.assertEqual(descriptor['display_hz'], 0 if fps == 30 else 120)
                    self.assertEqual((configs[0]['fps'], configs[0]['max_fps']), (fps, fps))
                    self.assertEqual(configs[0]['display_hz'], descriptor['display_hz'])
                    self.assertEqual(len(base64.b64decode(descriptor['key_b64'])), 32)
                    self.assertEqual(descriptor['bind_port'], 0)
                    self.assertTrue(descriptor['async_video'])
                    self.assertTrue(descriptor['network_feedback'])
                    candidate.close()
                    self.assertEqual(worker.stops, 1)

    def test_display_hint_remains_server_selected_not_a_new_request_option(self):
        for scope in ('lan', 'tailnet'):
            for fps in (30, 60):
                candidate = registry(scope)
                descriptor = candidate.create('wyw', settings(scope, fps, display_hz=30),
                                               lambda config: FakeWorker())
                self.assertEqual(descriptor['display_hz'], 0 if fps == 30 else 120)
                candidate.close()
        factory = Mock()
        with self.assertRaises(SessionError) as raised:
            registry('nps_owner').create('wyw',
                settings('nps_owner', 30, display_hz=30), factory)
        self.assertEqual(raised.exception.code, 'invalid_udp_settings')
        factory.assert_not_called()

    def test_nps_30_preserves_guest_projection_account_ready_and_cancel_boundaries(self):
        for node in ('m1', 'm5'):
            with self.subTest(node=node):
                candidate, worker = registry('nps_owner', node), FakeWorker()
                configs = []
                def factory(config):
                    configs.append(config)
                    return worker
                with self.assertRaises(SessionError) as raised:
                    candidate.create('huoguo', settings('nps_owner', 30, node), factory)
                self.assertEqual(raised.exception.code, 'nps_owner_account_required')
                self.assertEqual(configs, [])
                descriptor = candidate.create('wyw', settings('nps_owner', 30, node), factory)
                sid = descriptor['session']
                profile = owner_profile(node, allow_m5_owner_trial=node == 'm5')
                self.assertEqual((configs[0]['local_bind_host'], configs[0]['local_bind_port']),
                                 ('127.0.0.1', 45965))
                self.assertEqual(configs[0]['guest_serial'], profile.guest_serial)
                self.assertEqual(configs[0]['guest_avd'], profile.guest_avd)
                for private in ('local_bind_host', 'local_bind_port', 'guest_serial', 'guest_avd'):
                    self.assertNotIn(private, descriptor)
                self.assertFalse(candidate.touch_allowed(sid))
                self.assertTrue(candidate.authenticated_ready(sid))
                self.assertTrue(candidate.touch_allowed(sid))
                self.assertFalse(candidate.cancel('huoguo', sid))
                self.assertTrue(candidate.cancel('wyw', sid))
                self.assertFalse(candidate.touch_allowed(sid))
                self.assertFalse(candidate.authenticated_alive(sid))
                self.assertTrue(candidate.cancel('wyw', sid))
                self.assertEqual((worker.starts, worker.stops), (1, 1))
                self.assertTrue(candidate.close_and_wait()['quiescence_confirmed'])


class Udp30FpsHostChecks(unittest.TestCase):
    def test_actual_udp_worker_passes_30_to_encoder_and_raw_budget_without_wire_budget_change(self):
        worker = LanMediaWorker.__new__(LanMediaWorker)
        worker.config = parse_udp_settings({'max_fps': 30, 'max_size': 960,
            'video_bit_rate': 4_000_000, 'bitrate_mode': 'ADAPTIVE_VBR', 'touch': False})
        worker.stop_event, worker.lifecycle_lock = threading.Event(), threading.RLock()
        worker.busy, worker._background = Mock(return_value=False), Mock()
        worker.runtime, worker.native_encoder = Path('/fake/runtime'), Path('/fake/native-encoder')
        worker.packetizer = Path('/fake/packetizer')
        worker.guest_serial, worker.guest_avd = 'emulator-5556', 'RemoteAndroid17Compare'
        worker.closed, worker.sender = False, Mock()
        hardware, native = Mock(), Mock()
        with patch('udp_lan_worker.HostHardwareSession', return_value=hardware) as host, \
                patch('udp_lan_worker.subprocess.Popen', return_value=native) as spawn:
            worker._start_media()
        self.assertEqual(host.call_args.args[3:7], (960, 4_000_000, 30, 'ADAPTIVE_VBR'))
        self.assertEqual(host.call_args.kwargs['raw_submit_fps'], 30)
        self.assertIs(host.call_args.kwargs['matched_experimental_client'], True)
        self.assertEqual(host.call_args.kwargs['raw_queue_policy'], 'fifo')
        self.assertIs(host.call_args.kwargs['sps_low_delay'], True)
        self.assertEqual(spawn.call_args.args[0],
                         ['/fake/packetizer', '32000000', '500000', '0', '2048'])
        hardware.start.assert_called_once_with()
        self.assertIn('formal_session_monitor', [call.args[0] for call in worker._background.call_args_list])

    def test_actual_host_launch_requires_30_encoder_and_30_raw_readback(self):
        for readback_fps in (30, 60):
            with self.subTest(readback_fps=readback_fps):
                pairs = [(Mock(), Mock()) for _ in range(3)]
                for index, (_, remote) in enumerate(pairs, 20):
                    remote.fileno.return_value = index
                readback = raw_submit_budget_configuration(readback_fps, readback_fps)
                process = Mock(stdout=io.BytesIO((json.dumps(
                    {'initialized': True, 'raw_submit_budget': readback}) + '\n').encode()))
                process.poll.return_value = 0
                with patch('hardware_stream.socket.socketpair', side_effect=pairs), \
                        patch('hardware_stream.pathlib.Path.is_file', return_value=True), \
                        patch('hardware_stream.pathlib.Path.open', return_value=io.BytesIO()), \
                        patch('hardware_stream.os.access', return_value=True), \
                        patch('hardware_stream.select.select', return_value=([process.stdout], [], [])), \
                        patch('hardware_stream.subprocess.Popen', return_value=process) as spawn:
                    def start():
                        return HostHardwareSession('/unused', 'emulator-5556', 'compare',
                            960, 4_000_000, 30, 'VBR', native_encoder='/fake/native',
                            raw_submit_fps=30, matched_experimental_client=True)
                    if readback_fps == 30:
                        session = start()
                        self.assertEqual(session.raw_submit_budget_readback, readback)
                        session.close()
                    else:
                        with self.assertRaisesRegex(ValueError, 'readback missing or mismatched'):
                            start()
                    argv = spawn.call_args.args[0]
                    self.assertEqual(argv[argv.index('--fps') + 1], '30')
                    self.assertEqual(argv[argv.index('--raw-submit-fps') + 1], '30')
                    self.assertIn('--matched-experimental-client', argv)
                for local, remote in pairs:
                    local.close.assert_called_once_with()
                    remote.close.assert_called_once_with()

    def test_30_raw_budget_uses_33_ms_period_without_clamping_grpc_capture(self):
        config = raw_submit_budget_configuration(30, 30)
        self.assertIs(config['grpc_fps_limit_applied'], False)
        self.assertEqual(config['native_encoder_fps_arg'], 30)
        now = [0.0]
        budget = FrameRateBudget(config['effective_raw_submit_fps'], lambda: now[0])
        budget.consume(); budget.consume()  # Existing two-frame burst retained.
        self.assertAlmostEqual(budget.delay(), 1 / 30)
        now[0] = 1 / 60
        self.assertAlmostEqual(budget.delay(), 1 / 60)
        now[0] = 1 / 30
        self.assertAlmostEqual(budget.delay(), 0)
        budget.consume()
        self.assertAlmostEqual(budget.delay(), 1 / 30)


if __name__ == '__main__':
    unittest.main()
