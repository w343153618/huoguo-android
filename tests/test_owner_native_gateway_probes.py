"""Actual host command bounds; synthetic Mac/guest readbacks, no live service."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import owner_native_gateway_probes as probes
from scripts.probes.owner_lan_admission import ExpectedGateway


class ProbeChecks(unittest.TestCase):
    def test_construct_and_invalid_commands_inert(self):
        with patch.object(probes.subprocess,'Popen') as popen:
            runner=probes.Commands()
            for args in ((),('x\0',),('x',1)):
                with self.assertRaises(ValueError):runner.run(args)
            with self.assertRaises(ValueError):runner.run(('x',),seconds=float('nan'))
            popen.assert_not_called();self.assertEqual(runner.started,0)

    def test_actual_owned_readonly_child_exit_errors_overflow_and_timeout_reaped(self):
        runner=probes.Commands()
        self.assertEqual(runner.run((sys.executable,'-c','print("ok")')),'ok\n')
        for program,kw,label in (
                ('import sys;sys.stderr.write("private secret")',{},'command_unverified'),
                ('print("x"*5000)',{'bound':1024},'output_bound'),
                ('import time;time.sleep(2)',{'seconds':.05},'command_timeout')):
            with self.assertRaisesRegex(probes.ProbeError,'^native_probe_'+label+'$'):
                runner.run((sys.executable,'-c',program),**kw)
        self.assertEqual(runner.started,runner.reaped);self.assertEqual(runner.pending,[])
        self.assertLessEqual(runner.kill_calls,runner.terminate_calls)

    def test_inventory_closed_and_roles_conservative_including_reparented_known_roles(self):
        raw='10 1 10 /python /owned/gateway.py\n11 10 11 /python /owned/worker.py\n12 11 12 /python /owned/hardware_stream.py --serial emulator-5556\n13 1 13 /owned/session-pool-encoder\n14 1 14 /owned/h264_udp_packetizer\n'
        value=probes.roles(probes.inventory(raw),10,'ARGS\ninit\napp_process com.genymobile.scrcpy.Server 1\n')
        self.assertEqual(value,dict(owned_descendants=2,hardware_process_groups=2,packetizer_processes=1,guest_control_processes=1))
        for raw in ('','broken','1 0 1 x\n1 0 1 y','1 0 1 "unclosed'):
            with self.assertRaises(probes.ProbeError):probes.inventory(raw)
        for guest in ('','ARGS','PID NAME\n1 init'):
            with self.assertRaises(probes.ProbeError):probes.roles({},10,guest)

    def test_exact_original_full_hash_and_start_bracket_no_git_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'runtime with spaces';root.mkdir();hashes=[]
            for name in probes.RUNTIME_NAMES:
                file=root/name;file.parent.mkdir(parents=True,exist_ok=True)
                if name==probes.RUNTIME_NAMES[-1]:
                    raw=json.dumps(dict(touch_cancel_clears_pointer_state=True,guest_jar_sha256=dict(hashes)['hardware/scrcpy-audio-control'])).encode()
                else:raw=name.encode()
                file.write_bytes(raw);hashes.append((name,hashlib.sha256(raw).hexdigest()))
            expected=ExpectedGateway(4321,'Sun_Oct_4_01:01:01_2026',hashes[0][1],hashlib.sha256(json.dumps(hashes,separators=(',',':')).encode()).hexdigest())
            class Fake:
                started=reaped=0;pending=[]
                def run(self,argv,**kw):
                    self.started+=1;self.reaped+=1
                    if 'lstart=' in argv:return 'Sun Oct 4 01:01:01 2026\n'
                    if '-axo' in argv:return '4321 1 4321 /python '+str(root/probes.RUNTIME_NAMES[0])+'\n'
                    return 'ARGS\ninit\n'
            reader=probes.MacProbes(expected,root,Path('/inert/adb'),Fake())
            value=reader.original_readback();self.assertTrue(value['coverage_complete']);self.assertEqual(value['owned_descendants'],0)
            (root/probes.RUNTIME_NAMES[1]).write_bytes(b'changed')
            with self.assertRaisesRegex(probes.ProbeError,'runtime_changed'):reader.original_readback()

    def test_formal_snapshot_busy_or_unknown_is_not_permission(self):
        class Fake:
            def run(self,argv,**kw):return '123\n' if '-iTCP:15558' in argv else ''
        reader=probes.MacProbes(ExpectedGateway(123,'inert','a'*64,'b'*64),Path('/inert/original'),Path('/inert/adb'),Fake())
        self.assertFalse(reader.formal_clear())
        with patch.object(reader.commands,'run',side_effect=probes.ProbeError('native_probe_command_unverified')):
            with self.assertRaises(probes.ProbeError):reader.formal_clear()

    def test_actual_exited_Popen_with_PID_reuse_or_active_roles_refused(self):
        runner=probes.Commands()
        # This actual child is owned by this fixture, never a ps-selected PID.
        child=subprocess.Popen((sys.executable,'-c','pass'));child.wait(timeout=2)
        reader=probes.MacProbes(ExpectedGateway(123,'inert','a'*64,'b'*64),Path('/inert/original'),Path('/inert/adb'),runner)
        with patch.object(reader,'_inventory',return_value=({child.pid:(1,child.pid,('foreign',),'foreign')},'ARGS\ninit\n')):
            with self.assertRaisesRegex(probes.ProbeError,'PID_present_or_reused'):reader.owned_roles(child)
        with patch.object(reader,'_inventory',return_value=({},'ARGS\ninit\n')):
            self.assertEqual(reader.owned_roles(child)['gateway_pid'],child.pid)
        with self.assertRaises(probes.ProbeError):reader.owned_roles(child.pid)


if __name__=='__main__':unittest.main()
