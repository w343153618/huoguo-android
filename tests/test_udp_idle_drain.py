"""Actual registry idle/admission races, using inert owned workers only."""
from email.message import Message
import io
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_lan_gateway import handler_for as lan_handler
from udp_lan_sessions import SessionError, UdpLanSessions
from udp_nps_gateway import handler_for as nps_handler
from udp_nps_profile import PUBLIC_HOST, owner_profile


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class Worker:
    def __init__(self): self.starts = self.stops = 0; self.on_start = self.on_stop = lambda: None
    def start(self): self.starts += 1; self.on_start()
    def stop(self): self.stops += 1; self.on_stop()


class IdleDrainChecks(unittest.TestCase):
    def setUp(self):
        self.clock, self.worker = Clock(), Worker()
        self.registry = UdpLanSessions('192.168.9.128', clock=self.clock)
        self.releases, self.threads = [], []

    def tearDown(self):
        for event in self.releases: event.set()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive(), 'owned fixture thread did not finish')
        self.registry.close()

    def create(self, factory=None):
        return self.registry.create('wyw', {}, factory or (lambda config: self.worker))

    def blocker(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def wait():
            entered.set()
            if not release.wait(2): raise TimeoutError('fixture callback was not released')
        return entered, release, wait

    def launch(self, operation):
        result = []
        def run():
            try: result.append(operation())
            except SessionError as error: result.append(error.code)
            except BaseException as error: result.append(error)
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread); thread.start()
        return thread, result

    def finish(self, thread, release):
        release.set(); thread.join(2)
        self.assertFalse(thread.is_alive(), 'owned callback did not finish')

    def test_idle_drain_is_sticky_idempotent_and_allocates_no_session_or_worker(self):
        with patch('udp_lan_sessions.secrets.token_hex') as token, \
                patch('udp_lan_sessions.secrets.token_bytes') as key:
            self.assertTrue(self.registry.begin_idle_drain())
            self.assertTrue(self.registry.begin_idle_drain())
            factory = Mock()
            with self.assertRaises(SessionError) as raised: self.create(factory)
            self.assertEqual((raised.exception.status, raised.exception.code),
                             (503, 'udp_registry_draining'))
            factory.assert_not_called(); token.assert_not_called(); key.assert_not_called()
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 0))
        self.assertIsNone(self.registry._active)
        self.assertTrue(self.registry.close_and_wait()['quiescence_confirmed'])
        self.assertTrue(self.registry.begin_idle_drain())
        with self.assertRaises(SessionError) as raised: self.create()
        self.assertEqual(raised.exception.code, 'registry_closed')

    def test_default_admission_is_unchanged_and_busy_drain_does_not_cancel(self):
        sid = self.create()['session']
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.registry.status('wyw', sid)['phase'], 'waiting')
        self.assertTrue(self.registry.authenticated_ready(sid))
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertTrue(self.registry.touch_allowed(sid))
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 0))
        with self.assertRaises(SessionError) as raised: self.create()
        self.assertEqual(raised.exception.code, 'udp_session_busy')
        self.assertTrue(self.registry.cancel('wyw', sid))
        replacement = self.create(lambda config: Worker())
        self.assertEqual(self.registry.status('wyw', replacement['session'])['phase'], 'waiting')

    def test_expired_reservation_is_not_reaped_to_manufacture_idle(self):
        sid = self.create()['session']
        self.clock.now += self.registry.READY_SECONDS
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.worker.stops, 0)
        self.assertEqual(self.registry._active.phase, 'waiting')
        self.assertNotIn(sid, self.registry._tombstones)
        self.assertEqual(self.registry.reap(), 1)
        self.assertEqual(self.worker.stops, 1)
        self.assertTrue(self.registry.begin_idle_drain())

    def test_create_wins_lock_and_building_factory_remains_uninterrupted(self):
        entered, release, wait = self.blocker()
        def factory(config): wait(); return self.worker
        thread, result = self.launch(lambda: self.create(factory))
        self.assertTrue(entered.wait(2))
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.registry._active.phase, 'building')
        self.assertFalse(self.registry._draining)
        self.assertEqual(self.worker.stops, 0)
        self.finish(thread, release)
        self.assertIsInstance(result[0], dict)
        self.assertTrue(self.registry.authenticated_ready(result[0]['session']))

    def test_drain_wins_against_already_parsing_create(self):
        import udp_lan_sessions
        original_parse = udp_lan_sessions.parse_udp_settings
        entered, release, wait = self.blocker()
        def parse(settings):
            value = original_parse(settings); wait(); return value
        with patch('udp_lan_sessions.parse_udp_settings', side_effect=parse):
            thread, result = self.launch(self.create)
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.registry.begin_idle_drain())
            self.finish(thread, release)
        self.assertEqual(result, ['udp_registry_draining'])
        self.assertIsNone(self.registry._active)
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 0))

    def test_cancelled_but_unfinished_factory_cannot_be_mistaken_for_idle(self):
        entered, release, wait = self.blocker()
        captured = {}
        def factory(config): captured.update(config); wait(); return self.worker
        thread, result = self.launch(lambda: self.create(factory))
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.registry.cancel('wyw', captured['session']))
        self.assertEqual(self.registry._active.phase, 'closed')
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.worker.stops, 0)
        self.finish(thread, release)
        self.assertEqual(result, ['udp_session_revoked'])
        self.assertEqual(self.worker.stops, 1)
        self.assertTrue(self.registry.begin_idle_drain())

    def test_concurrent_create_and_drain_have_only_the_two_atomic_outcomes(self):
        for _ in range(24):
            candidate, worker = UdpLanSessions('192.168.9.128'), Worker()
            barrier = threading.Barrier(3)
            def create(): barrier.wait(2); return candidate.create('wyw', {}, lambda config: worker)
            def drain(): barrier.wait(2); return candidate.begin_idle_drain()
            creator, created = self.launch(create)
            drainer, drained = self.launch(drain)
            barrier.wait(2); creator.join(2); drainer.join(2)
            self.assertFalse(creator.is_alive()); self.assertFalse(drainer.is_alive())
            self.assertEqual(len(created), 1); self.assertEqual(len(drained), 1)
            if drained[0] is True:
                self.assertEqual(created, ['udp_registry_draining'])
                self.assertIsNone(candidate._active)
                self.assertEqual(worker.stops, 0)
            else:
                self.assertIs(drained[0], False)
                self.assertIsInstance(created[0], dict)
                self.assertFalse(candidate._draining)
                self.assertEqual(candidate._active.phase, 'waiting')
                self.assertEqual(worker.stops, 0)
            candidate.close()

    def test_start_in_progress_and_active_touch_permissions_survive_refused_drain(self):
        entered, release, wait = self.blocker(); self.worker.on_start = wait
        sid = self.create()['session']
        thread, result = self.launch(lambda: self.registry.authenticated_ready(sid))
        self.assertTrue(entered.wait(2))
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertTrue(self.registry._active.starting)
        self.assertEqual(self.worker.stops, 0)
        self.finish(thread, release)
        self.assertEqual(result, [True])
        self.assertTrue(self.registry.touch_allowed(sid))

    def test_revoked_but_blocking_stop_is_not_quiescent_maintenance(self):
        entered, release, wait = self.blocker(); self.worker.on_stop = wait
        sid = self.create()['session']
        self.registry.authenticated_ready(sid)
        thread, result = self.launch(lambda: self.registry.cancel('wyw', sid))
        self.assertTrue(entered.wait(2))
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.registry._active.phase, 'closed')
        self.assertFalse(self.registry._quiescent.is_set())
        self.assertEqual(self.worker.stops, 1)
        self.finish(thread, release)
        self.assertEqual(result, [True])
        self.assertTrue(self.registry.begin_idle_drain())
        self.assertEqual(self.worker.stops, 1)

    def test_deferred_touch_expiry_cleanup_is_not_run_by_drain(self):
        sid = self.create()['session']; self.registry.authenticated_ready(sid)
        self.clock.now += self.registry.ALIVE_SECONDS
        self.assertFalse(self.registry.dispatch_touch(sid, lambda: self.fail('expired input')))
        self.assertEqual(len(self.registry._pending_cleanup), 1)
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertEqual(self.worker.stops, 0)
        self.assertEqual(len(self.registry._pending_cleanup), 1)
        self.registry.reap()
        self.assertEqual(self.worker.stops, 1)
        self.assertTrue(self.registry.begin_idle_drain())

    def test_cleanup_failure_and_unconfirmed_completion_cannot_claim_idle(self):
        self.registry._quiescent.clear()
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertFalse(self.registry._draining)
        self.registry._quiescent.set()
        def fail(): raise RuntimeError('public inert fixture failure')
        self.worker.on_stop = fail
        sid = self.create()['session']; self.registry.cancel('wyw', sid)
        self.assertIsNone(self.registry._active)
        self.assertTrue(self.registry._quiescent.is_set())
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertFalse(self.registry._draining)
        with self.assertRaises(SessionError) as raised: self.create()
        self.assertEqual(raised.exception.code, 'udp_cleanup_failed')
        self.assertEqual(self.worker.stops, 1)

    def test_closed_without_prior_drain_is_not_an_idle_acceptance_receipt(self):
        self.registry.close()
        self.assertFalse(self.registry.begin_idle_drain())
        self.assertFalse(self.registry._draining)
        with self.assertRaises(SessionError) as raised: self.create()
        self.assertEqual(raised.exception.code, 'registry_closed')


