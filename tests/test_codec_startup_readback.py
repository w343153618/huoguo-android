"""Bounded experimental execution readback; no devices or source credentials."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('startup_driver',ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(DRIVER)

class CodecStartupReadbackCheck(unittest.TestCase):
    def value(self,enabled):
        result={'requested_codec_startup_ready_enabled':enabled}
        for stage in ('first','second'):
            result[stage+'_codec_startup_ready_enabled']=int(enabled)
            result[stage+'_codec_startup_readback_verified']=True
            result[stage+'_codec_startup_gate']=dict(enabled=int(enabled),phase_before_close=5 if enabled else 0,
                failure_code=0,ready_ns=100 if enabled else 0,fresh_received_ns=110 if enabled else 0,
                committed_ns=120 if enabled else 0,bootstrap_pts_us=7 if enabled else -1,fresh_pts_us=9 if enabled else -1)
        return result

    def test_both_sessions_require_execution_not_just_requested_option(self):
        for enabled in (False,True):
            value=self.value(enabled);self.assertTrue(DRIVER.verify_codec_startup_readback(value,enabled))
            for stage in ('first','second'):
                for key in ('codec_startup_ready_enabled','codec_startup_readback_verified','codec_startup_gate'):
                    bad=copy.deepcopy(value);bad.pop(stage+'_'+key)
                    self.assertFalse(DRIVER.verify_codec_startup_readback(bad,enabled))
            bad=copy.deepcopy(value);bad['failure_class']='IOException'
            self.assertFalse(DRIVER.verify_codec_startup_readback(bad,enabled))
        self.assertFalse(DRIVER.verify_codec_startup_readback(self.value(True),1))

    def test_numeric_phase_order_failure_and_old_pts_are_rejected(self):
        for key,bad_value in [('phase_before_close',3),('failure_code',2),('ready_ns',0),
                ('fresh_received_ns',99),('committed_ns',109),('fresh_pts_us',7),('enabled',True),('ready_ns',100.0)]:
            bad=self.value(True);bad['second_codec_startup_gate'][key]=bad_value
            self.assertFalse(DRIVER.verify_codec_startup_readback(bad,True),key)
        bad=self.value(False);bad['first_codec_startup_gate']['committed_ns']=1
        self.assertFalse(DRIVER.verify_codec_startup_readback(bad,False))

    def test_explicit_control_not_saved_or_sent_as_server_option(self):
        source=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        self.assertIn('codecStartup.setChecked(false)',source)
        self.assertIn('attempt.codecStartupReadyEnabled',source)
        self.assertNotIn('putBoolean("codec_startup',source)
        self.assertNotIn('request.put("codec_startup',source)
        contract=(ROOT/'app/src/udp/java/local/remoteandroid/direct/LanUdpContract.java').read_text()
        self.assertIn('HTTPS_PORT=45560,UDP_PORT=45963',contract)

if __name__=='__main__':unittest.main()
