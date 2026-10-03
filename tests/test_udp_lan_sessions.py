"""Pure candidate UDP lifecycle checks, with fake owned workers and clocks."""
import base64
import threading
import unittest
from unittest.mock import patch

from udp_lan_sessions import SessionError, UdpLanSessions, parse_udp_settings


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class Worker:
    def __init__(self): self.starts = 0; self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


class SettingsChecks(unittest.TestCase):
    def test_balanced_defaults_and_frozen_receiver_contract(self):
        defaults = parse_udp_settings({})
        self.assertEqual((defaults['max_size'], defaults['fps'], defaults['buffer_ms']),
                         (1280, 60, 80))
        self.assertEqual(defaults['seconds'], 120)
        self.assertEqual(defaults['video_bit_rate'], 8_000_000)
        self.assertEqual(defaults['bitrate_mode'], 'VBR')
        self.assertTrue(defaults['audio_enabled'])
        self.assertTrue(defaults['touch_enabled'])
        self.assertEqual(defaults['surface_submit_lead_ms'], 0)

    def test_owner_surface_lead_is_narrow_and_not_a_playback_buffer(self):
        for lead in (0, 16):
            settings = parse_udp_settings({'surface_submit_lead_ms': lead})
            self.assertEqual(settings['surface_submit_lead_ms'], lead)
            self.assertEqual(settings['buffer_ms'], 80)
            self.assertEqual(settings['seconds'], 120)
        for lead in (None, True, False, 0.0, 16.0, '16', -1, 8, 17, 80):
            with self.subTest(lead=lead), self.assertRaises(ValueError):
                parse_udp_settings({'surface_submit_lead_ms': lead})

    def test_owner_surface_opt_in_requires_explicit_boolean(self):
        for enabled in (None, 0, 1, 'true'):
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                UdpLanSessions('192.168.9.128', allow_owner_surface_submit_lead=enabled)

    def test_existing_parsers_apply_to_resolution_bitrate_and_mode(self):
        for size in (960, 1280, 1920):
            for mode in ('CBR', 'VBR', 'ADAPTIVE_VBR'):
                with self.subTest(size=size, mode=mode):
                    got = parse_udp_settings({'max_size': size, 'max_fps': 120,
                        'video_bit_rate': 12_000_000, 'bitrate_mode': mode,
                        'buffer_ms': 100, 'seconds': 1, 'audio': False, 'touch': False})
                    self.assertEqual(got['max_size'], size)
                    self.assertEqual(got['video_bit_rate'], 12_000_000)
                    self.assertEqual(got['bitrate_mode'], mode)
                    self.assertFalse(got['audio_enabled'])

    def test_reject_wrong_types_ranges_and_legacy_presets(self):
        invalid = [None, [], {'max_size': 1080}, {'max_size': 1600},
                   {'max_size': True}, {'max_fps': 30}, {'max_fps': 90},
                   {'max_fps': 60.0}, {'video_bit_rate': 499_999},
                   {'video_bit_rate': 40_000_001}, {'bitrate_mode': 'AVBR'},
                   {'buffer_ms': 29}, {'buffer_ms': 101}, {'buffer_ms': True},
                   {'buffer_ms': '80'}, {'seconds': 0}, {'seconds': 121},
                   {'seconds': False}, {'seconds': 2.0}, {'audio': 1},
                   {'touch': 'true'}, {'audio_enabled': None}]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                parse_udp_settings(settings)

    def test_receiver_toggle_aliases_cannot_override_conflicting_request(self):
        self.assertFalse(parse_udp_settings({'audio_enabled': False})['audio_enabled'])
        self.assertTrue(parse_udp_settings({'touch': True, 'touch_enabled': True})['touch_enabled'])
        for name in ('touch', 'audio'):
            with self.assertRaises(ValueError):
                parse_udp_settings({name: False, name + '_enabled': True})
            with self.assertRaises(ValueError):
                parse_udp_settings({name: 1, name + '_enabled': True})

    def test_lan_ipv4_and_port_are_literals_and_valid(self):
        for host in ('localhost', '127.0.0.1', '0.0.0.0', '169.254.1.1',
                     '224.0.0.1', '146.56.249.175', '100.65.0.2', '192.0.2.1', '::1', None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                UdpLanSessions(host)
        for port in (0, 65536, True, '15963'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                UdpLanSessions('192.168.9.128', port)

    def test_scope_is_explicit_and_tailnet_registry_is_exact_guarded_host(self):
        for scope in (None, True, 'public', 'TAILNET'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                parse_udp_settings({'network_scope': scope})
        with self.assertRaises(ValueError):
            UdpLanSessions('100.65.0.2', network_scope='tailnet')
        for host in ('100.65.0.3', '100.65.0.11', '192.168.9.128'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                UdpLanSessions(host, network_scope='tailnet', scope_guard=lambda: True)


class TailnetSessionChecks(unittest.TestCase):
    def setUp(self):
        self.healthy = True
        self.worker = Worker()
        self.factory_calls = 0
        self.registry = UdpLanSessions('100.65.0.2', network_scope='tailnet',
                                       scope_guard=lambda: self.healthy)

    def factory(self, config):
        self.factory_calls += 1
        return self.worker

    def create(self):
        return self.registry.create('test-owner', {'network_scope': 'tailnet'}, self.factory)

    def test_only_matching_requested_scope_creates_tailnet_descriptor(self):
        for settings in ({}, {'network_scope': 'lan'}):
            with self.assertRaises(SessionError) as error:
                self.registry.create('test-owner', settings, self.factory)
            self.assertEqual((error.exception.status, error.exception.code),
                             (400, 'udp_network_scope_mismatch'))
        self.assertEqual(self.factory_calls, 0)
        descriptor = self.create()
        self.assertEqual((descriptor['network_scope'], descriptor['peer_host']),
                         ('tailnet', '100.65.0.2'))
        lan = UdpLanSessions('192.168.9.128')
        with self.assertRaises(SessionError):
            lan.create('test-owner', {'network_scope': 'tailnet'}, self.factory)

    def test_cached_scope_failure_rejects_ready_alive_and_touch_without_releasing_guard(self):
        descriptor = self.create()
        sid = descriptor['session']
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.healthy = False
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertFalse(self.registry.authenticated_alive(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: self.fail('revoked touch write')))
        self.assertIsNone(self.registry.status('test-owner', sid))
        self.registry.close()
        self.registry.close()
        self.assertEqual(self.worker.stops, 1)
        with self.assertRaises(SessionError) as error:
            self.create()
        self.assertEqual(error.exception.status, 503)

    def test_scope_failure_before_or_during_factory_cannot_return_a_live_descriptor(self):
        self.healthy = False
        with self.assertRaises(SessionError):
            self.create()
        self.assertEqual(self.factory_calls, 0)
        self.healthy = True
        def factory(config):
            self.healthy = False
            return self.worker
        with self.assertRaises(SessionError) as error:
            self.registry.create('test-owner', {'network_scope': 'tailnet'}, factory)
        self.assertEqual((error.exception.status, error.exception.code),
                         (503, 'udp_network_scope_unavailable'))
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(self.worker.starts, 0)

    def test_scope_failure_during_start_revokes_and_stops_owned_worker(self):
        sid = self.create()['session']
        def start():
            self.worker.starts += 1
            self.healthy = False
        self.worker.start = start
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))


class SessionChecks(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = UdpLanSessions('192.168.9.128', clock=self.clock)
        self.worker = Worker()
        self.configs = []

    def factory(self, config):
        self.configs.append(config)
        return self.worker

    def create(self, **settings):
        return self.registry.create('huoguo', settings, self.factory)

    def test_descriptor_contains_unique_ephemeral_secret_and_no_account_password(self):
        first = self.create()
        self.assertEqual(len(first['session']), 32)
        int(first['session'], 16)
        self.assertEqual(len(base64.b64decode(first['key_b64'], validate=True)), 32)
        self.assertEqual(len(first['session_tag_hex']), 16)
        int(first['session_tag_hex'], 16)
        self.assertEqual(first['protocol'], 'HGUE_UDP_V1')
        self.assertEqual(first['network_scope'], 'lan')
        self.assertEqual((first['peer_host'], first['peer_port'], first['bind_port']),
                         ('192.168.9.128', 45963, 0))
        self.assertEqual(first['video_release'], 'scheduled')
        for field in ('async_video', 'decoder_reanchor_enabled', 'network_feedback'):
            self.assertTrue(first[field])
        self.assertFalse(first['diagnostic_events'])
        self.assertEqual(first['display_hz'], 120)
        self.assertEqual(first['surface_submit_lead_ms'], 0)
        self.assertNotIn('account', first)
        self.assertNotIn('password', first)
        self.registry.cancel('huoguo', first['session'])
        second = self.create()
        for field in ('session', 'key_b64', 'session_tag_hex'):
            self.assertNotEqual(first[field], second[field])

    def test_account_and_settings_validated_before_factory(self):
        for account in (None, '', b'huoguo', 'x' * 129):
            with self.subTest(account_type=type(account)), self.assertRaises(SessionError) as error:
                self.registry.create(account, {}, self.factory)
            self.assertEqual(error.exception.status, 401)
        with self.assertRaises(SessionError) as error:
            self.create(buffer_ms=120)
        self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.configs, [])

    def test_surface_lead16_default_rejects_without_reserving_or_starting_worker(self):
        with self.assertRaises(SessionError) as error:
            self.create(surface_submit_lead_ms=16)
        self.assertEqual((error.exception.status, error.exception.code),
                         (400, 'owner_surface_submit_experiment_not_enabled'))
        self.assertEqual(self.configs, [])
        self.assertEqual(self.worker.starts, 0)
        self.assertEqual(self.create()['surface_submit_lead_ms'], 0)

    def test_explicit_owner_surface_opt_in_echoes_selected_lead_to_descriptor_and_worker(self):
        self.registry = UdpLanSessions('192.168.9.128', clock=self.clock,
                                      allow_owner_surface_submit_lead=True)
        for lead in (0, 16, 0):
            descriptor = self.create(surface_submit_lead_ms=lead)
            self.assertEqual(descriptor['surface_submit_lead_ms'], lead)
            self.assertEqual(self.configs[-1]['surface_submit_lead_ms'], lead)
            self.assertEqual(descriptor['buffer_ms'], 80)
            self.assertEqual(descriptor['video_release'], 'scheduled')
            self.assertEqual(self.worker.starts, 0)
            self.registry.cancel('huoguo', descriptor['session'])

    def test_explicit_owner_surface_opt_in_keeps_scope_auth_busy_and_invalid_lead_guards(self):
        self.registry = UdpLanSessions('100.65.0.2', clock=self.clock,
            network_scope='tailnet', scope_guard=lambda: True,
            allow_owner_surface_submit_lead=True)
        for account, settings, code in ((None, {'surface_submit_lead_ms': 16}, 'account_required'),
                ('huoguo', {'surface_submit_lead_ms': 16}, 'udp_network_scope_mismatch'),
                ('huoguo', {'network_scope': 'tailnet', 'surface_submit_lead_ms': 8}, 'invalid_udp_settings')):
            with self.subTest(code=code), self.assertRaises(SessionError) as error:
                self.registry.create(account, settings, self.factory)
            self.assertEqual(error.exception.code, code)
        settings = {'network_scope': 'tailnet', 'surface_submit_lead_ms': 16}
        descriptor = self.registry.create('huoguo', settings, self.factory)
        with self.assertRaises(SessionError) as error:
            self.registry.create('different-owner', settings, self.factory)
        self.assertEqual(error.exception.code, 'udp_session_busy')
        self.assertEqual(len(self.configs), 1)
        self.assertFalse(self.registry.touch_allowed(descriptor['session']))

    def test_factory_config_and_returned_descriptor_do_not_mutate_registry_settings(self):
        def factory(config):
            config['touch_enabled'] = False
            return self.worker
        descriptor = self.registry.create('huoguo', {}, factory)
        sid = descriptor['session']
        descriptor['touch_enabled'] = False
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.assertTrue(self.registry.touch_allowed(sid))

    def test_factory_runs_once_and_busy_does_not_displace_existing_account(self):
        descriptor = self.create()
        with self.assertRaises(SessionError) as error:
            self.registry.create('wyw', {}, self.factory)
        self.assertEqual((error.exception.status, error.exception.code),
                         (409, 'udp_session_busy'))
        self.assertEqual(len(self.configs), 1)
        self.assertEqual(self.worker.stops, 0)
        self.assertIsNotNone(self.registry.status('huoguo', descriptor['session']))

    def test_media_waits_for_authenticated_ready_and_duplicate_ready_does_not_start_again(self):
        sid = self.create()['session']
        self.assertEqual(self.worker.starts, 0)
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertFalse(self.registry.authenticated_alive(sid))
        self.assertFalse(self.registry.authenticated_ready('f' * 32))
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.assertEqual(self.worker.starts, 1)
        self.assertTrue(self.registry.touch_allowed(sid))

    def test_exact_ready_deadline_reaps_and_delayed_ready_never_starts_media(self):
        sid = self.create()['session']
        self.clock.advance(10)
        self.assertEqual(self.registry.reap(), 1)
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 1))
        self.registry.reap()
        self.assertEqual(self.worker.stops, 1)

    def test_alive_renews_only_active_silence_and_expires_at_three_seconds(self):
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        self.clock.advance(2.9)
        self.assertTrue(self.registry.authenticated_alive(sid))
        self.clock.advance(2.9)
        self.assertTrue(self.registry.touch_allowed(sid))
        self.clock.advance(.1)
        self.assertFalse(self.registry.authenticated_alive(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual(self.worker.stops, 1)

    def test_duplicate_ready_never_extends_heartbeat(self):
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        self.clock.advance(2)
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.clock.advance(1)
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertEqual(self.worker.starts, 1)

    def test_hard_deadline_measured_from_ready_and_cannot_be_renewed(self):
        sid = self.create(seconds=4)['session']
        self.clock.advance(9)
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.clock.advance(2)
        self.assertTrue(self.registry.authenticated_alive(sid))
        self.clock.advance(1.99)
        self.assertTrue(self.registry.touch_allowed(sid))
        self.clock.advance(.01)
        self.assertFalse(self.registry.authenticated_alive(sid))
        self.assertEqual(self.worker.stops, 1)

    def test_only_owner_can_cancel_status_and_recent_tombstone(self):
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        self.assertIsNone(self.registry.status('wyw', sid))
        self.assertFalse(self.registry.cancel('wyw', sid))
        self.assertTrue(self.registry.touch_allowed(sid))
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertFalse(self.registry.cancel('wyw', sid))
        self.assertFalse(self.registry.cancel('huoguo', '0' * 32))
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual(self.worker.stops, 1)

    def test_touch_disabled_and_safe_status_does_not_expose_keys(self):
        descriptor = self.create(touch=False)
        sid = descriptor['session']
        status = self.registry.status('huoguo', sid)
        self.assertEqual(status, {'session': sid, 'phase': 'waiting', 'expires_in_ms': 10000})
        self.registry.authenticated_ready(sid)
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual(self.registry.status('huoguo', sid)['phase'], 'active')
        self.assertNotIn(descriptor['key_b64'], str(status))

    def test_dispatch_touch_requires_live_enabled_session_and_propagates_bounded_write_failure(self):
        writes = []
        sid = self.create()['session']
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: writes.append('before')))
        self.registry.authenticated_ready(sid)
        self.assertTrue(self.registry.dispatch_touch(sid, lambda: writes.append('active')))
        with self.assertRaises(OSError):
            self.registry.dispatch_touch(sid, lambda: (_ for _ in ()).throw(OSError('fake')))
        self.clock.advance(3)
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: writes.append('expired')))
        self.assertEqual(writes, ['active'])
        self.assertEqual(self.worker.stops, 0)  # Avoid joining the calling input writer.
        self.registry.reap()
        self.assertEqual(self.worker.stops, 1)
        new_sid = self.create(touch=False)['session']
        self.registry.authenticated_ready(new_sid)
        self.assertFalse(self.registry.dispatch_touch(new_sid, lambda: writes.append('disabled')))

    def test_cancel_and_close_drain_deferred_touch_expiry_cleanup_exactly_once(self):
        for finish in ('cancel', 'close'):
            with self.subTest(finish=finish):
                registry = UdpLanSessions('192.168.9.128', clock=self.clock)
                worker = Worker()
                sid = registry.create('huoguo', {}, lambda config: worker)['session']
                registry.authenticated_ready(sid)
                self.clock.advance(3)
                self.assertFalse(registry.dispatch_touch(sid, lambda: self.fail('expired write')))
                self.assertEqual(worker.stops, 0)
                if finish == 'cancel':
                    self.assertTrue(registry.cancel('huoguo', sid))
                else:
                    registry.close()
                registry.reap()
                registry.close()
                self.assertEqual(worker.stops, 1)

    def test_factory_error_is_safe_and_releases_reservation(self):
        def fail(config): raise RuntimeError('secret ' + config['key_b64'])
        with self.assertRaises(SessionError) as error:
            self.registry.create('huoguo', {}, fail)
        self.assertEqual(str(error.exception), 'udp_worker_unavailable')
        self.assertEqual(error.exception.status, 503)
        self.assertIsNotNone(self.create())

    def test_invalid_worker_with_stop_still_receives_owned_cleanup(self):
        class Invalid:
            def __init__(self): self.stops = 0
            def stop(self): self.stops += 1
        invalid = Invalid()
        with self.assertRaises(SessionError):
            self.registry.create('huoguo', {}, lambda config: invalid)
        self.assertEqual(invalid.stops, 1)
        self.assertIsNotNone(self.create())

    def test_factory_exceeding_ready_lease_stops_without_start(self):
        def slow(config):
            self.clock.advance(10)
            return self.worker
        with self.assertRaises(SessionError) as error:
            self.registry.create('huoguo', {}, slow)
        self.assertEqual(error.exception.code, 'udp_session_revoked')
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 1))

    def test_failed_start_stops_once_and_revokes_touch(self):
        def fail():
            self.worker.starts += 1
            raise RuntimeError('untrusted worker detail')
        self.worker.start = fail
        sid = self.create()['session']
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))

    def test_stop_failure_does_not_retry_or_allow_overlapping_unclosed_resource(self):
        def fail():
            self.worker.stops += 1
            raise RuntimeError('untrusted stop detail')
        self.worker.stop = fail
        sid = self.create()['session']
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.registry.reap()
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(self.registry._stop_failures, 1)
        with self.assertRaises(SessionError) as error:
            self.create()
        self.assertEqual(error.exception.code, 'udp_cleanup_failed')

    def test_trusted_worker_revoke_is_idempotent_and_immediately_prevents_touch(self):
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        self.assertTrue(self.registry.revoke(sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertTrue(self.registry.revoke(sid))
        self.assertFalse(self.registry.revoke('f' * 32))
        self.assertEqual(self.worker.stops, 1)

    def test_status_reports_current_lease_and_does_not_renew_it(self):
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        self.clock.advance(1)
        self.assertEqual(self.registry.status('huoguo', sid)['expires_in_ms'], 2000)
        self.clock.advance(2)
        self.assertIsNone(self.registry.status('huoguo', sid))
        self.assertEqual(self.worker.stops, 1)

    def test_close_is_permanent_and_cleanup_once(self):
        sid = self.create()['session']
        self.registry.close()
        self.registry.close()
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertEqual(self.worker.stops, 1)
        with self.assertRaises(SessionError) as error:
            self.create()
        self.assertEqual(error.exception.code, 'registry_closed')

    def test_expired_session_does_not_block_fresh_account(self):
        first = self.create()['session']
        self.clock.advance(10)
        second_worker = Worker()
        second = self.registry.create('wyw', {}, lambda config: second_worker)
        self.assertNotEqual(first, second['session'])
        self.assertEqual(self.worker.stops, 1)
        self.assertTrue(self.registry.cancel('huoguo', first))
        self.assertEqual(second_worker.stops, 0)

    def test_tombstone_storage_is_bounded_and_expires(self):
        self.registry.MAX_TOMBSTONES = 2
        ids = []
        for _ in range(3):
            sid = self.create()['session']; ids.append(sid)
            self.registry.cancel('huoguo', sid)
        self.assertFalse(self.registry.cancel('huoguo', ids[0]))
        self.assertTrue(self.registry.cancel('huoguo', ids[-1]))
        self.clock.advance(300)
        self.assertFalse(self.registry.cancel('huoguo', ids[-1]))


class RaceChecks(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = UdpLanSessions('192.168.9.128', clock=self.clock)
        self.worker = Worker()
        self.entered, self.release = threading.Event(), threading.Event()
        self.results = []

    def launch(self, fn):
        def run():
            try: self.results.append(fn())
            except SessionError as error: self.results.append(error.code)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(self.entered.wait(2), 'owned callback failed to enter')
        return thread

    def finish(self, thread):
        self.release.set(); thread.join(2)
        self.assertFalse(thread.is_alive(), 'owned callback did not finish')

    def assert_busy(self):
        with self.assertRaises(SessionError) as error:
            self.registry.create('wyw', {}, lambda config: Worker())
        self.assertEqual(error.exception.code, 'udp_session_busy')

    def test_cancel_during_factory_prevents_late_start_and_reserves_until_cleanup(self):
        observed = {}
        def factory(config):
            observed.update(config); self.entered.set(); self.release.wait(2)
            return self.worker
        thread = self.launch(lambda: self.registry.create('huoguo', {}, factory))
        sid = observed['session']
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assert_busy()
        self.finish(thread)
        self.assertEqual(self.results, ['udp_session_revoked'])
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 1))
        self.assertIsNotNone(self.registry.create('wyw', {}, lambda config: Worker()))

    def test_factory_timeout_during_build_stops_late_resource_once(self):
        def factory(config):
            self.entered.set(); self.release.wait(2); return self.worker
        thread = self.launch(lambda: self.registry.create('huoguo', {}, factory))
        self.clock.advance(10)
        self.assertEqual(self.registry.reap(), 1)
        self.assert_busy()
        self.finish(thread)
        self.assertEqual(self.results, ['udp_session_revoked'])
        self.assertEqual(self.worker.stops, 1)

    def test_concurrent_start_cancel_defers_stop_but_revokes_touch_immediately(self):
        def start():
            self.worker.starts += 1; self.entered.set(); self.release.wait(2)
        self.worker.start = start
        sid = self.registry.create('huoguo', {}, lambda config: self.worker)['session']
        thread = self.launch(lambda: self.registry.authenticated_ready(sid))
        self.assertTrue(self.registry.cancel('huoguo', sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual(self.worker.stops, 0)
        self.assert_busy()
        self.finish(thread)
        self.assertEqual(self.results, [False])
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))

    def test_new_session_cannot_overlap_owned_stop_in_progress(self):
        def stop():
            self.worker.stops += 1; self.entered.set(); self.release.wait(2)
        self.worker.stop = stop
        sid = self.registry.create('huoguo', {}, lambda config: self.worker)['session']
        self.registry.authenticated_ready(sid)
        thread = self.launch(lambda: self.registry.cancel('huoguo', sid))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assert_busy()
        self.finish(thread)
        self.assertEqual(self.results, [True])
        self.assertEqual(self.worker.stops, 1)
        self.assertIsNotNone(self.registry.create('wyw', {}, lambda config: Worker()))

    def test_replacement_create_drains_deferred_expiry_before_new_factory(self):
        def stop():
            self.worker.stops += 1; self.entered.set(); self.release.wait(2)
        self.worker.stop = stop
        sid = self.registry.create('huoguo', {}, lambda config: self.worker)['session']
        self.registry.authenticated_ready(sid)
        self.clock.advance(3)
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: self.fail('expired write')))
        self.assertEqual(self.worker.stops, 0)
        new_factory_calls = []
        def factory(config):
            new_factory_calls.append(config['session'])
            return Worker()
        thread = self.launch(lambda: self.registry.create('wyw', {}, factory))
        self.assertEqual(new_factory_calls, [])
        self.assertFalse(self.registry.touch_allowed(sid))
        self.finish(thread)
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(len(new_factory_calls), 1)
        self.assertEqual(self.results[0]['session'], new_factory_calls[0])

    def test_concurrent_duplicate_ready_has_only_one_start(self):
        def start():
            self.worker.starts += 1; self.entered.set(); self.release.wait(2)
        self.worker.start = start
        sid = self.registry.create('huoguo', {}, lambda config: self.worker)['session']
        thread = self.launch(lambda: self.registry.authenticated_ready(sid))
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.assertEqual(self.worker.starts, 1)
        self.finish(thread)
        self.assertEqual(self.results, [True])

    def test_revoke_serializes_after_admitted_touch_then_blocks_later_writes(self):
        sid = self.registry.create('huoguo', {}, lambda config: self.worker)['session']
        self.registry.authenticated_ready(sid)
        writes, revoked = [], threading.Event()
        def write():
            self.entered.set(); self.release.wait(2); writes.append('admitted')
        thread = self.launch(lambda: self.registry.dispatch_touch(sid, write))
        def revoke():
            self.registry.revoke(sid); revoked.set()
        revoker = threading.Thread(target=revoke)
        revoker.start()
        self.assertFalse(revoked.wait(.02), 'revoke must wait for admitted owned write')
        self.finish(thread)
        revoker.join(2)
        self.assertFalse(revoker.is_alive())
        self.assertTrue(revoked.is_set())
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: writes.append('late')))
        self.assertEqual(writes, ['admitted'])
        self.assertEqual(self.worker.stops, 1)

    def test_close_during_factory_prevents_late_resource_or_replacement(self):
        def factory(config):
            self.entered.set(); self.release.wait(2); return self.worker
        thread = self.launch(lambda: self.registry.create('huoguo', {}, factory))
        self.registry.close()
        self.finish(thread)
        self.assertEqual(self.results, ['udp_session_revoked'])
        self.assertEqual(self.worker.stops, 1)
        with self.assertRaises(SessionError) as error:
            self.registry.create('wyw', {}, lambda config: Worker())
        self.assertEqual(error.exception.code, 'registry_closed')


