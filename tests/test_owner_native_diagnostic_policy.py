"""Explicit server selection and ownership lifetimes, no real media/device."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from scripts.probes.owner_native_diagnostic_preflight import parse_plan
from tests import test_owner_native_diagnostic_preflight as pins
from tests import test_udp_lan_sessions as sessions
from tests import test_udp_lan_worker as workers
from owner_native_diagnostic_policy import FIXED_MEDIA, validate_selection, select_options
from udp_lan_sessions import UdpLanSessions, SessionError
from udp_lan_worker import LanMediaWorker


def plan(**changes):
    return parse_plan(dict(pins.plan_input(), **changes))


def request(**changes):
    return dict(max_size=960, max_fps=30, video_bit_rate=4_000_000, bitrate_mode='VBR',
                buffer_ms=80, seconds=120, audio_enabled=True, touch_enabled=True, **changes)


class NativePolicyChecks(unittest.TestCase):
    def test_default_OFF_is_independent_of_plan_HTTP_account_and_environment(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            self.assertIsNone(validate_selection(None, scope, 15556))
        registry = UdpLanSessions('192.168.9.128')
        with patch.dict('os.environ', {'DIRECT_DIAGNOSTIC_EVENTS': 'true'}):
            descriptor = registry.create('wyw', dict(request(), diagnostic_events=True), lambda c: Mock())
        self.assertFalse(descriptor['diagnostic_events'])
        self.assertEqual(descriptor['seconds'], 120)
        registry.cancel('wyw', descriptor['session'])

    def test_constructor_refuses_other_scopes_ports_and_unparsed_values(self):
        for value, scope, port in (({}, 'lan', 45963), (True, 'lan', 45963),
                                  (plan(), 'tailnet', 45963), (plan(), 'nps_owner', 15556),
                                  (plan(), 'lan', 45965), (plan(), 'lan', 45963.0)):
            with self.subTest(scope=scope, port=port), self.assertRaises(ValueError):
                validate_selection(value, scope, port)
        with self.assertRaisesRegex(ValueError, 'default_Surface'):
            UdpLanSessions('192.168.9.128', owner_native_diagnostic_plan=plan(),
                           allow_owner_surface_submit_lead=True)

    def test_explicit_plan_selects_only_descriptor_and_exact_worker_guest(self):
        registry = UdpLanSessions('192.168.9.128', owner_native_diagnostic_plan=plan())
        configs = []
        def factory(config): configs.append(config); return Mock()
        descriptor = registry.create('huoguo', dict(request(), diagnostic_events=False), factory)
        self.assertTrue(descriptor['diagnostic_events'])
        self.assertEqual(descriptor['seconds'], 30)
        self.assertEqual((configs[0]['guest_serial'], configs[0]['guest_avd']),
                         ('emulator-5556', 'RemoteAndroid17Compare'))
        self.assertNotIn('guest_serial', descriptor)
        self.assertNotIn('guest_avd', descriptor)
        self.assertNotIn('app_sha256', descriptor)
        for name, value in FIXED_MEDIA.items(): self.assertEqual(descriptor[name], value)
        registry.cancel('huoguo', descriptor['session'])

    def test_native_trial_refuses_media_changes_before_any_factory(self):
        for change in ({'max_fps': 60}, {'buffer_ms': 100}, {'max_size': 1280},
                       {'video_bit_rate': 8_000_000}, {'bitrate_mode': 'CBR'},
                       {'surface_submit_lead_ms': 16}, {'audio_enabled': False},
                       {'touch_enabled': False}):
            registry = UdpLanSessions('192.168.9.128', owner_native_diagnostic_plan=plan())
            factory = Mock()
            settings = request(); settings.update(change)
            with self.subTest(change=change), self.assertRaises(SessionError) as caught:
                registry.create('huoguo', settings, factory)
            self.assertEqual(caught.exception.status, 400)
            factory.assert_not_called()
            self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def test_trial_credentials_are_closed_without_claiming_operator_identity(self):
        registry = UdpLanSessions('192.168.9.128', owner_native_diagnostic_plan=plan())
        self.assertEqual(registry.owner_accounts, ('huoguo', 'wyw'))
        factory = Mock()
        with self.assertRaises(SessionError) as caught:
            registry.create('fixture-foreign-account', request(), factory)
        self.assertEqual(caught.exception.code, 'owner_native_existing_account_required')
        self.assertEqual(caught.exception.status, 403)
        factory.assert_not_called()

    def test_requested_shorter_duration_is_preserved_and_input_not_mutated(self):
        registry = UdpLanSessions('192.168.9.128', owner_native_diagnostic_plan=plan(sample_seconds=20))
        settings = request(); settings['seconds'] = 10
        descriptor = registry.create('huoguo', settings, lambda c: Mock())
        self.assertEqual(settings['seconds'], 10)
        self.assertEqual(descriptor['seconds'], 10)
        registry.cancel('huoguo', descriptor['session'])

    def test_process_deadline_caps_ready_and_active_lease_using_existing_reap(self):
        clock, worker = sessions.Clock(), sessions.Worker()
        registry = UdpLanSessions('192.168.9.128', clock=clock,
                                  owner_native_diagnostic_plan=plan(process_max_seconds=30))
        clock.advance(25)
        descriptor = registry.create('huoguo', request(), lambda c: worker)
        self.assertEqual(registry._active.ready_deadline, 130)
        self.assertTrue(registry.authenticated_ready(descriptor['session']))
        self.assertEqual(registry._active.hard_deadline, 130)
        clock.advance(2); self.assertTrue(registry.authenticated_alive(descriptor['session']))
        clock.advance(3)
        self.assertEqual(registry.reap(), 1)
        self.assertEqual((worker.starts, worker.stops), (1, 1))
        self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def test_expired_trial_has_no_new_admission_or_factory_side_effect(self):
        clock = sessions.Clock()
        registry = UdpLanSessions('192.168.9.128', clock=clock,
                                  owner_native_diagnostic_plan=plan(process_max_seconds=30))
        clock.advance(30); factory = Mock()
        with self.assertRaises(SessionError) as caught:
            registry.create('huoguo', request(), factory)
        self.assertEqual(caught.exception.code, 'udp_network_scope_unavailable')
        factory.assert_not_called()

    def test_cleanup_failure_on_trial_expiry_is_not_quiescent_success(self):
        clock = sessions.Clock()
        worker = sessions.Worker(); worker.stop = Mock(side_effect=RuntimeError('fixture'))
        registry = UdpLanSessions('192.168.9.128', clock=clock,
                                  owner_native_diagnostic_plan=plan(process_max_seconds=30))
        descriptor = registry.create('huoguo', request(), lambda c: worker)
        self.assertTrue(registry.authenticated_ready(descriptor['session']))
        clock.advance(30); registry.reap()
        with self.assertRaises(SessionError) as caught: registry.close_and_wait(.01)
        self.assertEqual(caught.exception.code, 'udp_cleanup_failed')

    def test_process_expiry_does_not_release_pending_owned_factory(self):
        clock, worker = sessions.Clock(), sessions.Worker()
        registry = UdpLanSessions('192.168.9.128', clock=clock,
                                  owner_native_diagnostic_plan=plan(process_max_seconds=30))
        entered, release, failures = threading.Event(), threading.Event(), []
        def factory(config):
            entered.set()
            if not release.wait(1): raise AssertionError('fixture release missing')
            return worker
        def create():
            try: registry.create('huoguo', request(), factory)
            except SessionError as error: failures.append(error.code)
        thread = threading.Thread(target=create)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            clock.advance(30); self.assertEqual(registry.reap(), 1)
            self.assertEqual(worker.stops, 0)
            with self.assertRaises(SessionError) as caught: registry.close_and_wait(.01)
            self.assertEqual(caught.exception.code, 'udp_shutdown_quiescence_timeout')
        finally:
            release.set(); thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual((worker.starts, worker.stops), (0, 1))
        self.assertEqual(failures, ['udp_network_scope_unavailable'])
        self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def worker_config(self, **changes):
        return workers.config(network_scope='lan', peer_port=45963, **FIXED_MEDIA,
            diagnostic_events=True, guest_serial='emulator-5556', guest_avd='RemoteAndroid17Compare', **changes)

    def test_descriptor_cannot_enable_worker_without_matching_trusted_selection(self):
        with patch('udp_lan_worker.socket.socket') as sock, \
                patch.object(Path, 'read_text') as files, \
                patch.object(LanMediaWorker, '_background') as background:
            with self.assertRaisesRegex(ValueError, 'trusted_worker_selection'):
                LanMediaWorker(self.worker_config(), '192.168.9.1', '192.168.9.2', 'en7',
                    '/fake', '/fake/p', '/fake/e', Mock(), '/fake', Mock())
        sock.assert_not_called(); files.assert_not_called(); background.assert_not_called()

    def test_wrong_guest_or_combined_host_experiments_fail_before_resources(self):
        for kwargs in ({'guest_serial':'emulator-5558'}, {'guest_avd':'RemoteAndroid17M5'},
                       {'capture_trace_dir':Path('/fake')}, {'raw_queue_policy':'latest'},
                       {'raw_submit_fps':60}, {'enobufs_retry_enabled':True}):
            with self.subTest(kwargs=kwargs), patch('udp_lan_worker.socket.socket') as sock, \
                    patch.object(Path, 'read_text') as files:
                with self.assertRaises(ValueError):
                    LanMediaWorker(self.worker_config(), '192.168.9.1', '192.168.9.2', 'en7',
                        '/fake', '/fake/p', '/fake/e', Mock(), '/fake', Mock(),
                        owner_native_diagnostic_plan=plan(), **kwargs)
                sock.assert_not_called(); files.assert_not_called()

    def test_worker_descriptor_duration_and_enable_must_match_selection(self):
        for change in ({'seconds':31}, {'diagnostic_events':False}, {'diagnostic_events':1},
                       {'network_scope':'tailnet'}, {'peer_port':15556}, {'fps':60}):
            config = self.worker_config(); config.update(change)
            with self.subTest(change=change), patch('udp_lan_worker.socket.socket') as sock, \
                    patch.object(Path, 'read_text') as files:
                with self.assertRaises(ValueError):
                    LanMediaWorker(config, '192.168.9.1', '192.168.9.2', 'en7',
                        '/fake', '/fake/p', '/fake/e', Mock(), '/fake', Mock(),
                        owner_native_diagnostic_plan=plan())
                sock.assert_not_called(); files.assert_not_called()

    def test_valid_worker_uses_only_fake_owned_resources_and_no_host_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hardware = root/'hardware'; hardware.mkdir()
            jar = hardware/'scrcpy-audio-control'; jar.write_bytes(b'fixture')
            import hashlib, json
            (hardware/'capabilities.json').write_text(json.dumps(dict(
                touch_cancel_clears_pointer_state=True,
                guest_jar_sha256=hashlib.sha256(jar.read_bytes()).hexdigest())))
            fake = workers.FakeSocket()
            with patch('udp_lan_worker.socket.socket', return_value=fake), \
                    patch('udp_lan_worker.socket.if_nametoindex', return_value=6), \
                    patch.object(LanMediaWorker, '_background'), \
                    patch('udp_lan_worker.private_trace_attempt') as tracing:
                worker = LanMediaWorker(self.worker_config(), '192.168.9.1', '192.168.9.2',
                    'en7', root, '/fake/p', '/fake/e', Mock(), root, Mock(),
                    owner_native_diagnostic_plan=plan())
            self.assertIsNone(worker.capture_trace_path)
            self.assertIsNone(worker.feed_trace_sink)
            self.assertEqual(worker.raw_queue_policy, 'fifo')
            self.assertIsNone(worker.owner_raw_submit_fps_requested)
            self.assertEqual(fake.binds, [('192.168.9.2',45963)])
            tracing.assert_not_called(); worker.udp.close()


if __name__ == '__main__':
    unittest.main()