class IdleDrainHttpBoundaryChecks(unittest.TestCase):
    def fixture(self, scope):
        if scope == 'nps_owner':
            profile = owner_profile('m1')
            candidate = UdpLanSessions(PUBLIC_HOST, profile.public_media.port,
                network_scope=scope, node='m1', scope_guard=lambda: True,
                guest_serial=profile.guest_serial, guest_avd=profile.guest_avd)
            verifier = SimpleNamespace(permits_peer=lambda peer: True, verify=lambda: True)
            handler = nps_handler(candidate, Mock(return_value=Worker()), profile,
                                  busy=lambda: False, scope=verifier)
            body = {'network_scope': scope, 'node': 'm1'}
        else:
            candidate = UdpLanSessions('192.168.9.128')
            handler = lan_handler(candidate, Mock(return_value=Worker()),
                                  '192.168.9.128', busy=lambda: False)
            body = {}
        self.addCleanup(candidate.close)
        return candidate, handler, body

    def request(self, handler, body, path='/udp/session', auth=None):
        request = handler.__new__(handler)
        request.path, request.client_address = path, ('192.168.9.149', 45678)
        encoded = json.dumps(body).encode()
        request.headers = Message(); request.headers.add_header('Content-Length', str(len(encoded)))
        request.rfile, request.connection = io.BytesIO(encoded), Mock()
        request.responses = []
        request.reply = lambda status, value: request.responses.append((status, value))
        request.account = 'wyw'
        request.auth = auth or Mock(return_value=True)
        return request

    def test_authenticated_post_after_drain_returns_explicit_503_in_both_adapters(self):
        for scope in ('lan', 'nps_owner'):
            with self.subTest(scope=scope):
                candidate, handler, body = self.fixture(scope)
                self.assertTrue(candidate.begin_idle_drain())
                request = self.request(handler, body); request.do_POST()
                self.assertEqual(request.responses, [(503, {'error': 'udp_registry_draining'})])
                request.auth.assert_called_once_with()
                self.assertIsNone(candidate._active)

    def test_http_settings_do_not_authorize_drain_and_no_public_management_route_exists(self):
        for scope in ('lan', 'nps_owner'):
            candidate, handler, body = self.fixture(scope)
            for path in ('/udp/drain', '/maintenance/drain', '/udp/session/drain/maintenance'):
                for method in ('POST', 'GET', 'DELETE'):
                    # A session-shaped GET/DELETE is merely not-found, never maintenance.
                    request = self.request(handler, body, path); getattr(request, 'do_' + method)()
                    self.assertEqual(request.responses[0][0], 404)
                    self.assertFalse(candidate._draining)
            request = self.request(handler, {**body, 'draining': True}); request.do_POST()
            self.assertEqual(request.responses[0][0], 400 if scope == 'nps_owner' else 201)
            self.assertFalse(candidate._draining)

    def test_already_authenticating_post_cannot_cross_a_later_idle_drain(self):
        for scope in ('lan', 'nps_owner'):
            with self.subTest(scope=scope):
                candidate, handler, body = self.fixture(scope)
                entered, release = threading.Event(), threading.Event()
                errors = []
                def auth():
                    entered.set()
                    if not release.wait(2): raise TimeoutError('fixture auth not released')
                    return True
                request = self.request(handler, body, auth=auth)
                def run():
                    try: request.do_POST()
                    except BaseException as error: errors.append(type(error).__name__)
                thread = threading.Thread(target=run, daemon=True); thread.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(candidate.begin_idle_drain())
                finally:
                    release.set(); thread.join(2)
                self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
                self.assertEqual(request.responses, [(503, {'error': 'udp_registry_draining'})])
                self.assertIsNone(candidate._active)


if __name__ == '__main__': unittest.main()