class ShutdownBarrierChecks(unittest.TestCase):
    """Owned inert callbacks only; no UDP sockets, media or account input."""
    def setUp(self):
        self.registry = UdpLanSessions('192.168.9.128')
        self.worker = Worker()
        self.entered, self.release = threading.Event(), threading.Event()
        self.threads = []

    def tearDown(self):
        self.release.set()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive(), 'owned test callback did not finish')

    def launch(self, operation):
        results, finished = [], threading.Event()
        def run():
            try:
                results.append(operation())
            except SessionError as error:
                results.append(error.code)
            finally:
                finished.set()
        thread = threading.Thread(target=run)
        self.threads.append(thread)
        thread.start()
        return results, finished

    def create(self):
        return self.registry.create('test-owner', {}, lambda config: self.worker)['session']

    def blocked_stop(self):
        self.worker.stops += 1
        self.entered.set()
        if not self.release.wait(2):
            raise TimeoutError('owned test release missing')

    def test_idle_shutdown_is_confirmed_and_registry_cannot_reopen(self):
        self.assertEqual(self.registry.close_and_wait(.5),
                         {'quiescence_confirmed': True, 'stop_failures': 0})
        with self.assertRaises(SessionError) as error:
            self.create()
        self.assertEqual(error.exception.code, 'registry_closed')

    def test_timeout_argument_is_finite_positive_bounded_and_not_boolean(self):
        for timeout in (0, -.1, 60.001, float('inf'), float('nan'), True, None, '1'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.registry.close_and_wait(timeout)
        self.assertFalse(self.registry._closed)

    def test_shutdown_owned_stop_does_not_hold_lock_and_waits_for_completion(self):
        sid = self.create()
        self.worker.stop = self.blocked_stop
        results, finished = self.launch(lambda: self.registry.close_and_wait(1))
        self.assertTrue(self.entered.wait(1))
        self.assertFalse(finished.wait(.04), 'closed flag is not cleanup completion')
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertTrue(self.registry.cancel('test-owner', sid))
        self.assertEqual(self.worker.stops, 1)
        self.release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(results, [{'quiescence_confirmed': True, 'stop_failures': 0}])

    def test_inflight_udp_revoke_and_shutdown_do_not_repeat_or_abandon_stop(self):
        sid = self.create()
        self.registry.authenticated_ready(sid)
        self.worker.stop = self.blocked_stop
        revoke_results, revoked = self.launch(lambda: self.registry.revoke(sid))
        self.assertTrue(self.entered.wait(1))
        shutdown_results, shutdown = self.launch(lambda: self.registry.close_and_wait(1))
        self.assertFalse(shutdown.wait(.04), 'gateway must await existing STOP owner')
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertTrue(self.registry.cancel('test-owner', sid))
        self.assertFalse(revoked.is_set())
        self.assertEqual(self.worker.stops, 1)
        self.release.set()
        self.assertTrue(revoked.wait(1))
        self.assertTrue(shutdown.wait(1))
        self.assertEqual(revoke_results, [True])
        self.assertEqual(shutdown_results, [{'quiescence_confirmed': True, 'stop_failures': 0}])

    def test_factory_and_start_must_finish_before_shutdown_is_confirmed(self):
        for phase in ('factory', 'start'):
            with self.subTest(phase=phase):
                self.registry = UdpLanSessions('192.168.9.128')
                self.worker = Worker()
                self.entered.clear(); self.release.clear()
                def factory(config):
                    self.entered.set()
                    if not self.release.wait(2):
                        raise TimeoutError('owned test release missing')
                    return self.worker
                def start():
                    self.worker.starts += 1
                    self.entered.set()
                    if not self.release.wait(2):
                        raise TimeoutError('owned test release missing')
                if phase == 'factory':
                    operation = lambda: self.registry.create('test-owner', {}, factory)
                else:
                    sid = self.create()
                    self.worker.start = start
                    operation = lambda: self.registry.authenticated_ready(sid)
                operation_results, operation_done = self.launch(operation)
                self.assertTrue(self.entered.wait(1))
                results, finished = self.launch(lambda: self.registry.close_and_wait(1))
                self.assertFalse(finished.wait(.04))
                self.assertEqual(self.worker.stops, 0, 'do not stop while factory/start owns setup')
                self.release.set()
                self.assertTrue(operation_done.wait(1))
                self.assertTrue(finished.wait(1))
                self.assertEqual(operation_results, ['udp_session_revoked'] if phase == 'factory' else [False])
                self.assertEqual(results, [{'quiescence_confirmed': True, 'stop_failures': 0}])
                self.assertEqual(self.worker.stops, 1)

    def test_timeout_retains_inflight_stop_and_late_completion_is_not_retried(self):
        sid = self.create()
        self.worker.stop = self.blocked_stop
        _, stop_done = self.launch(lambda: self.registry.revoke(sid))
        self.assertTrue(self.entered.wait(1))
        with self.assertRaises(SessionError) as error:
            self.registry.close_and_wait(.025)
        self.assertEqual(error.exception.code, 'udp_shutdown_quiescence_timeout')
        self.assertIsNotNone(self.registry._active)
        self.assertEqual(self.worker.stops, 1)
        self.release.set()
        self.assertTrue(stop_done.wait(1))
        self.assertEqual(self.registry.close_and_wait(.5),
                         {'quiescence_confirmed': True, 'stop_failures': 0})
        self.assertEqual(self.worker.stops, 1)

    def test_timeout_also_bounds_cleanup_claimed_by_shutdown_itself(self):
        self.create(); self.worker.stop = self.blocked_stop
        with self.assertRaises(SessionError) as error:
            self.registry.close_and_wait(.025)
        self.assertEqual(error.exception.code, 'udp_shutdown_quiescence_timeout')
        self.assertTrue(self.entered.is_set())
        self.assertIsNotNone(self.registry._active)
        self.assertEqual(self.worker.stops, 1)
        self.release.set()
        self.assertEqual(self.registry.close_and_wait(.5),
                         {'quiescence_confirmed': True, 'stop_failures': 0})
        self.assertEqual(self.worker.stops, 1)

    def test_timeout_covers_pending_factory_and_start_without_early_release(self):
        for phase in ('factory', 'start'):
            with self.subTest(phase=phase):
                self.registry = UdpLanSessions('192.168.9.128')
                self.worker = Worker()
                self.entered.clear(); self.release.clear()
                def factory(config):
                    self.entered.set(); self.release.wait(2)
                    return self.worker
                def start():
                    self.worker.starts += 1
                    self.entered.set(); self.release.wait(2)
                if phase == 'factory':
                    operation = lambda: self.registry.create('test-owner', {}, factory)
                else:
                    sid = self.create(); self.worker.start = start
                    operation = lambda: self.registry.authenticated_ready(sid)
                _, finished = self.launch(operation)
                self.assertTrue(self.entered.wait(1))
                with self.assertRaises(SessionError) as error:
                    self.registry.close_and_wait(.025)
                self.assertEqual(error.exception.code, 'udp_shutdown_quiescence_timeout')
                self.assertIsNotNone(self.registry._active)
                self.assertEqual(self.worker.stops, 0)
                self.release.set()
                self.assertTrue(finished.wait(1))
                self.assertEqual(self.registry.close_and_wait(.5),
                                 {'quiescence_confirmed': True, 'stop_failures': 0})
                self.assertEqual(self.worker.stops, 1)

    def test_duplicate_shutdown_waiters_share_one_cleanup_owner(self):
        self.create(); self.worker.stop = self.blocked_stop
        first, first_done = self.launch(lambda: self.registry.close_and_wait(1))
        self.assertTrue(self.entered.wait(1))
        second, second_done = self.launch(lambda: self.registry.close_and_wait(1))
        self.assertFalse(first_done.wait(.025))
        self.assertFalse(second_done.wait(.025))
        self.assertEqual(self.worker.stops, 1)
        self.release.set()
        self.assertTrue(first_done.wait(1)); self.assertTrue(second_done.wait(1))
        self.assertEqual(first, second)
        self.assertEqual(self.worker.stops, 1)

    def test_completed_stop_failure_is_not_success_or_retried(self):
        self.create()
        def fail():
            self.worker.stops += 1
            raise RuntimeError('untrusted owned failure detail')
        self.worker.stop = fail
        for _ in range(2):
            with self.assertRaises(SessionError) as error:
                self.registry.close_and_wait(.5)
            self.assertEqual(error.exception.code, 'udp_cleanup_failed')
            self.assertNotIn('detail', str(error.exception))
        self.assertEqual(self.worker.stops, 1)

    def test_shutdown_thread_start_failure_is_closed_and_not_success(self):
        self.create()
        with patch('udp_lan_sessions.threading.Thread.start', side_effect=RuntimeError('untrusted detail')):
            with self.assertRaises(SessionError) as error:
                self.registry.close_and_wait(.5)
        self.assertEqual(error.exception.code, 'udp_shutdown_worker_unavailable')
        self.assertTrue(self.registry._closed)
        self.assertTrue(self.registry._cleanup_failed)
        self.assertFalse(self.registry._quiescent.is_set())
        self.assertEqual(self.worker.stops, 0)


if __name__ == '__main__':
    unittest.main()
