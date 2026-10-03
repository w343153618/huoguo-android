"""Experiment-only sampling switch; owned source/readback checks, no devices."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('stage_driver',ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(DRIVER)


class StageOptinCheck(unittest.TestCase):
    @staticmethod
    def valid(enabled):
        value={'requested_stage_diagnostics_enabled':enabled}
        for stage in ('first','second'):
            value[stage+'_stage_diagnostics_enabled']=int(enabled)
            value[stage+'_stage_diagnostics_verified']=True
        return value

    def test_both_sessions_exact_execution_readback_required(self):
        for enabled in (False,True):
            value=self.valid(enabled)
            self.assertTrue(DRIVER.verify_stage_diagnostics_readback(value,enabled))
            for field in ('first_stage_diagnostics_enabled','second_stage_diagnostics_enabled'):
                for bad in (None,str(int(enabled)),enabled,1.0 if enabled else 0.0,int(not enabled)):
                    changed=dict(value);changed[field]=bad
                    self.assertFalse(DRIVER.verify_stage_diagnostics_readback(changed,enabled))
            changed=dict(value);changed['failure_class']='Exception'
            self.assertFalse(DRIVER.verify_stage_diagnostics_readback(changed,enabled))
        self.assertFalse(DRIVER.verify_stage_diagnostics_readback(self.valid(True),1))

    def test_local_only_switch_frozen_reset_and_not_persisted(self):
        ui=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        self.assertIn('private boolean ownerStageDiagnosticsEnabled=true',ui)
        self.assertIn('ownerStageDiagnosticsEnabled=true;',ui.split('@Override public void showLogin()',1)[1])
        self.assertIn('stageDiagnosticsEnabled=stages',ui)
        self.assertIn('attempt.stageDiagnosticsEnabled',ui)
        self.assertNotIn('putBoolean("stage_diagnostics',ui)
        self.assertNotIn('request.put("stage_diagnostics',ui)
        main=(ROOT/'app/src/main/java/local/remoteandroid/direct/MainActivity.java').read_text()
        self.assertNotIn('ownerStageDiagnosticsEnabled',main)
        helper=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        self.assertIn('stageDiagnostics()!=report.has("decoder_stage_metrics")',helper)

    def test_static_surface_and_short_prefix_cannot_pass_continuous_media_progress(self):
        value=dict(steady_progress_monitor_enabled=True,steady_media_progress_healthy=True,
            steady_progress_max_idle_ns=250000000,steady_progress_stall_threshold_ns=3000000000,
            steady_progress_samples=[dict(phone_ns=1000000000+i*1000000000,
                worker_received_frames=100+i*60,codec_callback_count=90+i*60) for i in range(33)])
        self.assertTrue(DRIVER.verify_steady_media_progress(value))
        for key,bad in [('steady_media_progress_healthy',False),('steady_progress_max_idle_ns',3000000000),
                ('steady_progress_max_idle_ns',True),('steady_progress_samples',value['steady_progress_samples'][:4])]:
            changed=dict(value);changed[key]=bad
            self.assertFalse(DRIVER.verify_steady_media_progress(changed))
        changed=dict(value);changed['steady_progress_samples']=[dict(row,worker_received_frames=100,
            codec_callback_count=90) for row in value['steady_progress_samples']]
        self.assertFalse(DRIVER.verify_steady_media_progress(changed))


if __name__=='__main__':unittest.main()
