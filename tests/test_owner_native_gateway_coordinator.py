"""Fixed native entries plus actual host child lifecycle, inert admission/phone.

Host programs stand in for gateway/ADB; no listener, TLS, media, RPC or phone.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import owner_lan_admission as admission
from scripts.probes import owner_native_gateway_lifecycle as lifecycle
from scripts.probes import owner_native_gateway_coordinator as coordinator
from tests.test_owner_native_window import selection
from tests.test_owner_lan_admission import InertSocket


FOOTER=dict(event='candidate_shutdown',quiescence_confirmed=True,stop_failures=0,owned_reaper_exit_confirmed=True)
LISTEN=dict(event='listening',host='192.168.9.128',https_port=45560,udp_port=45963,
    scope='isolated_same_subnet_LAN_not_WAN',network_scope='lan',inner_interface='en7',
    capture_trace_enabled=False,owner_raw_queue_policy_requested='fifo',
    owner_raw_submit_fps_requested=None,owner_enobufs_retry_enabled=False)
SELECT=dict(event='owner_native_window_selection',diagnostic_events_requested=True,
    descriptor_seconds_ceiling=30,process_seconds_ceiling=300,operator_or_server_lease_established_by_this_record=False)


class CoordinatorChecks(unittest.TestCase):
    def setUp(self):
        self.platform=patch.object(admission.sys,'platform','darwin');self.index=patch.object(admission.socket,'if_nametoindex',return_value=1)
        self.platform.start();self.index.start();self.addCleanup(self.platform.stop);self.addCleanup(self.index.stop)
        self.items=[]
        self.scope=tempfile.TemporaryDirectory();self.addCleanup(self.scope.cleanup)
        self.marker=Path(self.scope.name)/"driver-owned-ready"

    def tearDown(self):
        for item,sock in self.items:
            for child in (item.driver,item.gateway.process):
                if child is not None:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=2)
            for drain in item.driver_drains+item.gateway.drains:drain.thread.join(timeout=2)
            if not sock.closed:sock.close()  # inert fixture socket only, not guard release.

    def make(self,qualify=None,phone_idle=None):
        events=[];sock=InertSocket(events)
        expected=admission.ExpectedGateway(123,'inert','a'*64,'b'*64)
        original=dict(vars(expected),coverage_complete=True,owned_descendants=0,hardware_process_groups=0,packetizer_processes=0,guest_control_processes=0)
        guard=admission.OwnerLanAdmission(expected,lambda:dict(status=503,error='udp_worker_unavailable'),lambda:original,_socket_factory=lambda *a:sock)
        def roles(child):return dict(gateway_pid=child.pid,coverage_complete=True,owned_descendants=0,hardware_process_groups=0,packetizer_processes=0,guest_control_processes=0)
        gateway=lifecycle.OwnedGateway(selection(),guard,lambda:True,roles)
        entries=coordinator.Entries(Path(__file__).resolve().parents[1],Path(sys.executable),Path('/inert/runtime'),Path('/inert/private'))
        def fresh(phase):events.append(('qualified',phase));return True
        def idle():events.append(('phone_idle',));return True
        item=coordinator.Coordinator(gateway,entries,qualify or fresh,phone_idle or idle)
        self.items.append((item,sock));return item,sock,events

    def fixture_argv(self,kind,gateway=None,driver=None):
        if kind=='gateway':
            program=gateway or ('import os,time,sys; print('+repr(json.dumps(LISTEN))+',flush=True);print('+repr(json.dumps(SELECT))+',flush=True)\nend=time.monotonic()+5\nwhile not os.path.exists('+repr(str(self.marker))+') and time.monotonic()<end:time.sleep(.005)\nif not os.path.exists('+repr(str(self.marker))+'):sys.exit(2)\nprint('+repr(json.dumps(FOOTER))+',flush=True)')
        else:program='from pathlib import Path;Path('+repr(str(self.marker))+').write_text("owned_driver_started");'+(driver or 'print("{}",flush=True)')
        return (sys.executable,'-u','-c',program)

    def test_entries_inert_exact_Window_plan_module_paths_and_no_secret_or_switch(self):
        item,sock,events=self.make()
        with patch.object(subprocess,'Popen') as popen:
            for kind in ('gateway','driver'):
                argv=item.entries.argv(item.gateway.window,kind)
                program=argv[argv.index('-c')+1]
                module=ast.parse(program);window=module.body[-2].value
                self.assertEqual(ast.literal_eval(window.args[0].keywords[0].value),vars(item.gateway.window.plan))
                self.assertEqual(ast.literal_eval(window.args[1]),5)
                self.assertNotIn('password',program);self.assertNotIn('--owner-native',argv)
                self.assertIn('-I',argv)
                self.assertEqual(argv[0],sys.executable)
                self.assertNotIn('--capture-trace-dir',argv)
            popen.assert_not_called()
        self.assertEqual(events,[]);self.assertFalse(sock.closed)
        with self.assertRaises(ValueError):item.entries.argv(item.gateway.window,'public')
        with self.assertRaises(ValueError):coordinator.Entries(Path('relative'),Path('/p'),Path('/r'),Path('/e'))
        with self.assertRaises(AttributeError):item.driver=123

    def test_closed_readiness_strict_duplicate_foreign_and_prefix_only(self):
        raw=(json.dumps(LISTEN)+'\n'+json.dumps(SELECT)+'\n').encode()
        self.assertTrue(coordinator._ready(raw+b'partial',selection(),'en7'))
        self.assertFalse(coordinator._ready(json.dumps(LISTEN).encode()+b'\n',selection(),'en7'))
        for bad in (raw+json.dumps(SELECT).encode()+b'\n',b'[]\n',b'{"event":"a","event":"b"}\n',
                (json.dumps(dict(LISTEN,udp_port=True))+'\n'+json.dumps(SELECT)+'\n').encode()):
            with self.assertRaises(lifecycle.LifecycleError):coordinator._ready(bad,selection(),'en7')

    def test_actual_two_Popens_readiness_fresh_qualification_and_release_order(self):
        item,sock,events=self.make()
        with patch.object(coordinator.Entries,'argv',side_effect=lambda w,k:self.fixture_argv(k)):
            item.start(env=dict(os.environ));item.start_driver(env=dict(os.environ))
        self.assertEqual(item.wait_driver(2),0);self.assertEqual(item.gateway.wait(2),0)
        result=item.finish();self.assertTrue(result['driver_success']);self.assertTrue(result['reservation_release_confirmed'])
        self.assertFalse(result['observed_ART_native_report_or_performance_accepted'])
        self.assertEqual(events[-2:],[('phone_idle',),('close',)])
        self.assertIn(('qualified','before_driver'),events)
        self.assertEqual(item.driver.args,self.fixture_argv('driver'))
        with self.assertRaises(lifecycle.LifecycleError):item.start_driver(env=dict(os.environ))

    def test_before_start_qualification_decline_no_lease_Popen_or_inherited_environment(self):
        item,sock,_=self.make(qualify=lambda phase:False)
        with self.assertRaisesRegex(lifecycle.LifecycleError,'fresh_qualification'):item.start(env=dict(os.environ))
        self.assertIsNone(item.gateway.process);self.assertFalse(sock.closed)
        with self.assertRaises(lifecycle.LifecycleError):item.start_driver(env=dict(os.environ))
        other,_,_=self.make()
        with self.assertRaises(ValueError):other.start(env=None)
        self.assertIsNone(other.gateway.process)

    def test_actual_startup_failure_retains_guard_no_driver_even_with_success_footer(self):
        item,sock,_=self.make()
        program='import sys; print('+repr(json.dumps(FOOTER))+',flush=True);sys.exit(2)'
        with patch.object(coordinator.Entries,'argv',side_effect=lambda w,k:self.fixture_argv(k,gateway=program)):
            item.start(env=dict(os.environ));self.assertEqual(item.gateway.wait(2),2)
            with self.assertRaisesRegex(lifecycle.LifecycleError,'gateway_startup_failed'):item.start_driver(env=dict(os.environ))
        self.assertIsNone(item.driver);self.assertFalse(sock.closed)
        with self.assertRaises(lifecycle.LifecycleError):item.finish()

    def test_Popen_creation_failure_reports_retained_supervisor_state(self):
        item,sock,_=self.make()
        with patch.object(lifecycle.subprocess,'Popen',side_effect=OSError('private path')):
            with self.assertRaisesRegex(lifecycle.LifecycleError,'start_or_drain_unverified'):item.start(env=dict(os.environ))
        self.assertEqual(item.state,'cleanup_required');self.assertFalse(sock.closed)
        self.assertIsNone(item.driver)
        with self.assertRaises(lifecycle.LifecycleError):item.start(env=dict(os.environ))

    def test_current_phone_unknown_or_driver_timeout_cannot_release_or_signal_driver(self):
        item,sock,_=self.make(phone_idle=lambda:False)
        with patch.object(coordinator.Entries,'argv',side_effect=lambda w,k:self.fixture_argv(k,driver='import time;time.sleep(.25)')):
            item.start(env=dict(os.environ));item.start_driver(env=dict(os.environ))
        with patch.object(item.driver,'terminate') as term,patch.object(item.driver,'kill') as kill:
            self.assertIsNone(item.wait_driver(.01));term.assert_not_called();kill.assert_not_called()
        self.assertFalse(sock.closed)
        self.assertEqual(item.wait_driver(2),0);self.assertEqual(item.gateway.wait(2),0)
        with self.assertRaisesRegex(lifecycle.LifecycleError,'phone_cleanup_unverified'):item.finish()
        self.assertFalse(sock.closed)
        item.phone_idle=lambda:True
        self.assertTrue(item.finish()['reservation_release_confirmed'])

    def test_driver_failure_separate_from_independent_safe_cleanup_not_performance_success(self):
        item,sock,_=self.make()
        with patch.object(coordinator.Entries,'argv',side_effect=lambda w,k:self.fixture_argv(k,driver='import sys;sys.exit(1)')):
            item.start(env=dict(os.environ));item.start_driver(env=dict(os.environ))
        self.assertEqual(item.wait_driver(2),1);self.assertEqual(item.gateway.wait(2),0)
        result=item.finish();self.assertFalse(result['driver_success']);self.assertTrue(sock.closed)

    def test_fresh_pre_driver_original_or_qualification_failure_retains_started_guard(self):
        item,sock,_=self.make(qualify=lambda phase:phase=='before_gateway')
        with patch.object(coordinator.Entries,'argv',side_effect=lambda w,k:self.fixture_argv(k)):
            item.start(env=dict(os.environ))
            with self.assertRaises(lifecycle.LifecycleError):item.start_driver(env=dict(os.environ))
        self.assertIsNone(item.driver);self.assertFalse(sock.closed)


if __name__=='__main__':unittest.main()
