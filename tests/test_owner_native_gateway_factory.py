"""Trusted finite gateway wiring; inert workers/TLS, no listener/device/secret."""
import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import udp_lan_gateway as gateway
from udp_lan_sessions import UdpLanSessions, SessionError
from scripts.probes.owner_native_window import gateway_plan
from tests.test_owner_native_window import selection
from tests.test_owner_native_diagnostic_policy import request


def options(**changes):
    value=dict(network_scope='lan',host='192.168.9.128',interface='en7',https_port=45560,
        udp_port=45963,max_runtime=300,allow_owner_surface_submit_lead=False,
        allow_owner_enobufs_retry=False,owner_raw_queue_policy='fifo',owner_raw_submit_fps=None,
        capture_trace_dir=None,runtime=Path('/inert/runtime'),packetizer=Path('/inert/packetizer'),
        native_encoder=Path('/inert/encoder'),evidence_dir=Path('/inert/evidence'))
    return SimpleNamespace(**dict(value,**changes))


class Worker:
    def __init__(self,*args,**kwargs):self.args=args;self.kwargs=kwargs;self.stops=0
    def start(self):pass
    def stop(self):self.stops+=1


class NativeGatewayFactoryChecks(unittest.TestCase):
    def test_default_constructor_does_not_select_events_or_bind_an_owner_account(self):
        args=options(max_runtime=600)
        registry,factory=gateway.registry_and_factory(args,args.host,gateway.LanScope(args.host),worker_type=Worker)
        made=[]
        descriptor=registry.create('inert-ordinary-account',{},lambda c:made.append(factory(c,'192.168.9.149')) or made[-1])
        self.assertFalse(descriptor['diagnostic_events'])
        self.assertIsNone(made[0].kwargs['owner_native_diagnostic_plan'])
        self.assertNotIn('guest_serial',made[0].kwargs)
        registry.cancel('inert-ordinary-account',descriptor['session'])
        self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def test_exact_same_trusted_plan_reaches_actual_registry_and_owned_factory(self):
        chosen=selection();args=options();made=[]
        registry,factory=gateway.registry_and_factory(args,args.host,gateway.LanScope(args.host),
            owner_native_window=chosen,worker_type=Worker)
        descriptor=registry.create('huoguo',request(),lambda c:made.append(factory(c,'192.168.9.149')) or made[-1])
        self.assertTrue(descriptor['diagnostic_events']);self.assertEqual(descriptor['seconds'],30)
        self.assertIs(made[0].kwargs['owner_native_diagnostic_plan'],chosen.plan)
        self.assertEqual((made[0].kwargs['guest_serial'],made[0].kwargs['guest_avd']),('emulator-5556','RemoteAndroid17Compare'))
        self.assertEqual(made[0].kwargs['raw_queue_policy'],'fifo');self.assertIsNone(made[0].kwargs['raw_submit_fps'])
        self.assertFalse(made[0].kwargs['enobufs_retry_enabled']);self.assertIsNone(made[0].kwargs['capture_trace_dir'])
        registry.cancel('huoguo',descriptor['session'])
        self.assertEqual(made[0].stops,1);self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def test_wrong_scope_host_ports_lifetime_trace_or_overrides_refuse_before_registry_factory(self):
        chosen=selection()
        for change in ({'network_scope':'tailnet'},{'host':'192.168.9.129'},{'interface':'lo0'},
                {'udp_port':45965},{'https_port':49556},{'max_runtime':600},{'max_runtime':True},
                {'allow_owner_surface_submit_lead':True},{'allow_owner_enobufs_retry':True},
                {'capture_trace_dir':Path('/inert/trace')},{'owner_raw_queue_policy':'latest'},{'owner_raw_submit_fps':60}):
            with self.subTest(change=change),self.assertRaises(ValueError):gateway_plan(options(**change),chosen)
        calls=[]
        def unexpected(*args,**kwargs):calls.append(1);raise AssertionError('created before qualification')
        for host,scope in (('192.168.9.129',SimpleNamespace(name='lan')),
                           ('192.168.9.128',SimpleNamespace(name='tailnet'))):
            with self.assertRaises(ValueError):gateway.registry_and_factory(options(),host,scope,
                owner_native_window=chosen,registry_type=unexpected)
        self.assertEqual(calls,[])

    def test_wrong_account_or_nonmatching_settings_refuse_before_worker(self):
        args=options();calls=[]
        registry,factory=gateway.registry_and_factory(args,args.host,gateway.LanScope(args.host),owner_native_window=selection(),worker_type=lambda *a,**kw:calls.append(1))
        for account,settings in (('inert-foreign',request()),('huoguo',dict(request(),max_fps=60))):
            with self.assertRaises(SessionError):registry.create(account,settings,lambda c:factory(c,'192.168.9.149'))
        self.assertEqual(calls,[]);self.assertTrue(registry.close_and_wait(.01)['quiescence_confirmed'])

    def test_main_has_no_CLI_opt_in_and_rejects_local_mismatched_lifetime_before_TLS(self):
        argv=['probe','--host','192.168.9.128','--interface','en7','--runtime','/inert/runtime',
            '--packetizer','/inert/packetizer','--native-encoder','/inert/encoder','--evidence-dir','/inert/evidence']
        for extra,chosen in ((['--owner-native-window','single'],None),([],selection())):
            with patch.object(gateway.ssl,'SSLContext') as tls,patch.object(gateway.sys if hasattr(gateway,'sys') else __import__('sys'),'argv',argv+extra),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                gateway.main(owner_native_window=chosen)
            tls.assert_not_called()

    def finite_main(self,still_alive=False):
        argv=['probe','--host','192.168.9.128','--interface','en7','--runtime','/inert/runtime',
            '--packetizer','/inert/packetizer','--native-encoder','/inert/encoder','--evidence-dir','/inert/evidence','--max-runtime','300']
        instances=[]
        class Server:
            def __init__(self,*a,**kw):self.closed=False;instances.append(self)
            def serve_forever(self,poll_interval):pass
            def shutdown(self):pass
            def server_close(self):self.closed=True
        output=io.StringIO()
        with patch.object(__import__('sys'),'argv',argv),patch.dict('os.environ',{'DIRECT_AUTH_FILE':'inert-auth-not-read'}),\
                patch.object(gateway,'physical_lan_address',return_value='192.168.9.128'),\
                patch.object(gateway.ssl,'SSLContext'),patch.object(gateway,'BoundedTlsServer',Server),\
                patch.object(gateway.signal,'signal'),contextlib.redirect_stdout(output):
            if still_alive:
                with patch.object(gateway.threading.Thread,'is_alive',return_value=True),self.assertRaises(SystemExit) as closed:
                    gateway.main(owner_native_window=selection())
                self.assertEqual(closed.exception.code,1)
            else:gateway.main(owner_native_window=selection())
        records=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(instances[0].closed)
        return records

    def test_actual_finite_main_flow_with_fake_TLS_and_no_socket_closes_real_registry_and_reaper(self):
        records=self.finite_main()
        self.assertEqual(records[-1],{'event':'candidate_shutdown','quiescence_confirmed':True,'stop_failures':0,'owned_reaper_exit_confirmed':True})
        self.assertEqual(records[-2]['event'],'owner_native_window_selection')
        self.assertFalse(records[-2]['operator_or_server_lease_established_by_this_record'])

    def test_unconfirmed_reaper_makes_unique_shutdown_false_and_process_nonzero(self):
        records=self.finite_main(still_alive=True)
        self.assertFalse(records[-1]['quiescence_confirmed'])
        self.assertFalse(records[-1]['owned_reaper_exit_confirmed'])
        self.assertEqual(records[-1]['reason'],'udp_native_reaper_exit_unconfirmed')


if __name__=='__main__':unittest.main()
