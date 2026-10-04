import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.probes import run_authenticated_lan_ui as driver
from scripts.probes import source_authenticated_driver as source


def recovery():
    row = {'requested_authenticated_source_input': True, 'helper_owned_attempt_started': True,
        'source_pause_only_recovery': True, 'source_recovery_no_steady_window': True,
        'source_recovery_no_reconnect': True, 'source_phase_1_external_observer_confirmation': True,
        'requested_credential_source': 'saved-ui', 'saved_UI_private_input_touched': False,
        'saved_UI_secret_exported': False, 'requested_credential_save_acceptance': False,
        'requested_v50_profile': True, 'requested_network_scope': 'nps_owner', 'requested_node': 'm1',
        'requested_surface_submit_lead_ms': 0, 'requested_stage_diagnostics_enabled': False,
        'requested_codec_startup_ready_enabled': False, 'first_leave_audio_threads_alive': 0,
        'source_phase_1_local_tap': {'down_attempted': True, 'down_returned': True,
            'up_attempted': True, 'up_returned': True, 'cancel_attempted': False,
            'cancel_returned': False, 'cancel_skipped_for_changed_owner': False,
            'remote_playback_verified_by_helper': False}}
    for key in ('saved_UI_route_verified', 'saved_UI_allowed_account_verified',
                'saved_UI_nonempty_credential_verified', 'saved_UI_normal_restore_used',
                'video_profile_readback_verified', 'v50_profile_button_clicked',
                'exit_dialog_shown', 'exit_repeated_back_same_dialog', 'exit_continue_preserved_attempt',
                'exit_continue_media_progress', 'exit_positive_button_clicked', 'exit_captured_attempt_cancelled',
                'exit_used_actual_UI_buttons', 'exit_UI_callbacks_observed', 'physical_network_same_lease',
                'network_readback_verified', 'media_transport_is_App_UDP_not_NPC_outer_verification',
                'surface_submit_execution_verified', 'stage_diagnostics_verified', 'codec_startup_readback_verified'):
        row['first_'+key] = True
    row.update(first_actual_fps_limit=30, first_actual_buffer_ms=80, first_actual_video_width=540,
        first_actual_video_height=960, first_v50_profile_button_click_count=1,
        first_video_profile_is_presented_FPS=False, first_physical_network_handle=12,
        first_physical_network_transport=1, first_physical_https_bind_calls=1,
        first_physical_udp_bind_calls=1, first_physical_packet_route_verified=False,
        first_physical_domestic_country_verified=False, first_actual_network_scope='nps_owner',
        first_actual_node='m1', first_media_transport_code=1, first_actual_received_frames=20,
        first_actual_codec_callback_count=15, first_surface_submit_lead_ms=0,
        first_surface_submit_status_code=0, first_surface_submit_wait_count=0,
        first_surface_submit_applications=0, first_stage_diagnostics_enabled=0,
        first_codec_startup_ready_enabled=0)
    profile = driver.planned_profile('m1')
    row.update(first_actual_control_host=profile.public_control.host,
        first_actual_control_port=profile.public_control.port,
        first_actual_media_peer_host=profile.public_media.host,
        first_actual_media_peer_port=profile.public_media.port)
    row['first_codec_startup_gate'] = dict.fromkeys(('enabled', 'phase_before_close', 'failure_code',
        'ready_ns', 'fresh_received_ns', 'committed_ns', 'bootstrap_pts_us', 'fresh_pts_us'), 0)
    return row


