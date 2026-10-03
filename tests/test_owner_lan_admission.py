"""Inert admission checks plus kernel exclusivity on an owned ephemeral socket.

The real socket fixture calls the shared binding primitive directly with
('127.0.0.1', 0). It never enters OwnerLanAdmission, binds production45965, or
starts HTTP, media, a gateway, or a guest. Full guard ordering is inert-tested.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/probes/owner_lan_admission.py'
spec = importlib.util.spec_from_file_location('owner_lan_admission', SOURCE)
admission = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admission
spec.loader.exec_module(admission)


def expected():
    return admission.ExpectedGateway(123, 'start-20261003T120000', 'a'*64, 'b'*64)


def readback(**updates):
    result = dict(vars(expected()), coverage_complete=True, owned_descendants=0,
                  hardware_process_groups=0, packetizer_processes=0,
                  guest_control_processes=0)
    result.update(updates)
    return result


def quiescence(**updates):
    result = dict(event='candidate_shutdown', quiescence_confirmed=True,
                  stop_failures=0, gateway_exit_confirmed=True,
                  owned_media_exit_confirmed=True)
    result.update(updates)
    return result


class InertSocket:
    def __init__(self, events, *, busy=False, reuse=False, bad_interface=False):
        self.events, self.busy, self.reuse = events, busy, reuse
        self.bad_interface, self.index, self.closed = bad_interface, 0, False

    def getsockopt(self, level, option):
        if level == socket.SOL_SOCKET:
            return int(self.reuse)
        return self.index + int(self.bad_interface)

    def setsockopt(self, level, option, value):
        self.events.append(('option', level, option, value))
        self.index = value

    def bind(self, endpoint):
        self.events.append(('bind', endpoint))
        if self.busy:
            raise OSError('inert bind occupied')

    def getsockname(self):
        return admission.LOOPBACK_ENDPOINT

    def close(self):
        self.closed = True
        self.events.append(('close',))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.platform = mock.patch.object(admission.sys, 'platform', 'darwin')
        self.index = mock.patch.object(admission.socket, 'if_nametoindex', return_value=1)
        self.platform.start()
        self.index.start()
        self.addCleanup(self.platform.stop)
        self.addCleanup(self.index.stop)

    def guard(self, witness=None, snapshot=None, **socket_options):
        events = []
        owned = InertSocket(events, **socket_options)
        def get_witness():
            events.append(('witness',))
            return witness if witness is not None else {'status':503,'error':'udp_worker_unavailable'}
        def get_readback():
            events.append(('readback',))
            return snapshot if snapshot is not None else readback()
        guard = admission.OwnerLanAdmission(expected(), get_witness, get_readback,
                                             _socket_factory=lambda *args: owned)
        return guard, owned, events

    def test_dry_run_opens_nothing(self):
        output = io.StringIO()
        with mock.patch.object(admission.socket, 'socket', side_effect=AssertionError), contextlib.redirect_stdout(output):
            admission.main([])
        value = json.loads(output.getvalue())
        self.assertFalse(value['binding_performed'])
        self.assertEqual(value['signals_sent'], 0)
        self.assertEqual(value['http_requests_sent'], 0)

    def test_bind_before_witness_then_readback_and_no_early_release(self):
        guard, owned, events = self.guard()
        with guard:
            self.assertEqual(guard.state, 'admitted')
            self.assertFalse(owned.closed)
            guard.mark_owned_lan_starting()
            self.assertFalse(owned.closed)
            guard.confirm_owned_lan_quiescence(quiescence())
            self.assertFalse(owned.closed)
        self.assertTrue(owned.closed)
        self.assertLess(events.index(('bind', admission.LOOPBACK_ENDPOINT)), events.index(('witness',)))
        self.assertLess(events.index(('witness',)), events.index(('readback',)))
        self.assertEqual(events[-1], ('close',))

    def test_busy_bind_does_not_call_witness_or_readback(self):
        guard, owned, events = self.guard(busy=True)
        with self.assertRaisesRegex(admission.AdmissionError, '^reservation_bind_unavailable$'):
            guard.__enter__()
        self.assertTrue(owned.closed)
        self.assertNotIn(('witness',), events)
        self.assertNotIn(('readback',), events)

    def test_reuse_or_interface_mismatch_rejected_before_witness(self):
        for opts in ({'reuse':True}, {'bad_interface':True}):
            guard, owned, events = self.guard(**opts)
            with self.assertRaises(admission.AdmissionError):
                guard.__enter__()
            self.assertTrue(owned.closed)
            self.assertNotIn(('witness',), events)

    def test_exact_witness_only(self):
        cases = [({'status':409,'error':'udp_session_busy'}, 'witness_busy'),
                 ({'status':503,'error':'udp_cleanup_failed'}, 'witness_cleanup_failed'),
                 ({'status':401,'error':'account_required'}, 'witness_auth_rejected'),
                 ({'status':503,'error':'unknown'}, 'witness_not_idle'),
                 ({'status':201,'error':'udp_worker_unavailable'}, 'witness_not_idle'),
                 ({'status':True,'error':'udp_worker_unavailable'}, 'witness_schema_rejected'),
                 ({'status':503,'error':'udp_worker_unavailable','descriptor':{}}, 'witness_schema_rejected')]
        for result, code in cases:
            with self.subTest(code=code):
                guard, owned, events = self.guard(witness=result)
                with self.assertRaisesRegex(admission.AdmissionError, '^'+code+'$'):
                    guard.__enter__()
                self.assertTrue(owned.closed)
                self.assertNotIn(('readback',), events)

    def test_network_exception_redacted_and_released(self):
        guard, owned, events = self.guard()
        def bad():
            raise RuntimeError('private credential MUST NOT escape')
        guard.witness = bad
        with self.assertRaisesRegex(admission.AdmissionError, '^witness_unavailable$'):
            guard.__enter__()
        self.assertTrue(owned.closed)

    def test_readback_requires_exact_instance_full_coverage_and_zero(self):
        for update in ({'gateway_pid':124}, {'gateway_start_id':'later'},
                       {'runtime_manifest_sha256':'c'*64}, {'coverage_complete':False},
                       {'hardware_process_groups':1}, {'guest_control_processes':1},
                       {'owned_descendants':True}, {'secret_argv':'rejected'}):
            guard, owned, _ = self.guard(snapshot=readback(**update))
            with self.assertRaises(admission.AdmissionError):
                guard.__enter__()
            self.assertTrue(owned.closed)

    def test_readback_exception_redacted(self):
        guard, owned, _ = self.guard()
        guard.readback = mock.Mock(side_effect=RuntimeError('private argv'))
        with self.assertRaisesRegex(admission.AdmissionError, '^readback_unavailable$'):
            guard.__enter__()
        self.assertTrue(owned.closed)

    def test_normal_exit_without_launch_releases(self):
        guard, owned, _ = self.guard()
        with guard:
            pass
        self.assertTrue(owned.closed)

    def test_incomplete_shutdown_retains_reservation_until_confirmed(self):
        guard, owned, _ = self.guard()
        with self.assertRaisesRegex(admission.AdmissionError, '^owned_lan_quiescence_required$'):
            with guard:
                guard.mark_owned_lan_starting()
        self.assertFalse(owned.closed)
        self.assertEqual(guard.state, 'cleanup_required')
        for update in ({'quiescence_confirmed':False}, {'stop_failures':1},
                       {'gateway_exit_confirmed':False}, {'owned_media_exit_confirmed':False}):
            with self.assertRaises(admission.AdmissionError):
                guard.confirm_owned_lan_quiescence(quiescence(**update))
            self.assertFalse(owned.closed)
        with self.assertRaises(admission.AdmissionError):
            guard.close()
        guard.confirm_owned_lan_quiescence(quiescence())
        guard.close()
        self.assertTrue(owned.closed)

    def test_media_failure_not_overridden_but_lease_retained(self):
        guard, owned, _ = self.guard()
        try:
            with self.assertRaisesRegex(ValueError, '^owned failure$'):
                with guard:
                    guard.mark_owned_lan_starting()
                    raise ValueError('owned failure')
            self.assertFalse(owned.closed)
        finally:
            guard.confirm_owned_lan_quiescence(quiescence())
            guard.close()

    def test_construction_and_platform_are_fail_closed(self):
        guard, owned, _ = self.guard()
        self.assertEqual(guard.state, 'new')
        with mock.patch.object(admission.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(admission.AdmissionError, '^darwin_physical_binding_required$'):
                guard.__enter__()
        self.assertFalse(owned.closed)  # Factory was never called.
        with self.assertRaises(ValueError):
            admission.ExpectedGateway(True, 'start', 'a'*64, 'b'*64)


@unittest.skipUnless(sys.platform == 'darwin', 'actual Darwin IP_BOUND_IF fixture')
class KernelLoopbackTests(unittest.TestCase):
    def test_owned_ephemeral_binding_primitive_is_exclusive_until_close(self):
        owned = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            endpoint = admission._bind_exclusive_loopback(owned, ('127.0.0.1', 0))
            self.assertEqual(endpoint, owned.getsockname()[:2])
            self.assertGreater(endpoint[1], 0)
            self.assertEqual(owned.getsockopt(socket.IPPROTO_IP, 25), socket.if_nametoindex('lo0'))
            rival = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                rival.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex('lo0'))
                with self.assertRaises(OSError):
                    rival.bind(endpoint)
            finally:
                rival.close()
            owned.close()
            successor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                successor.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex('lo0'))
                successor.bind(endpoint)
            finally:
                successor.close()
        finally:
            owned.close()


if __name__ == '__main__':
    unittest.main()
