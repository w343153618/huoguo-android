"""Actual bounded host Python children; inert admission/TLS/device callbacks.

No production port, certificate, account, gateway, emulator, phone or media.
Synthetic footer/role callbacks never become live native ownership evidence.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from scripts.probes import owner_lan_admission as admission
from scripts.probes import owner_native_gateway_lifecycle as lifecycle
from tests.test_owner_native_window import selection
from tests.test_owner_lan_admission import InertSocket


FOOTER = dict(event='candidate_shutdown', quiescence_confirmed=True,
              stop_failures=0, owned_reaper_exit_confirmed=True)


class NativeOwnedLifecycleChecks(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(admission.sys, 'platform', 'darwin')
        self.index = patch.object(admission.socket, 'if_nametoindex', return_value=1)
        self.platform.start(); self.index.start()
        self.addCleanup(self.platform.stop); self.addCleanup(self.index.stop)
        self.started = []

    def tearDown(self):
        # Only Popen objects constructed by this fixture; never PID/group scans.
        for owned in self.started:
            if owned.process is not None:
                if owned.process.poll() is None:
                    owned.terminate_owned()
                owned.process.wait(timeout=3)
                for drain in owned.drains:
                    drain.thread.join(timeout=3)
            # Fake socket only; rejected finish keeps the actual guard schema.
            if owned.admission._socket is not None:
                owned.admission._socket.close()

    def make(self, formal=True, roles=None, snapshot=None):
        events=[]; sock=InertSocket(events)
        expected=admission.ExpectedGateway(123,'inert-start','a'*64,'b'*64)
        original=dict(vars(expected),coverage_complete=True,owned_descendants=0,
            hardware_process_groups=0,packetizer_processes=0,guest_control_processes=0)
        def witness():events.append(('witness',));return {'status':503,'error':'udp_worker_unavailable'}
        def readback():events.append(('original',));return copy.deepcopy(original if snapshot is None else snapshot)
        guard=admission.OwnerLanAdmission(expected,witness,readback,_socket_factory=lambda *a:sock)
        def formal_clear():events.append(('formal',));return formal
        def owned_roles(proc):
            events.append(('owned_roles',proc.pid))
            return dict(gateway_pid=proc.pid,coverage_complete=True,owned_descendants=0,
                hardware_process_groups=0,packetizer_processes=0,guest_control_processes=0) if roles is None else roles(proc)
        owned=lifecycle.OwnedGateway(selection(),guard,formal_clear,owned_roles)
        self.started.append(owned)
        return owned,sock,events,original

    def launch(self, owned, program=None):
        program = 'print('+repr(json.dumps(FOOTER))+',flush=True)' if program is None else program
        return owned.start((sys.executable,'-u','-c',program),cwd=Path(__file__).resolve().parents[1])

    def test_construction_and_bad_launch_are_inert_no_process_or_bind(self):
        owned,sock,events,_=self.make()
        self.assertEqual(events,[]);self.assertIsNone(owned.process)
        with self.assertRaises(AttributeError):owned.process=123
        for command in ([],(),('x\0',),('a'*8193,),('x',1)):
            with self.assertRaises(ValueError):owned.start(command,cwd=Path('/inert'))
        with self.assertRaises(ValueError):owned.start(('x',),cwd=Path('relative'))
        with self.assertRaises(ValueError):owned.start(('x',),cwd=Path('/inert'),env={'x':True})
        self.assertEqual(events,[]);self.assertFalse(sock.closed)

    def test_actual_child_natural_exit_mark_before_Popen_full_post_before_release(self):
        owned,sock,events,_=self.make()
        actual=subprocess.Popen
        def spawn(*args,**kwargs):
            self.assertEqual(owned.admission.state,'lan_starting')
            self.assertFalse(sock.closed);events.append(('actual_Popen',))
            self.assertFalse(kwargs['shell']);self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
            return actual(*args,**kwargs)
        with patch.object(lifecycle.subprocess,'Popen',side_effect=spawn):pid=self.launch(owned)
        self.assertIsInstance(owned.process,actual)
        self.assertEqual(owned.wait(3),0)
        result=owned.finish()
        self.assertTrue(result['reservation_release_confirmed'])
        self.assertEqual((result['gateway_pid'],result['gateway_exit_observed']),(pid,0))
        self.assertTrue(result['exit_zero_no_parent_terminate_call'])
        self.assertFalse(result['phone_ART_native_producer_or_full_pipeline_acceptance'])
        self.assertEqual(result['parent_gateway_terminate_calls'],0)
        self.assertEqual(events[-1],('close',))
        self.assertLess(events.index(('witness',)),events.index(('actual_Popen',)))
        self.assertEqual(events[-4:-1],[('owned_roles',pid),('original',),('formal',)])
        self.assertTrue(sock.closed);self.assertEqual(owned.admission.state,'released')

    def test_busy_formal_refuses_before_mark_or_child_and_closes_unstarted_lease(self):
        owned,sock,events,_=self.make(formal=False)
        with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_formal_unverified$'):
            self.launch(owned)
        self.assertIsNone(owned.process);self.assertTrue(sock.closed)
        self.assertEqual(owned.admission.state,'released')

    def test_Popen_creation_failure_retains_marked_lease_no_false_exit(self):
        owned,sock,_,_=self.make()
        with patch.object(lifecycle.subprocess,'Popen',side_effect=OSError('private path MUST NOT escape')):
            with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_start_or_drain_unverified$'):
                self.launch(owned)
        self.assertFalse(sock.closed);self.assertEqual(owned.admission.state,'lan_starting')
        with self.assertRaises(admission.AdmissionError):owned.admission.close()

    def test_wait_timeout_keeps_child_and_reserve_then_only_owned_terminate(self):
        owned,sock,_,_=self.make()
        self.launch(owned,'import signal,time,sys; signal.signal(signal.SIGTERM,lambda *a:(print('+repr(json.dumps(FOOTER))+',flush=True),sys.exit(0))); print("{}",flush=True); time.sleep(2)')
        # Child output is emitted after its handler is installed. Fixed sleep
        # alone is not a readiness event on a loaded CI runner.
        deadline=time.monotonic()+2
        while b'{}\n' not in bytes(owned.drains[0].data) and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertIn(b'{}\n',bytes(owned.drains[0].data))
        self.assertIsNone(owned.wait(.01));self.assertFalse(sock.closed)
        self.assertTrue(owned.terminate_owned());self.assertEqual(owned.wait(3),0)
        result=owned.finish();self.assertEqual(result['parent_gateway_terminate_calls'],1)
        self.assertFalse(result['exit_zero_no_parent_terminate_call'])
        with self.assertRaises(lifecycle.LifecycleError):owned.terminate_owned()

    def test_nonzero_exit_or_running_process_cannot_promote_a_good_footer(self):
        for code in (1,2):
            owned,sock,_,_=self.make()
            self.launch(owned,'import sys; print('+repr(json.dumps(FOOTER))+',flush=True); sys.exit('+str(code)+')')
            self.assertEqual(owned.wait(3),code)
            with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_exit_unverified$'):owned.finish()
            self.assertFalse(sock.closed)
        owned,sock,_,_=self.make();self.launch(owned,'import time; time.sleep(.2)')
        with self.assertRaises(lifecycle.LifecycleError):owned.finish()
        self.assertFalse(sock.closed)

    def test_actual_ignored_TERM_owned_KILL_reaps_but_never_releases(self):
        owned,sock,_,_=self.make()
        self.launch(owned,'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("{}",flush=True); time.sleep(5)')
        end=time.monotonic()+2
        while b'{}\n' not in bytes(owned.drains[0].data) and time.monotonic()<end:time.sleep(.005)
        self.assertIn(b'{}\n',bytes(owned.drains[0].data))
        self.assertTrue(owned.terminate_owned());self.assertIsNone(owned.wait(.03))
        self.assertTrue(owned.kill_owned());self.assertLess(owned.wait(2),0)
        self.assertEqual(owned.kill_calls,1)
        with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_exit_unverified$'):owned.finish()
        self.assertFalse(sock.closed);self.assertEqual(owned.state,'cleanup_required')
        # A forged successful footer or return code cannot bless forced exit.
        with patch.object(owned.process,'poll',return_value=0):
            with self.assertRaises(lifecycle.LifecycleError):owned.finish()
        fresh,other,_,_=self.make()
        with self.assertRaises(lifecycle.LifecycleError):fresh.kill_owned()
        self.assertFalse(other.closed)

    def test_closed_unique_footer_rejects_foreign_duplicate_boolean_or_unknown(self):
        cases=[b'',json.dumps(FOOTER).encode()+b'\n'+json.dumps(FOOTER).encode(),
            b'{"event":"candidate_shutdown","event":"candidate_shutdown"}',b'not JSON',b'[]',
            json.dumps(dict(FOOTER,stop_failures=True)).encode(),
            json.dumps(dict(FOOTER,stop_failures=1)).encode(),
            json.dumps(dict(FOOTER,owned_reaper_exit_confirmed=False)).encode(),
            json.dumps(dict(FOOTER,gateway_exit_confirmed=True)).encode(),
            b'{"event":NaN}',b'x'*(lifecycle.OUTPUT_BYTES+1)]
        for raw in cases:
            with self.subTest(raw=raw[:30]),self.assertRaises(lifecycle.LifecycleError):lifecycle.native_shutdown(raw)
        self.assertEqual(lifecycle.native_shutdown(b'{}\n'+json.dumps(FOOTER).encode()),FOOTER)

    def test_actual_child_footer_unknown_output_overflow_or_unjoined_drain_retains_lease(self):
        for program in ('print("{}")', 'print('+repr(json.dumps(dict(FOOTER,owned_reaper_exit_confirmed=False)))+')',
                'import sys; sys.stdout.write("x"*70000); print('+repr(json.dumps(FOOTER))+')'):
            owned,sock,events,_=self.make();self.launch(owned,program);self.assertEqual(owned.wait(3),0)
            with self.assertRaises(lifecycle.LifecycleError):owned.finish()
            self.assertFalse(sock.closed);self.assertFalse(any(e[0]=='owned_roles' for e in events))
            self.assertLessEqual(len(owned.drains[0].data),lifecycle.OUTPUT_BYTES)
        owned,sock,_,_=self.make();self.launch(owned);self.assertEqual(owned.wait(3),0)
        with patch.object(owned.drains[0].thread,'is_alive',return_value=True),self.assertRaises(lifecycle.LifecycleError):owned.finish()
        self.assertFalse(sock.closed)
        self.assertTrue(owned.finish()['reservation_release_confirmed'])

    def test_full_owned_roles_same_actual_Popen_and_fresh_original_required(self):
        for update in ({'gateway_pid':987654},{'coverage_complete':False},{'owned_descendants':True},
                       {'hardware_process_groups':1},{'foreign':0}):
            def roles(proc):return dict(dict(gateway_pid=proc.pid,coverage_complete=True,owned_descendants=0,
                hardware_process_groups=0,packetizer_processes=0,guest_control_processes=0),**update)
            owned,sock,_,_=self.make(roles=roles);self.launch(owned);self.assertEqual(owned.wait(3),0)
            with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_postcheck_unverified$'):owned.finish()
            self.assertFalse(sock.closed)
        owned,sock,_,original=self.make();self.launch(owned);self.assertEqual(owned.wait(3),0)
        owned.admission.readback=lambda:dict(original,gateway_start_id='later-original')
        with self.assertRaises(lifecycle.LifecycleError):owned.finish()
        self.assertFalse(sock.closed)
        owned.admission.readback=lambda:original
        owned.owned_roles=lambda proc:(_ for _ in ()).throw(RuntimeError('private scan data'))
        with self.assertRaisesRegex(lifecycle.LifecycleError,'^native_gateway_postcheck_unverified$'):owned.finish()

    def test_release_error_cannot_return_PASS_and_does_not_adopt_external_PID(self):
        owned,sock,_,_=self.make();self.launch(owned);self.assertEqual(owned.wait(3),0)
        actual_close=sock.close
        def failed_close():raise RuntimeError('private metadata')
        sock.close=failed_close
        with self.assertRaisesRegex(admission.AdmissionError,'^reservation_release_unconfirmed$'):owned.finish()
        self.assertFalse(sock.closed);self.assertEqual(owned.state,'cleanup_required')
        sock.close=actual_close
        # Guard is quiescent after confirmation, so release may be retried only
        # by the trusted caller after fresh postchecks; finish is not a PID API.
        self.assertIsNotNone(owned.admission._socket)
        self.assertTrue(owned.finish()['reservation_release_confirmed'])
        self.assertTrue(sock.closed)
        untouched,other,_,_=self.make()
        with self.assertRaises(lifecycle.LifecycleError):untouched.finish()
        self.assertFalse(other.closed)


if __name__=='__main__':unittest.main()
