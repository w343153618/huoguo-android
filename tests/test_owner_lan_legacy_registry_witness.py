"""Exact dd43 registry locking checks; no socket, password, guest or service.

Load hash-pinned legacy Python bytes in private module namespaces. The current
files still match these pins; if they change, require the exact local Git object
rather than silently substituting a new registry or skipping coverage.
"""
import builtins
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'dd43a39f49f6dceb55854fbc6e43367d34f381c3'
PINS = {
    'stream_settings': 'd5bf38e20dd8e0c5367755ad08d2c276dd156832203e8d31eac1c7f536db0406',
    'udp_network_scope': '0d610954e1d84f4fdd29f5b7772a00485c31657c7e60d995c17b8e79b8dae305',
    'udp_nps_profile': 'ae552dad51da32d7abf57a72538c173f2f21297602d19ef0477a97e1d5dba9dd',
    'udp_lan_sessions': '8e3afce002c9eab223e47df5535069fa63f1748038fe0a658b03dc9db041e2b6',
}


def legacy_modules():
    loaded = {}
    ordinary_import = builtins.__import__

    def legacy_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in loaded:
            return loaded[name]
        return ordinary_import(name, globals, locals, fromlist, level)

    for name, digest in PINS.items():
        path = ROOT / (name + '.py')
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != digest:
            try:
                result = subprocess.run(['git', 'show', COMMIT+':'+name+'.py'],
                    cwd=ROOT, check=True, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=5)
                source = result.stdout
            except (OSError, subprocess.SubprocessError):
                raise RuntimeError('required_frozen_dd43_source_unavailable') from None
        if hashlib.sha256(source).hexdigest() != digest:
            raise RuntimeError('frozen_dd43_source_hash_mismatch')
        private_name = 'tests._owner_lan_legacy_dd43.' + name
        module = types.ModuleType(private_name)
        module.__file__ = 'frozen-git:'+COMMIT+':'+name+'.py'
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=legacy_import)
        sys.modules[private_name] = module  # Required by the actual dataclasses.
        loaded[name] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return loaded


class ControlledWorker:
    def __init__(self, fail):
        self.entered, self.release = threading.Event(), threading.Event()
        self.fail = fail

    def start(self):
        raise AssertionError('fixture_never_sends_READY')

    def stop(self):
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError('inert_cleanup_timeout')
        if self.fail:
            raise RuntimeError('inert_cleanup_failure')


class FrozenRegistryWitnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = legacy_modules()['udp_lan_sessions']

    def setUp(self):
        self.no_socket = mock.patch('socket.socket', side_effect=AssertionError('fixture_must_not_open_socket'))
        self.no_socket.start()
        self.addCleanup(self.no_socket.stop)
        self.registry = self.legacy.UdpLanSessions('146.56.249.175',15556,
            clock=lambda:100., network_scope='nps_owner', node='m1',
            scope_guard=lambda:True, guest_serial='emulator-5556',
            guest_avd='RemoteAndroid17Compare')
        self.settings = {'network_scope':'nps_owner', 'node':'m1',
                         'max_fps':30, 'seconds':120, 'audio':False, 'touch':False}
        self.factory_calls = 0
        self.addCleanup(self.registry.close)

    def failed_factory_outcome(self):
        def pre_guest_failure(config):
            self.factory_calls += 1
            raise OSError('inert_bind_conflict_before_guest')
        with self.assertRaises(self.legacy.SessionError) as observed:
            self.registry.create('wyw', self.settings, pre_guest_failure)
        return observed.exception.status, observed.exception.code

    @contextmanager
    def pending_old_stop(self, *, fail):
        worker = ControlledWorker(fail)
        descriptor = self.registry.create('wyw', self.settings, lambda config:worker)
        errors = []
        def cancel():
            try:
                self.registry.cancel('wyw', descriptor['session'])
            except BaseException as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=cancel, name='owned_inert_legacy_stop')
        thread.start()
        try:
            self.assertTrue(worker.entered.wait(1), 'old_stop_not_entered')
            yield
        finally:
            worker.release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive(), 'inert_stop_not_joined')
            self.assertEqual(errors, [])

    def test_inflight_normal_stop_is_busy_before_factory(self):
        with self.pending_old_stop(fail=False):
            self.assertEqual(self.failed_factory_outcome(), (409,'udp_session_busy'))
            self.assertEqual(self.factory_calls, 0)

    def test_confirmed_normal_stop_allows_exact_failed_factory_witness(self):
        with self.pending_old_stop(fail=False):
            pass
        self.assertEqual(self.failed_factory_outcome(), (503,'udp_worker_unavailable'))
        self.assertEqual(self.factory_calls, 1)

    def test_inflight_eventually_failed_stop_still_is_busy(self):
        with self.pending_old_stop(fail=True):
            self.assertEqual(self.failed_factory_outcome(), (409,'udp_session_busy'))
            self.assertEqual(self.factory_calls, 0)

    def test_failed_stop_refuses_witness_before_factory(self):
        with self.pending_old_stop(fail=True):
            pass
        self.assertEqual(self.failed_factory_outcome(), (503,'udp_cleanup_failed'))
        self.assertEqual(self.factory_calls, 0)


if __name__ == '__main__':
    unittest.main()