class SourcePauseRecoveryChecks(unittest.TestCase):
    args = SimpleNamespace(v50_profile='on', surface_submit_lead_ms=0,
                           stage_diagnostics='off', codec_startup='off')
    report = {'phone_sampler_started': False, 'touch_source_switched': False}

    def test_one_phase_complete_receipt_is_not_steady_or_two_session_acceptance(self):
        row = recovery()
        self.assertTrue(driver.verify_pause_recovery(row, self.args, self.report))
        self.assertTrue(source.helper_readback(row, mode='pause-only'))
        self.assertFalse(source.helper_readback(row))
        self.assertFalse(driver.verify_credential_source_readback(row, 'saved-ui'))
        self.assertFalse(driver.verify_nps_network_readback(row, 'm1'))
        self.assertFalse(driver.verify_exit_confirmation_readback(row))
        self.assertFalse(driver.verify_steady_media_progress(row))
        self.assertFalse(driver.verify_steady_window_readback(row, 30))

    def test_any_contrary_receipt_or_actual_sampler_rejects_recovery(self):
        for key, value in (('source_recovery_no_reconnect', False), ('failure_class', 'IllegalStateException'),
                ('first_leave_audio_threads_alive', False), ('first_leave_audio_threads_alive', 1),
                ('first_actual_media_peer_port', 15558), ('source_owned_marker_cleanup_failed', True),
                ('steady_media_started_ns', 1), ('normal_UI_reconnected_received_media', False),
                ('source_phase_2_external_observer_confirmation', False),
                ('first_surface_submit_execution_verified', False)):
            row = recovery(); row[key] = value
            self.assertFalse(driver.verify_pause_recovery(row, self.args, self.report), key)
        row = recovery(); row['source_phase_1_local_tap']['remote_playback_verified_by_helper'] = True
        self.assertFalse(driver.verify_pause_recovery(row, self.args, self.report))
        for key in ('phone_sampler_started', 'touch_source_switched'):
            report = dict(self.report, **{key: True})
            self.assertFalse(driver.verify_pause_recovery(recovery(), self.args, report))

    def test_cleanup_projection_keeps_new_mode_receipts_bounded_without_exporting_raw_output(self):
        row = recovery()
        raw = 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(row)+'\n'
        value = driver.instrumentation_cleanup_diagnostic((raw, ''))
        self.assertEqual(value['numeric_result_status'], 'valid')
        for key in ('source_pause_only_recovery', 'source_recovery_no_steady_window', 'source_recovery_no_reconnect'):
            self.assertIs(value['numeric_result'][key], True)
        self.assertNotIn('first_actual_control_host', value['numeric_result'])
        row['source_recovery_no_reconnect'] = 1
        value = driver.instrumentation_cleanup_diagnostic((
            'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(row)+'\n', ''))
        self.assertEqual(value['numeric_result_status'], 'malformed')

    def test_driver_recovery_starts_no_sampler_or_reconnect_even_with_unexpected_old_marker(self):
        calls = []
        class Phase:
            phase, completed, observations = 1, [], {}
            def advance(self, **kwargs): self.phase, self.completed = 3, [1]
        class Markers:
            def require_absent(self): pass
            def cleanup(self): return {'owned_marker_cleanup_confirmed': True, 'cleanup_failures': 0}
        class Selection:
            def status(self): return {'remote_scope_clear_verified': True, 'possibly_retained_scopes': []}
        class Child:
            returncode = 0
            calls = 0
            def poll(self):
                self.calls += 1
                return None if self.calls == 1 else 0
            def communicate(self, **kwargs):
                return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(recovery())+'\n', ''
        child = Child()
        def run(argv, **kwargs):
            calls.append(argv); body = argv[-1]
            if argv[0] == 'lsof': return subprocess.CompletedProcess(argv, 1, b'', b'')
            if body == 'pidof '+driver.TARGET_PACKAGE: return subprocess.CompletedProcess(argv, 1, '', '')
            if body.startswith('cmd package list'):
                return subprocess.CompletedProcess(argv, 0, 'package:'+driver.TARGET_PACKAGE+' uid:10234\n', '')
            if body.startswith('pm path --user 0 '):
                return subprocess.CompletedProcess(argv, 0, 'package:/data/app/'+body.split()[-1]+'/base.apk\n', '')
            if 'sha256sum ' in body:
                name = 'local.huoguo.lanuitest' if 'lanuitest' in body else driver.TARGET_PACKAGE
                sha = source.HELPER_SHA256 if 'lanuitest' in name else 'd0437e51e8c2d0d27c89458b3a5e6467ee421f25337551d19ecc0992d18ecf08'
                return subprocess.CompletedProcess(argv, 0, sha+'  /data/app/'+name+'/base.apk\n', '')
            if 'cat '+driver.PRIVATE+'udp-app-last-report.json' in body:
                return subprocess.CompletedProcess(argv, 0, '{"audio_cleanup_confirmed":1}', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            argv = ['probe', '--output', folder, '--source-input', 'pause-only', '--network-scope', 'nps_owner',
                '--node', 'm1', '--media-only', '--credential-source', 'saved-ui', '--source-input-identity',
                '3470:10235:1952', '--source-snapshot-deployment', '/tmp/inert.json', '--v50-profile', 'on']
            stack.enter_context(patch.object(driver.sys, 'argv', argv))
            stack.enter_context(patch.object(driver.subprocess, 'run', side_effect=run))
            popen = stack.enter_context(patch.object(driver.subprocess, 'Popen', return_value=child))
            stack.enter_context(patch.object(driver.source_authenticated_driver, 'Coordinator', return_value=Phase()))
            stack.enter_context(patch.object(driver.source_phone_markers, 'PhoneMarkers', return_value=Markers()))
            stack.enter_context(patch.object(driver.source_snapshot_selection, 'read_private', return_value=object()))
            stack.enter_context(patch.object(driver.source_snapshot_selection, 'Selection', return_value=Selection()))
            stack.enter_context(patch.object(driver.time, 'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(driver.main(), 0)
            report = json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertEqual(popen.call_count, 1)
        self.assertFalse(report['phone_sampler_started'])
        self.assertIsNone(report['steady_media_progress_verified'])
        self.assertIsNone(report['steady_window_readback_verified'])
        self.assertTrue(report['source_recovery_audio_cleanup_verified'])
        self.assertFalse(report['reconnect_acceptance_exercised'])
        self.assertFalse(any('force-stop' in row[-1] for row in calls))
        self.assertFalse(any('test -f '+driver.PRIVATE+'udp-ui-phase-steady-media' in row[-1] for row in calls))


if __name__ == '__main__': unittest.main()
