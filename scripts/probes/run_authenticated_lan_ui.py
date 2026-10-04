#!/usr/bin/env python3
"""Bounded normal-UI UDP acceptance; private input or explicit saved-UI mode.

Does not create accounts, read a host credential or provision a UDP key. Outputs
only bounded numeric test reports, never instrumentation/system raw logs.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = '/data/user/0/local.remoteandroid.direct.experiment/files/'
TARGET_PACKAGE = 'local.remoteandroid.direct.experiment'
INSTRUMENTATION_NUMERIC_MARKER = 'INSTRUMENTATION_RESULT: numeric_result='
INSTRUMENTATION_DIAGNOSTIC_BYTES = 65536
INSTRUMENTATION_FAILURE_CLASSES = frozenset((
    'IllegalStateException', 'IllegalArgumentException', 'JSONException',
    'InterruptedException', 'NullPointerException', 'NoSuchFieldException',
    'IllegalAccessException', 'InvocationTargetException', 'ClassCastException',
    'SecurityException', 'IOException', 'RuntimeException', 'AssertionError'))
INSTRUMENTATION_FAILURE_LABELS = frozenset((
    'private_login_input_bound', 'input_read', 'input_cleanup',
    'nps_owner_account_required', 'existing_UI_attempt_busy',
    'normal_UI_start_button_missing', 'normal_UI_start', 'no_authenticated_media',
    'saved_UI_route_unverified', 'saved_UI_account_unavailable',
    'saved_UI_credential_unavailable', 'saved_UI_restore_unavailable',
    'reconnect_UI_button_missing', 'normal_UI_reconnect',
    'reconnect_no_authenticated_media', 'steady_sampler_completion_missing',
    'source_marker_timeout', 'source_marker_preexisting', 'source_marker_descriptor_bound',
    'source_marker_changed_size', 'source_marker_changed_inode', 'source_input_capture',
    'source_phase_preexisting', 'source_command_cleanup', 'source_input_ownership',
    'source_input_dispatch', 'source_observer_nonce_changed', 'source_verified_marker_cleanup',
    'source_owned_marker_cleanup'))
sys.path.insert(0, str(ROOT))
from udp_nps_profile import planned_profile
from scripts.probes import owner_source_gate
from scripts.probes import owner_source_stats_gate, source_window_control
from scripts.probes import source_authenticated_driver, source_authenticated_observation
from scripts.probes import source_remote_observation, source_phone_markers, source_snapshot_selection
from scripts.probes.owner_trace_prefix import TracePrefixReader, SourceTraceError, LABELS as TRACE_LABELS


def target_process_state(result):
    """Conservative pre-instrument check; no PID/raw error text is exported.

    Normal am instrumentation may restart its target before helper busy checks.
    Only pidof's empty exit1 with empty stderr verifies absence. A running idle
    login App is deliberately skipped too; no-restart lifecycle is unverified.
    """
    if type(result.returncode) is not int or not isinstance(result.stdout, str) or not isinstance(result.stderr, str):
        return 'unavailable'
    output, error = result.stdout.strip(), result.stderr.strip()
    if error or len(output) > 128:
        return 'unavailable'
    if result.returncode == 1 and not output:
        return 'absent'
    if result.returncode == 0 and re.fullmatch(r'[0-9]+(?:\s+[0-9]+){0,7}', output):
        values = [int(value) for value in output.split()]
        if all(0 < value <= 4194304 for value in values):
            return 'active'
    return 'unavailable'


def target_user0_uid(result):
    """Only the exact --user0 package readback may own the /data/user/0 marker."""
    if (type(result.returncode) is not int or result.returncode != 0
            or type(result.stdout) is not str or type(result.stderr) is not str
            or result.stderr.strip() or len(result.stdout) > 4096):
        raise ValueError('isolated_App_uid_readback')
    match = re.fullmatch('package:'+re.escape(TARGET_PACKAGE)+r' uid:([0-9]+)',
                         result.stdout.strip())
    if match is None or not 10000 <= int(match[1]) <= 999999:
        raise ValueError('isolated_App_uid_readback')
    return match[1]


def verify_credential_source_readback(result, expected_source, *, stages=('first', 'second')):
    if stages not in (('first',), ('first', 'second')): return False
    if not isinstance(result, dict) or 'failure_class' in result:
        return False
    if expected_source == 'private-file':
        # Historical private-file helper reports remain readable.
        return result.get('requested_credential_source', 'private-file') == 'private-file'
    if expected_source != 'saved-ui' or result.get('requested_credential_source') != 'saved-ui':
        return False
    if result.get('saved_UI_private_input_touched') is not False or result.get('saved_UI_secret_exported') is not False:
        return False
    return all(result.get(stage + '_' + field) is True for stage in stages
        for field in ('saved_UI_route_verified', 'saved_UI_allowed_account_verified',
                      'saved_UI_nonempty_credential_verified', 'saved_UI_normal_restore_used'))


def verify_nps_network_readback(result, node, *, stages=('first', 'second')):
    """Check the App's current attempt and parsed receiver, not requested settings.

    This establishes selected public tuples only. NPC outer transit, geography,
    independent content presentation and physical latency need separate evidence.
    """
    if stages not in (('first',), ('first', 'second')): return False
    if not isinstance(result, dict) or 'failure_class' in result or node not in ('m1', 'm5'):
        return False
    profile = planned_profile(node)
    if (result.get('requested_network_scope') != 'nps_owner'
            or result.get('requested_node') != node):
        return False
    for stage in stages:
        expected = {'actual_network_scope': 'nps_owner', 'actual_node': node,
            'actual_control_host': profile.public_control.host,
            'actual_control_port': profile.public_control.port,
            'actual_media_peer_host': profile.public_media.host,
            'actual_media_peer_port': profile.public_media.port,
            'network_readback_verified': True, 'media_transport_code': 1,
            'media_transport_is_App_UDP_not_NPC_outer_verification': True}
        for key, value in expected.items():
            actual = result.get(stage + '_' + key)
            if type(actual) is not type(value) or actual != value:
                return False
        for key, floor in (('actual_received_frames', 15), ('actual_codec_callback_count', 10)):
            value = result.get(stage + '_' + key)
            if type(value) is not int or value < floor:
                return False
    return True


def verify_v50_profile_readback(result, expected_enabled, *, stages=('first', 'second')):
    """ON needs actual preset click plus parsed session and decoded frame geometry.

    Legacy OFF reports remain readable; no callback count is a physical FPS claim.
    """
    if stages not in (('first',), ('first', 'second')): return False
    if type(expected_enabled) is not bool or not isinstance(result,dict) or 'failure_class' in result:
        return False
    if not expected_enabled and 'requested_v50_profile' not in result:
        return True  # Historical helper had no V50 entry and always selected 60 FPS.
    if result.get('requested_v50_profile') is not expected_enabled:
        return False
    previous_clicks=0
    for stage in stages:
        if (result.get(stage+'_video_profile_readback_verified') is not True
                or result.get(stage+'_video_profile_is_presented_FPS') is not False
                or result.get(stage+'_v50_profile_button_clicked') is not expected_enabled):
            return False
        fps,buffer,width,height,clicks=(result.get(stage+'_'+key) for key in (
            'actual_fps_limit','actual_buffer_ms','actual_video_width','actual_video_height',
            'v50_profile_button_click_count'))
        if (any(type(value) is not int for value in (fps,buffer,width,height,clicks))
                or fps!=(30 if expected_enabled else 60) or buffer!=80
                or not 1<=width<=4096 or not 1<=height<=4096):
            return False
        if expected_enabled:
            if clicks<=previous_clicks or max(width,height)>960:
                return False
        elif clicks!=0:
            return False
        previous_clicks=clicks
    return True


def verify_exit_confirmation_readback(result, *, stages=('first', 'second')):
    """Both sessions must execute Continue then Exit using real App dialog listeners."""
    if stages not in (('first',), ('first', 'second')): return False
    if not isinstance(result,dict) or 'failure_class' in result:return False
    fields=('exit_dialog_shown','exit_repeated_back_same_dialog','exit_continue_preserved_attempt',
            'exit_continue_media_progress','exit_positive_button_clicked','exit_captured_attempt_cancelled',
            'exit_used_actual_UI_buttons','exit_UI_callbacks_observed')
    return all(result.get(stage+'_'+field) is True for stage in stages for field in fields)


def verify_credential_save_readback(result, expected_enabled):
    if type(expected_enabled) is not bool or not isinstance(result,dict) or 'failure_class' in result:return False
    if result.get('requested_credential_save_acceptance') is not expected_enabled:return False
    fields=('credential_save_used_actual_UI','credential_save_reopen_restored','credential_clear_reopen_empty','credential_final_save_reopen_retained')
    if not expected_enabled:return not any(key in result for key in fields)
    return all(result.get(key) is True for key in fields) and result.get('credential_secret_exported') is False and result.get('credential_other_package_modified') is False


def verify_nps_physical_network_binding(result, *, stages=('first', 'second')):
    """API identity/bind evidence only; this cannot prove packet path or country."""
    if stages not in (('first',), ('first', 'second')): return False
    if not isinstance(result,dict) or 'failure_class' in result:return False
    for stage in stages:
        if result.get(stage+'_physical_network_same_lease') is not True:return False
        for field,floor,ceiling in (('physical_network_handle',1,None),('physical_network_transport',1,2),
                ('physical_https_bind_calls',1,None),('physical_udp_bind_calls',1,1)):
            value=result.get(stage+'_'+field)
            if type(value) is not int or value<floor or (ceiling is not None and value>ceiling):return False
        for field in ('physical_packet_route_verified','physical_domestic_country_verified'):
            if result.get(stage+'_'+field) is not False:return False
    return True


def verify_surface_submit_readback(result, expected_lead, *, stages=('first', 'second')):
    """Require actual per-session execution evidence, never descriptor/request echo alone."""
    if stages not in (('first',), ('first', 'second')): return False
    if (type(expected_lead) is not int or expected_lead not in (0, 16)
            or not isinstance(result, dict) or 'failure_class' in result
            or type(result.get('requested_surface_submit_lead_ms')) is not int
            or result['requested_surface_submit_lead_ms'] != expected_lead):
        return False
    for stage in stages:
        if result.get(stage+'_surface_submit_execution_verified') is not True:
            return False
        keys = ('surface_submit_lead_ms', 'surface_submit_status_code',
                'surface_submit_wait_count', 'surface_submit_applications')
        values = [result.get(stage+'_'+key) for key in keys]
        if any(type(value) is not int for value in values):
            return False
        lead, status, waits, applications = values
        if lead != expected_lead or (expected_lead == 0 and (status, waits, applications) != (0, 0, 0)):
            return False
        if expected_lead == 16 and (status != 1 or waits < 1 or applications < 1):
            return False
    return True


def verify_steady_window_readback(result, expected_seconds):
    """The sampler completion marker is required before the UI may leave steady media."""
    if (type(expected_seconds) is not int or not 20 <= expected_seconds <= 150
            or not isinstance(result,dict) or 'failure_class' in result
            or type(result.get('requested_steady_seconds')) is not int
            or result['requested_steady_seconds']!=expected_seconds
            or result.get('steady_sampler_completion_observed') is not True):
        return False
    started,finished,wait_ms=(result.get(key) for key in (
        'steady_media_started_ns','steady_media_finished_ns','steady_media_wait_ms'))
    return bool(type(started) is int and type(finished) is int and 0 < started <= finished
        and type(wait_ms) in (int,float) and math.isfinite(wait_ms)
        and wait_ms >= (expected_seconds+2)*1000
        and abs(wait_ms-(finished-started)/1e6) <= .001)


def verify_stage_diagnostics_readback(result, expected_enabled, *, stages=('first', 'second')):
    if stages not in (('first',), ('first', 'second')): return False
    if (type(expected_enabled) is not bool or not isinstance(result,dict)
            or 'failure_class' in result or result.get('requested_stage_diagnostics_enabled') is not expected_enabled):
        return False
    for stage in stages:
        value=result.get(stage+'_stage_diagnostics_enabled')
        if (type(value) is not int or value != int(expected_enabled)
                or result.get(stage+'_stage_diagnostics_verified') is not True):
            return False
    return True


def verify_codec_startup_readback(result, expected_enabled, *, stages=('first', 'second')):
    """Require ended sessions' mode and committed gate execution, not an option echo."""
    if stages not in (('first',), ('first', 'second')): return False
    if (type(expected_enabled) is not bool or not isinstance(result, dict)
            or 'failure_class' in result
            or result.get('requested_codec_startup_ready_enabled') is not expected_enabled):
        return False
    for stage in stages:
        if (type(result.get(stage+'_codec_startup_ready_enabled')) is not int
                or result[stage+'_codec_startup_ready_enabled']!=int(expected_enabled)
                or result.get(stage+'_codec_startup_readback_verified') is not True):
            return False
        gate=result.get(stage+'_codec_startup_gate')
        fields=('enabled','phase_before_close','failure_code','ready_ns',
                'fresh_received_ns','committed_ns','bootstrap_pts_us','fresh_pts_us')
        if not isinstance(gate,dict) or any(type(gate.get(key)) is not int for key in fields):
            return False
        if expected_enabled:
            if (gate['enabled']!=1 or gate['phase_before_close']!=5 or gate['failure_code']!=0
                    or not 0<gate['ready_ns']<=gate['fresh_received_ns']<=gate['committed_ns']
                    or not 0<=gate['bootstrap_pts_us']<gate['fresh_pts_us']):
                return False
        elif (gate['enabled'],gate['phase_before_close'],gate['committed_ns'])!=(0,0,0):
            return False
    return True


def verify_steady_media_progress(result):
    if (not isinstance(result,dict) or result.get('steady_progress_monitor_enabled') is not True
            or result.get('steady_media_progress_healthy') is not True):return False
    limit=result.get('steady_progress_stall_threshold_ns');idle=result.get('steady_progress_max_idle_ns')
    rows=result.get('steady_progress_samples')
    if (type(limit) is not int or limit!=3000000000 or type(idle) is not int or not 0<=idle<limit
            or not isinstance(rows,list) or not 20<=len(rows)<=48):return False
    for i,row in enumerate(rows):
        if not isinstance(row,dict):return False
        for key in ('phone_ns','worker_received_frames','codec_callback_count'):
            if type(row.get(key)) is not int or row[key]<0:return False
        if i and (row['phone_ns']<=rows[i-1]['phone_ns'] or
                  any(row[k]<rows[i-1][k] for k in ('worker_received_frames','codec_callback_count'))):return False
    seconds=result.get('requested_steady_seconds',20)
    if type(seconds) is not int or not 20<=seconds<=150:return False
    # A long case cannot pass with only the historical first48s of samples.
    minimum_span_ns=max(19,seconds-5)*1000000000
    return (rows[-1]['phone_ns']-rows[0]['phone_ns']>=minimum_span_ns and
            any(rows[-1][k]>rows[0][k] for k in ('worker_received_frames','codec_callback_count')))


def verify_pause_recovery(result, args, driver_report):
    """One actual first-session close; never imply steady/reconnect acceptance."""
    if (not source_authenticated_driver.helper_readback(result, mode='pause-only')
            or driver_report.get('phone_sampler_started') is not False
            or driver_report.get('touch_source_switched') is not False
            or result.get('first_leave_audio_threads_alive') != 0
            or type(result.get('first_leave_audio_threads_alive')) is not int
            or any(name in result for name in ('steady_media_started_ns',
                      'steady_sampler_completion_observed', 'normal_UI_reconnected_received_media'))):
        return False
    stages = ('first',)
    return all((verify_credential_source_readback(result, 'saved-ui', stages=stages),
        verify_credential_save_readback(result, False),
        verify_v50_profile_readback(result, args.v50_profile == 'on', stages=stages),
        verify_exit_confirmation_readback(result, stages=stages),
        verify_nps_physical_network_binding(result, stages=stages),
        verify_nps_network_readback(result, 'm1', stages=stages),
        verify_surface_submit_readback(result, args.surface_submit_lead_ms, stages=stages),
        verify_stage_diagnostics_readback(result, args.stage_diagnostics == 'on', stages=stages),
        verify_codec_startup_readback(result, args.codec_startup == 'on', stages=stages)))


def record_cleanup_failure(report, operation, failure=None, returncode=None):
    """Never include subprocess output or exception text in numeric evidence."""
    item = {'operation': operation}
    if failure is not None:
        item['failure_class'] = type(failure).__name__
    if returncode is not None:
        item['return_code'] = returncode
    report.setdefault('cleanup_failures', []).append(item)


def attempt_cleanup(report, operation, call):
    try:
        result = call()
        if result.returncode:
            record_cleanup_failure(report, operation, returncode=result.returncode)
    except Exception as failure:
        record_cleanup_failure(report, operation, failure=failure)


def reap_owned_process(proc, report, operation, wait_timeout, terminate=False):
    """Bound every owned child wait, including a child ignoring SIGTERM."""
    def signal(method):
        try:
            if proc.poll() is None:
                getattr(proc, method)()
        except Exception as failure:
            record_cleanup_failure(report, operation+'_'+method, failure=failure)

    if terminate:
        signal('terminate')
    for timeout, escalation in [(wait_timeout, 'terminate'), (2, 'kill'), (2, None)]:
        try:
            return proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as failure:
            record_cleanup_failure(report, operation+'_wait', failure=failure)
            if escalation is not None:
                signal(escalation)
        except Exception as failure:
            record_cleanup_failure(report, operation+'_reap', failure=failure)
            signal('kill')
            # A failed pipe read can still leave a running child. Reap it with
            # wait, without depending on those broken pipes or printing them.
            try:
                proc.wait(timeout=2)
            except Exception as wait_failure:
                record_cleanup_failure(report, operation+'_final_wait', failure=wait_failure)
            return None
    return None


def instrumentation_cleanup_diagnostic(output):
    """Project cleanup output only; never infer media success or phone ownership.

    The byte bound applies to parsing/projection, not the existing communicate
    pipe capture. Raw child output exists in memory but is never persisted here.
    Only stdout's single complete numeric marker is eligible; stderr is never a
    result source. This function does not change any cleanup or failure action.
    """
    result = dict(schema=1, scope='cleanup_output_not_media_success_or_phone_ownership',
        output_available=False, stdout_bytes=None, stderr_bytes=None,
        stdout_lines=None, stderr_lines=None, numeric_result_status='absent',
        numeric_result_count=None, stderr_numeric_marker_count=None,
        instrumentation_failed_marker_count=None, instrumentation_aborted_marker_count=None,
        security_exception_line_count=None, instrumentation_code_count=None,
        instrumentation_code_status='absent', instrumentation_code=None)
    if output is None:
        return result
    if (type(output) is not tuple or len(output) != 2
            or any(type(channel) is not str for channel in output)):
        result['numeric_result_status'] = 'malformed'
        return result
    stdout, stderr = output
    result['output_available'] = True
    # Count the already captured text without a second unbounded encoded copy.
    try:
        for name, text in (('stdout', stdout), ('stderr', stderr)):
            result[name+'_lines'] = text.count('\n') + int(bool(text) and not text.endswith('\n'))
            result[name+'_bytes'] = sum(len(text[start:start+4096].encode('utf-8'))
                for start in range(0, len(text), 4096))
    except UnicodeError:
        result['numeric_result_status'] = 'malformed'
        return result
    if result['stdout_bytes'] + result['stderr_bytes'] > INSTRUMENTATION_DIAGNOSTIC_BYTES:
        result['numeric_result_status'] = 'over_bound'
        return result
    def lf_rows(text):
        # Instrumentation uses LF/CRLF. A bare CR or Unicode separator inside
        # an arbitrary log line must not create an eligible marker at column0.
        parts = text.split('\n')
        return [row+'\n' for row in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

    stdout_rows, stderr_rows = lf_rows(stdout), lf_rows(stderr)
    candidates = [row for row in stdout_rows if row.startswith(INSTRUMENTATION_NUMERIC_MARKER)]
    stderr_count = sum(row.startswith(INSTRUMENTATION_NUMERIC_MARKER) for row in stderr_rows)
    result.update(numeric_result_count=len(candidates), stderr_numeric_marker_count=stderr_count)
    rows = stdout_rows + stderr_rows
    result['instrumentation_failed_marker_count'] = sum(row.startswith('INSTRUMENTATION_FAILED:') for row in rows)
    result['instrumentation_aborted_marker_count'] = sum(row.startswith('INSTRUMENTATION_ABORTED:') for row in rows)
    result['security_exception_line_count'] = sum('java.lang.SecurityException' in row for row in rows)
    codes = [row for row in rows if row.startswith('INSTRUMENTATION_CODE:')]
    result['instrumentation_code_count'] = len(codes)
    if len(codes) > 1:
        result['instrumentation_code_status'] = 'ambiguous'
    elif codes:
        match = re.fullmatch(r'INSTRUMENTATION_CODE: (-?[0-9]{1,10})\r?\n', codes[0])
        if match and -(1 << 31) <= int(match[1]) < (1 << 31):
            result.update(instrumentation_code_status='valid', instrumentation_code=int(match[1]))
        else:
            result['instrumentation_code_status'] = 'malformed'
    if len(candidates) > 1 or (candidates and stderr_count):
        result['numeric_result_status'] = 'ambiguous'
        return result
    if not candidates:
        return result
    row = candidates[0]
    if not row.endswith('\n'):
        result['numeric_result_status'] = 'malformed'
        return result

    def closed_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate_key')
            value[key] = item
        return value

    def reject_constant(_):
        raise ValueError('nonfinite_json')

    try:
        value = json.loads(row[len(INSTRUMENTATION_NUMERIC_MARKER):],
            object_pairs_hook=closed_object, parse_constant=reject_constant)
        if type(value) is not dict:
            raise ValueError('object_required')
        projected = {}
        if 'helper_owned_attempt_started' in value:
            if type(value['helper_owned_attempt_started']) is not bool:
                raise ValueError('bool_required')
            projected['helper_owned_attempt_started'] = value['helper_owned_attempt_started']
        for name in ('requested_authenticated_source_input', 'source_owned_marker_cleanup_failed',
                     'source_pause_only_recovery', 'source_recovery_no_steady_window', 'source_recovery_no_reconnect',
                     'source_phase_1_external_observer_confirmation', 'source_phase_2_external_observer_confirmation'):
            if name in value:
                if type(value[name]) is not bool:
                    raise ValueError('bool_required')
                projected[name] = value[name]
        tap_keys = {'down_attempted', 'down_returned', 'up_attempted', 'up_returned',
                    'cancel_attempted', 'cancel_returned', 'cancel_skipped_for_changed_owner',
                    'remote_playback_verified_by_helper'}
        for phase in (1, 2):
            name = 'source_phase_%d_local_tap' % phase
            if name in value:
                receipt = value[name]
                if (type(receipt) is not dict or set(receipt) != tap_keys
                        or any(type(flag) is not bool for flag in receipt.values())):
                    raise ValueError('tap_receipt_bool_schema')
                projected[name] = dict(receipt)
        for name, allowed in (('failure_class', INSTRUMENTATION_FAILURE_CLASSES),
                ('failure_cause_class', INSTRUMENTATION_FAILURE_CLASSES),
                ('bounded_failure_label', INSTRUMENTATION_FAILURE_LABELS)):
            if name in value:
                if type(value[name]) is not str:
                    raise ValueError('enum_required')
                projected[name] = value[name] if value[name] in allowed else 'unclassified'
        if 'connection_failure_code' in value:
            code = value['connection_failure_code']
            if type(code) is not int or not (code in (0, 1, 2) or 100 <= code <= 599):
                raise ValueError('connection_code_bound')
            projected['connection_failure_code'] = code
    except (ValueError, TypeError, RecursionError, OverflowError):
        result['numeric_result_status'] = 'malformed'
        return result
    result.update(numeric_result_status='valid', numeric_result=projected)
    return result


def instrumentation_budget_seconds(steady_seconds):
    """Finite global budget; retain short cases and allow leave/reconnect after135s."""
    if type(steady_seconds) is not int or not 20<=steady_seconds<=150:
        raise ValueError('steady_seconds_bound')
    return max(120,steady_seconds+90)


def parse_arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phone', default='f7fc9469')
    p.add_argument('--guest', default=None,
                   help='Explicit source emulator on this ADB server; public M5 may use phone-only sampling')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--touch-mode', choices=['direct','os','adb','kernel'], default='direct')
    p.add_argument('--rate-index', type=int, choices=range(5), default=2)
    p.add_argument('--network-scope', choices=['lan','tailnet','nps_owner'], default='lan')
    p.add_argument('--node', choices=['m1','m5'], default=None,
                   help='Required exact public owner-trial node; not allowed for LAN/Tailnet')
    p.add_argument('--phone-only-sampler', action='store_true',
                   help='Media-only: collect phone SF, omit unavailable remote source ADB; never claim source cadence')
    p.add_argument('--v50-profile', choices=['off','on'], default='off',
                   help='Click actual V50 optimize button: 540P/4M/30FPS/80ms; low-load diagnostics retained')
    p.add_argument('--pcm-queue', choices=['off','on'], default='off')
    p.add_argument('--stage-diagnostics', choices=['off','on'], default='on',
                   help='Owner instrumentation only: stage sampling on/off, frozen before media startup')
    p.add_argument('--codec-startup', choices=['off','on'], default='off',
                   help='Explicit one-attempt codec-ready startup admission candidate; steady queue policy retained')
    p.add_argument('--surface-submit-lead-ms', type=int, choices=[0,16], default=0,
                   help='Explicit owner-only Surface submission experiment; does not change playback target/buffer')
    p.add_argument('--credential-save',choices=['off','on'],default='off',help='Actual save/reopen/clear/reopen/save UI acceptance; existing test account only')
    p.add_argument('--credential-source', choices=['private-file', 'saved-ui'], default='private-file',
                   help='Explicit owner saved-UI read: exact public node, existing encrypted UI restore only; no fallback')
    p.add_argument('--steady-seconds', type=int, choices=range(20,151), default=20,
                   help='Bounded20..150s SF steady window; helper waits this duration plus2s before reconnect')
    p.add_argument('--media-only', action='store_true')
    p.add_argument('--source-description', default='Morphe YouTube real video; selected content format requires separate readback')
    p.add_argument('--owner-source-guard', choices=['off','numeric-playing-unknown'], default='off',
                   help='Owner LAN only: one bounded source observation immediately before instrumentation')
    p.add_argument('--source-listener-monotonic-ns', type=int, default=None)
    p.add_argument('--source-expected-pid', type=int, default=None)
    p.add_argument('--source-expected-uid', type=int, default=None)
    p.add_argument('--source-expected-start-ticks', type=int, default=None)
    p.add_argument('--source-trace-root', type=Path, default=None,
                   help='Fresh private empty root for this owned listener; never a historical trace directory')
    p.add_argument('--source-stats-window', choices=['off','on'], default='off',
                   help='Explicit protected M1 owner experiment: paused Stats endpoints around owned playback')
    p.add_argument('--source-input', choices=['off', 'native', 'pause-only'], default='off',
                   help='Explicit M1 saved-UI owner trial: current authenticated helper native play/pause')
    p.add_argument('--source-snapshot-deployment', type=Path, default=None,
                   help='Explicit pause-only trial: private pinned standalone readonly JAR descriptor')
    p.add_argument('--source-input-identity', default=None,
                   help='Exact fresh source PID:UID:start_ticks; never a cached target-coordinate file')
    args = p.parse_args(argv)
    source_options = (args.source_listener_monotonic_ns, args.source_expected_pid,
                      args.source_expected_uid, args.source_expected_start_ticks, args.source_trace_root)
    if args.owner_source_guard == 'off':
        if any(value is not None for value in source_options):
            p.error('Source guard options require explicit owner source guard')
    elif (args.network_scope != 'lan' or not args.media_only or args.phone_only_sampler
            or any(value is None for value in source_options)
            or args.source_listener_monotonic_ns <= 0
            or not 0 < args.source_expected_pid <= 2147483647
            or not 0 <= args.source_expected_uid <= 2147483647
            or not 0 < args.source_expected_start_ticks <= 9223372036854775807
            or not args.source_trace_root.is_absolute()):
        p.error('Numeric source guard requires owner LAN media-only and complete bounded identity/trace options')
    if args.source_stats_window == 'on' and (args.owner_source_guard == 'off' or args.guest not in (None, 'emulator-5556')):
        p.error('Stats window requires the protected M1 numeric source guard')
    if args.v50_profile == 'on':
        if args.pcm_queue != 'off' or args.codec_startup != 'off' or args.surface_submit_lead_ms != 0:
            p.error('V50 preset acceptance cannot also enable PCM/startup/Surface experiments')
        # The actual button sets these values; report the effective preset rather
        # than claim the separate owner instrumentation default survived its click.
        args.rate_index = 0
        args.stage_diagnostics = 'off'
    if (args.network_scope == 'nps_owner') != (args.node is not None):
        p.error('Public owner scope requires explicit node; existing scopes do not accept node')
    if args.credential_source == 'saved-ui' and (args.network_scope != 'nps_owner' or args.credential_save != 'off'):
        p.error('Saved-UI credentials require an exact public owner node and credential-save off')
    if args.phone_only_sampler and not args.media_only:
        p.error('Phone-only sampler requires media-only; no remote source touch setup')
    if args.network_scope == 'nps_owner' and args.node == 'm5' and not args.phone_only_sampler:
        p.error('Public M5 requires phone-only media sampling; remote source ADB identity is not verified by this driver')
    if args.network_scope == 'nps_owner':
        profile = planned_profile(args.node)
        args.guest = args.guest or profile.guest_serial
        if args.guest != profile.guest_serial:
            p.error('Explicit source guest must match the fixed public node profile')
    else:
        args.guest = args.guest or 'emulator-5556'
    if args.source_input != 'off':
        if (args.network_scope != 'nps_owner' or args.node != 'm1' or not args.media_only
                or args.phone_only_sampler or args.credential_source != 'saved-ui'
                or args.owner_source_guard != 'off' or args.source_stats_window != 'off'
                or args.source_input_identity is None):
            p.error('Native source input requires explicit M1 saved-UI media-only and fresh source identity')
        match = re.fullmatch(r'([0-9]{1,10}):([0-9]{1,10}):([0-9]{1,19})', args.source_input_identity)
        if match is None:
            p.error('Native source input identity must be bounded PID:UID:start_ticks')
        values = list(map(int, match.groups()))
        if not (1 <= values[0] <= 2147483647 and 0 <= values[1] <= 2147483647
                and 1 <= values[2] <= 9223372036854775807):
            p.error('Native source input identity out of range')
        args.source_input_identity = dict(zip(('pid', 'uid', 'start_ticks'), values))
    elif args.source_input_identity is not None:
        p.error('Native source input identity requires explicit opt-in')
    if ((args.source_input == 'pause-only') != (args.source_snapshot_deployment is not None)
            or (args.source_snapshot_deployment is not None and not args.source_snapshot_deployment.is_absolute())):
        p.error('Direct snapshot selection requires the explicit bounded pause-only owner trial')
    return args


def main():
    args = parse_arguments()
    args.output.mkdir(parents=True, exist_ok=True)

    def adb(serial, command, check=True):
        return subprocess.run(['adb', '-s', serial, 'shell', command],
                              capture_output=True, text=True, timeout=8, check=check)

    def root(command, check=True):
        return adb(args.phone, 'su -c '+shlex.quote(command), check)

    def app_uid():
        if args.owner_source_guard != 'off' or args.source_input != 'off':
            return target_user0_uid(adb(args.phone,
                'cmd package list packages --user 0 -U '+TARGET_PACKAGE))
        return adb(args.phone,'cmd package list packages -U '+TARGET_PACKAGE).stdout.split('uid:',1)[1].split(',',1)[0].strip()

    flags=['udp-ui-phase-ready-touch','udp-ui-phase-touch-ready','udp-ui-phase-touch-ready.tmp','udp-ui-phase-steady-media','udp-ui-phase-steady-sampled','udp-ui-phase-steady-sampled.tmp','udp-ui-phase-adb-tap','udp-ui-phase-adb-tap-done','udp-ui-phase-adb-tap-done.tmp']
    proc = None
    source_reader = source_gate = source_stats_before = None
    source_native = source_markers = snapshot_selection = None
    instrumentation_reaped = False
    samplers=[]
    scope_label = ('physical LAN' if args.network_scope == 'lan' else 'registered Tailnet'
                   if args.network_scope == 'tailnet' else 'owner nonisolated public NPS')
    report={'scope':'normal App UI existing account; bounded '+scope_label+' UDP; host isolation and outer path require separate evidence',
            'source':args.source_description+('' if args.media_only else '; dedicated receipt only during touch phase'),
            'phone_sampler_started':False,'touch_source_switched':False}
    report.update(requested_v50_profile=args.v50_profile=='on',touch_mode=args.touch_mode, video_target_bps=[4000000,8000000,12000000,16000000,24000000][args.rate_index],network_scope=args.network_scope,pcm_queue_enabled=args.pcm_queue=='on',media_only=args.media_only,requested_surface_submit_lead_ms=args.surface_submit_lead_ms,requested_steady_seconds=args.steady_seconds,requested_stage_diagnostics_enabled=args.stage_diagnostics=='on',requested_codec_startup_ready_enabled=args.codec_startup=='on',requested_credential_save_acceptance=args.credential_save=='on')
    if args.network_scope == 'nps_owner':
        profile = planned_profile(args.node)
        report.update(node=args.node, advertised_control_host=profile.public_control.host,
            advertised_control_port=profile.public_control.port,
            advertised_media_host=profile.public_media.host,
            advertised_media_port=profile.public_media.port,
            host_isolation_accepted=False, NPC_outer_path_verified=False,
            local_driver_formal_gate_is_remote_host_gate=False)
    report.update(phone_only_sampler=args.phone_only_sampler,
        source_SF_sampled_by_this_driver=not args.phone_only_sampler,
        physical_FPS_acceptance=False, requested_credential_source=args.credential_source,
        preinstrument_target_process_absent_verified=False,
        instrumentation_no_restart_lifecycle_verified=False)
    report.update(owner_source_guard_enabled=args.owner_source_guard != 'off',
        source_launch_freshness_verified=False, source_first_capture_freshness_verified=False,
        source_final_target_process_absent_verified=False,
        source_freshness_scope='first_owned_session_capture_only_not_reconnect_or_presentation')
    report['source_stats_window_enabled'] = args.source_stats_window == 'on'
    report['requested_authenticated_source_input'] = args.source_input
    adb_tap_done=False
    try:
        gate=subprocess.run(['lsof','-nP','-iTCP:15556','-sTCP:ESTABLISHED','-t'],
                          capture_output=True, timeout=2)
        if gate.returncode not in (0,1):
            raise RuntimeError('formal_gate_failed')
        if gate.stdout.strip():
            raise RuntimeError('formal_session_active')
        state = target_process_state(adb(args.phone, 'pidof '+TARGET_PACKAGE, False))
        report['preinstrument_target_process_state'] = state
        if state != 'absent':
            raise RuntimeError('target_App_process_active_skip' if state == 'active'
                               else 'target_App_process_check_unavailable_skip')
        report['preinstrument_target_process_absent_verified'] = True
        if args.source_input != 'off':
            if args.source_snapshot_deployment is not None:
                snapshot_selection = source_snapshot_selection.Selection(
                    source_snapshot_selection.read_private(args.source_snapshot_deployment))
            def marker_root(command):
                return subprocess.run(['adb', '-s', args.phone, 'shell', 'su -c '+shlex.quote(command)],
                    capture_output=True, text=True, timeout=1)
            source_markers = source_phone_markers.PhoneMarkers(marker_root, int(app_uid()))
            source_markers.require_absent()
            # Actual installed APK bytes, not a helper filename or request echo.
            for package, expected_sha in (
                    ('local.huoguo.lanuitest', source_authenticated_driver.HELPER_SHA256),
                    (TARGET_PACKAGE, 'd0437e51e8c2d0d27c89458b3a5e6467ee421f25337551d19ecc0992d18ecf08')):
                path_result = adb(args.phone, 'pm path --user 0 '+package)
                match = re.fullmatch(r'package:(/data/app/[A-Za-z0-9_/+~=.-]+/base\.apk)\s*', path_result.stdout)
                if path_result.stderr or match is None:
                    raise source_authenticated_driver.Rejected('source_input_helper_unmatched')
                digest = marker_root('sha256sum '+shlex.quote(match[1]))
                if (digest.returncode != 0 or digest.stderr
                        or digest.stdout != expected_sha+'  '+match[1]+'\n'):
                    raise source_authenticated_driver.Rejected('source_input_helper_unmatched')
            report['source_input_matching_installed_artifacts_verified'] = True
            def collect_target(state, timeout):
                kwargs = {'reader_factory': snapshot_selection.factory} if snapshot_selection is not None else {}
                return source_remote_observation.collect('adb', args.guest, 'aqz-KE-bpKQ',
                    required_state=state, display_width=1080, display_height=1920,
                    require_display=True, timeout=timeout, **kwargs)
            def observe_target(state):
                return collect_target(state, 6)
            def observe_transition(state, identity):
                begin = time.monotonic_ns()
                value = source_authenticated_observation.collect_state('adb', args.guest, identity,
                    state, timeout=3, previous_state='paused' if state == 'playing' else 'playing')
                if state == 'paused':
                    remaining = 6 - (time.monotonic_ns()-begin)/1e9
                    if remaining < 3:
                        raise source_authenticated_driver.Rejected('source_input_observer_budget')
                    post = collect_target('paused', min(6, remaining))
                    # Qualify the current snapshot before interpreting its fields.
                    ready = dict(source_native.ready, phase=1)
                    source_authenticated_driver.command(ready, post, identity, time.monotonic_ns())
                    first = source_native.observations['1']['target']['source']['stats']
                    after = post['source']['stats']
                    if any(first[k] != after[k] for k in ('video_id', 'video_format', 'audio_format')):
                        raise source_authenticated_driver.Rejected('source_input_identity_changed')
                    value['paused_Stats_after'] = post
                    value['endpoint_formats_match'] = True
                return value
            source_native = source_authenticated_driver.Coordinator(source_markers, observe_target,
                observe_transition, args.source_input_identity, mode=args.source_input)
        if args.credential_source == 'private-file' and root('test -s '+PRIVATE+'udp-test-login.json',False).returncode:
            raise RuntimeError('private_login_missing')
        root('rm -f '+PRIVATE+'udp-app-last-report.json '+PRIVATE+'udp-app-first-report.json '+' '.join(PRIVATE+f for f in flags))
        if args.owner_source_guard != 'off':
            # All slow preflight/marker work precedes this one synchronous read.
            # Reservation/registry/quiescence still belong to the supervisor.
            source_reader = TracePrefixReader(args.source_trace_root)
            if args.source_stats_window == 'on':
                source_stats_before = owner_source_stats_gate.collect_fresh('adb', args.guest,
                    {'pid': args.source_expected_pid, 'uid': args.source_expected_uid,
                     'start_ticks': args.source_expected_start_ticks}, 'aqz-KE-bpKQ',
                    required_state='paused', listener_ready_ns=args.source_listener_monotonic_ns)
                report['source_Stats_before'] = source_stats_before
                report['source_resume_receipt'] = source_window_control.transition('adb', args.guest,
                    source_stats_before['identity'], 'playing')
            source_gate = owner_source_gate.collect_playing_unknown('adb', args.guest,
                args.source_listener_monotonic_ns, {'pid': args.source_expected_pid,
                'uid': args.source_expected_uid, 'start_ticks': args.source_expected_start_ticks})
            report['source_observation'] = source_gate
            # The bounded collector can take15s. Recheck the target before am
            # instrument, which may restart it. This narrows that availability
            # gap; it is not an atomic phone-side reservation or no-restart proof.
            state = target_process_state(adb(args.phone, 'pidof '+TARGET_PACKAGE, False))
            report['source_preinstrument_target_process_state'] = state
            if state != 'absent':
                raise RuntimeError('target_App_process_active_skip' if state == 'active'
                    else 'target_App_process_check_unavailable_skip')
            report['source_final_target_process_absent_verified'] = True
            launch_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            owner_source_gate.validate_launch_fresh(source_gate, launch_ns)
            if source_stats_before is not None:
                owner_source_stats_gate.require_fresh(source_stats_before, launch_ns)
            report.update(source_launch_freshness_verified=True,
                          source_instrumentation_launch_monotonic_ns=launch_ns)
        if source_native is not None:
            state = target_process_state(adb(args.phone, 'pidof '+TARGET_PACKAGE, False))
            if state != 'absent':
                raise RuntimeError('target_App_process_active_skip' if state == 'active'
                                   else 'target_App_process_check_unavailable_skip')
            report['source_input_final_target_absence_verified'] = True
        proc = subprocess.Popen(['adb','-s',args.phone,'shell','su -c '+shlex.quote(
            'am instrument -w -e touch_mode '+args.touch_mode+' -e rate_index '+str(args.rate_index)+' -e network_scope '+args.network_scope+(' -e node '+args.node if args.node else '')+' -e v50_profile '+args.v50_profile+' -e pcm_queue '+args.pcm_queue+' -e stage_diagnostics '+args.stage_diagnostics+' -e codec_startup '+args.codec_startup+' -e credential_save '+args.credential_save+' -e credential_source '+args.credential_source+' -e source_input '+args.source_input+' -e surface_submit_lead_ms '+str(args.surface_submit_lead_ms)+' -e steady_seconds '+str(args.steady_seconds)+' -e media_only '+str(args.media_only).lower()+' local.huoguo.lanuitest/local.remoteandroid.direct.LanUiAcceptance')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline=time.monotonic()+instrumentation_budget_seconds(args.steady_seconds)+(20 if args.credential_save=='on' else 0)
        while proc.poll() is None and time.monotonic()<deadline:
            if source_native is not None:
                if source_native.phase == 1 or (source_native.phase == 2 and report.get('steady_samplers_completed_before_leave')):
                    source_native.advance(samplers_completed=bool(report.get('steady_samplers_completed_before_leave')))
                    report['authenticated_source_phases'] = source_native.observations
                if args.source_input == 'pause-only' or source_native.phase == 1:
                    time.sleep(.1)
                    continue
            if source_gate is not None and not report['source_first_capture_freshness_verified']:
                now_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                owner_source_gate.validate_launch_fresh(source_gate, now_ns)
                prefix = source_reader.read()
                # The producer may append between the pre-read deadline check
                # and pread. Qualify against a fresh post-read clock sample.
                observed_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                try:
                    qualification = owner_source_gate.qualify_first_capture(source_gate,
                        prefix, observed_ns,
                        clock_domain='host_clock_gettime_CLOCK_MONOTONIC_ns')
                except owner_source_gate.SourceGateError as error:
                    if str(error) != 'source_first_capture_missing':
                        raise
                else:
                    report['source_first_capture'] = qualification
                    report['source_first_capture_freshness_verified'] = True
                if not report['source_first_capture_freshness_verified']:
                    time.sleep(.1)
                    continue
            if not report['phone_sampler_started']:
                if root('test -f '+PRIVATE+'udp-ui-phase-steady-media',False).returncode==0:
                    selections = [('phone',args.phone,'local.remoteandroid.direct.experiment')]
                    if not args.phone_only_sampler:
                        selections.append(('source',args.guest,'app.morphe.android.youtube'))
                    if source_stats_before is not None:
                        report['sampler_dispatch_started_MONOTONIC_ns'] = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                        owner_source_stats_gate.require_fresh(source_stats_before, report['sampler_dispatch_started_MONOTONIC_ns'])
                    for name,serial,package in selections:
                        samplers.append(subprocess.Popen([sys.executable,str(ROOT/'scripts/probes/measure_surface_cadence.py'),
                            '--serial',serial,'--package',package,'--seconds',str(args.steady_seconds),'--wait-layer','3',
                            '--output',str(args.output/(name+'-cadence.json'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
                    report['phone_sampler_started']=True
            if (report['phone_sampler_started'] and not report.get('steady_samplers_completed_before_leave')
                    and len(samplers)==(1 if args.phone_only_sampler else 2)
                    and all(child.poll() is not None for child in samplers)):
                if any(child.returncode!=0 for child in samplers):
                    raise RuntimeError('steady_sampler_failed')
                if source_stats_before is not None:
                    # Bounds cover dispatch through observed Popen completion,
                    # not exact SF sample timestamps or continuous content.
                    end_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                    report['sampler_Popens_completion_observed_MONOTONIC_ns'] = end_ns
                    # A playing-window UI dump exceeded its fixed command
                    # budget in a real source-only probe. Pause first, outside
                    # the sampler dispatch/completion bounds; no larger timeout.
                    report['source_pause_receipt'] = source_window_control.transition('adb', args.guest,
                        source_stats_before['identity'], 'paused')
                    source_stats_after = owner_source_stats_gate.collect_fresh('adb', args.guest,
                        source_stats_before['identity'], 'aqz-KE-bpKQ', required_state='paused',
                        listener_ready_ns=args.source_listener_monotonic_ns)
                    report['source_Stats_after'] = source_stats_after
                    match = owner_source_stats_gate.endpoint_match(source_stats_before, source_stats_after,
                        window_started_ns=report['sampler_dispatch_started_MONOTONIC_ns'], window_finished_ns=end_ns)
                    report['source_Stats_endpoint_match'] = match
                    if not match['endpoint_content_matches']:
                        raise RuntimeError('source_Stats_endpoint_mismatch')
                uid=app_uid()
                if not uid.isdigit():
                    raise ValueError('isolated_App_uid_readback')
                done=PRIVATE+'udp-ui-phase-steady-sampled';staged=done+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+done)
                report['steady_samplers_completed_before_leave']=True
            if not report['touch_source_switched'] and root('test -f '+PRIVATE+'udp-ui-phase-ready-touch',False).returncode==0:
                adb(args.guest,'am force-stop local.huoguo.touchreceipt')
                adb(args.guest,'am start -n local.huoguo.touchreceipt/.TouchReceiptActivity')
                focus_deadline=time.monotonic()+4
                while time.monotonic()<focus_deadline:
                    focus=adb(args.guest,'dumpsys window | sed -n "/mCurrentFocus=/p"').stdout
                    if 'local.huoguo.touchreceipt/' in focus and 'TouchReceiptActivity' in focus:break
                    time.sleep(.25)
                else:raise RuntimeError('guest_receipt_not_focused')
                # Focus can precede a usable fullscreen input window. This is
                # test setup, outside the media sampling window.
                time.sleep(4)
                uid=app_uid()
                if not uid.isdigit():
                    raise ValueError('isolated_App_uid_readback')
                ready=PRIVATE+'udp-ui-phase-touch-ready'
                staged=ready+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+ready)
                report['touch_source_switched']=True
            if args.touch_mode in ('adb','kernel') and not adb_tap_done and root('test -f '+PRIVATE+'udp-ui-phase-adb-tap',False).returncode==0:
                raw=root('cat '+PRIVATE+'udp-ui-phase-adb-tap').stdout
                if len(raw)>64:raise ValueError('test_coordinate_file_bound')
                x,y=json.loads(raw)
                if type(x)!=int or type(y)!=int or not 0<x<10000 or not 0<y<10000:raise ValueError('test_coordinate_bound')
                if args.touch_mode=='adb':
                    report['adb_touch_command_exit']=adb(args.phone,'input touchscreen -d 0 tap '+str(x)+' '+str(y),False).returncode
                else:
                    if args.phone!='f7fc9469':raise ValueError('kernel_test_dedicated_phone_only')
                    capability=root('getevent -lp /dev/input/event8').stdout
                    if not ('"touchpanel"' in capability and 'max 23040' in capability and 'max 50688' in capability and 'ABS_MT_SLOT' in capability and 'max 9' in capability):
                        raise ValueError('kernel_touch_capability_mismatch')
                    device='/dev/input/event8'
                    def send(rows):
                        command='; '.join('sendevent '+device+' '+str(t)+' '+str(c)+' '+str(v) for t,c,v in rows)
                        root(command)
                    def release():
                        send([(3,47,0),(3,57,-1),(3,47,1),(3,57,-1),(1,330,0),(1,325,0),(0,0,0)])
                    try:
                        # Fixed calibrated root test only: evdev -> InputReader
                        # -> phone Window -> UDP. No physical-finger latency claim.
                        send([(3,47,0),(3,57,54320),(3,53,x*16),(3,54,y*16),(3,48,30),(3,49,30),(1,330,1),(1,325,1),(0,0,0)])
                        time.sleep(.08);release();time.sleep(.2)
                        send([(3,47,0),(3,57,54321),(3,53,(x-60)*16),(3,54,y*16),(3,48,30),(3,49,30),(1,330,1),(1,325,1),(0,0,0)])
                        time.sleep(.08)
                        send([(3,47,1),(3,57,54322),(3,53,(x+60)*16),(3,54,y*16),(3,48,30),(3,49,30),(0,0,0)])
                        time.sleep(.08)
                        send([(3,47,0),(3,53,(x-55)*16),(3,54,(y+5)*16),(3,47,1),(3,53,(x+55)*16),(3,54,(y+5)*16),(0,0,0)])
                        time.sleep(.08)
                    finally:release()
                    report['kernel_touch_two_contacts_attempted']=True
                done=PRIVATE+'udp-ui-phase-adb-tap-done';staged=done+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+done)
                adb_tap_done=True
            time.sleep(.5)
        if proc.poll() is None:
            report['timeout']=True
            raise TimeoutError('instrumentation_timeout')
        stdout,stderr=proc.communicate(timeout=5)
        instrumentation_reaped = True
        report['instrumentation_exit_code']=proc.returncode
        marker='INSTRUMENTATION_RESULT: numeric_result='
        numeric=next((line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)),None)
        if numeric is not None and len(numeric)<=65536:
            report['ui_result']=json.loads(numeric)
        else:
            report['numeric_result_missing']=True
        ui_result = report.get('ui_result')
        if isinstance(ui_result, dict) and ui_result.get('bounded_failure_label') == 'existing_UI_attempt_busy':
            # The normal UI helper declined before owning an attempt. Its
            # failure must not turn into a force-stop of somebody else's App.
            report['existing_phone_UI_attempt_busy_skip'] = True
            raise RuntimeError('existing_UI_attempt_busy')
        if (isinstance(ui_result, dict) and ui_result.get('bounded_failure_label') in ('saved_UI_credential_unavailable',
                    'saved_UI_account_unavailable', 'saved_UI_route_unverified', 'saved_UI_restore_unavailable')):
            report['saved_UI_unavailable_skip'] = True
            report['saved_UI_declined_before_owned_attempt'] = ui_result.get('helper_owned_attempt_started') is False
            raise RuntimeError(ui_result['bounded_failure_label'])
        stages = ('first',) if args.source_input == 'pause-only' else ('first', 'second')
        report['v50_profile_readback_verified']=verify_v50_profile_readback(report.get('ui_result'),args.v50_profile=='on',stages=stages)
        report['credential_save_readback_verified']=verify_credential_save_readback(report.get('ui_result'),args.credential_save=='on')
        report['credential_source_readback_verified']=verify_credential_source_readback(report.get('ui_result'),args.credential_source,stages=stages)
        if source_native is not None:
            ui_source = report.get('ui_result', {})
            report['authenticated_source_phases_complete'] = (source_native.completed ==
                ([1] if args.source_input == 'pause-only' else [1, 2])
                and source_authenticated_driver.helper_readback(ui_source, mode=args.source_input))
            if not report['authenticated_source_phases_complete']:
                raise source_authenticated_driver.Rejected('source_input_transition_unverified')
        if args.source_input == 'pause-only':
            report['source_pause_recovery_verified'] = verify_pause_recovery(
                report.get('ui_result'), args, report)
            report['steady_media_progress_verified'] = None
            report['steady_window_readback_verified'] = None
            report['reconnect_acceptance_exercised'] = False
            if not report['source_pause_recovery_verified']:
                raise source_authenticated_driver.Rejected('source_input_transition_unverified')
            result = root('cat '+PRIVATE+'udp-app-last-report.json', False)
            if result.returncode != 0 or result.stderr or not 0 < len(result.stdout.encode('utf-8')) <= 65536:
                raise RuntimeError('source_recovery_report_unverified')
            actual = json.loads(result.stdout)
            if type(actual) is not dict or type(actual.get('audio_cleanup_confirmed')) is not int or actual['audio_cleanup_confirmed'] != 1:
                raise RuntimeError('source_recovery_audio_cleanup_unverified')
            (args.output/'App-report.json').write_text(json.dumps(actual,indent=2)+'\n')
            report['App_report_read'] = True
            report['App_actual_json_bytes'] = len(result.stdout.encode('utf-8'))
            report['source_recovery_audio_cleanup_verified'] = True
        else:
            report['exit_confirmation_readback_verified']=verify_exit_confirmation_readback(report.get('ui_result'))
            report['surface_submit_execution_verified']=verify_surface_submit_readback(report.get('ui_result'),args.surface_submit_lead_ms)
            report['stage_diagnostics_readback_verified']=verify_stage_diagnostics_readback(report.get('ui_result'),args.stage_diagnostics=='on')
            report['codec_startup_readback_verified']=verify_codec_startup_readback(report.get('ui_result'),args.codec_startup=='on')
            report['steady_media_progress_verified']=verify_steady_media_progress(report.get('ui_result'))
            report['steady_window_readback_verified']=verify_steady_window_readback(report.get('ui_result'),args.steady_seconds)
            if args.network_scope == 'nps_owner':
                report['nps_physical_network_binding_verified']=verify_nps_physical_network_binding(report.get('ui_result'))
                report['nps_network_profile_readback_verified'] = verify_nps_network_readback(
                    report.get('ui_result'), args.node)
            reports=[('App',args.phone,PRIVATE+'udp-app-last-report.json'),
                     ('App-first',args.phone,PRIVATE+'udp-app-first-report.json')]
            if not args.media_only:
                reports.append(('guest_touch',args.guest,'/data/user/0/local.huoguo.touchreceipt/files/touch-receipt.json'))
            else:
                report['guest_touch_not_exercised']=True
            for name,serial,path in reports:
                result=root('cat '+path,False) if serial==args.phone else adb(serial,'run-as local.huoguo.touchreceipt cat files/touch-receipt.json',False)
                if result.returncode==0 and 0<len(result.stdout)<=65536:
                    report[name+'_actual_json_bytes']=len(result.stdout.encode('utf-8'))
                    (args.output/(name+'-report.json')).write_text(json.dumps(json.loads(result.stdout),indent=2)+'\n')
                    report[name+'_report_read']=True
            if not report['v50_profile_readback_verified']:
                raise RuntimeError('v50_profile_readback_unverified')
            if not report['credential_save_readback_verified']:
                raise RuntimeError('credential_save_readback_unverified')
            if not report['credential_source_readback_verified']:
                raise RuntimeError('credential_source_readback_unverified')
            if not report['exit_confirmation_readback_verified']:
                raise RuntimeError('exit_confirmation_readback_unverified')
            if args.network_scope == 'nps_owner' and not report['nps_physical_network_binding_verified']:
                raise RuntimeError('nps_physical_network_binding_unverified')
            if not report['surface_submit_execution_verified']:
                raise RuntimeError('surface_submit_readback_unverified')
            if args.network_scope == 'nps_owner' and not report['nps_network_profile_readback_verified']:
                raise RuntimeError('nps_network_profile_readback_unverified')
            if not report['steady_window_readback_verified']:
                raise RuntimeError('steady_window_readback_unverified')
            if not report['stage_diagnostics_readback_verified']:
                raise RuntimeError('stage_diagnostics_readback_unverified')
            if not report['codec_startup_readback_verified']:
                raise RuntimeError('codec_startup_readback_unverified')
            if not report['steady_media_progress_verified']:
                raise RuntimeError('steady_media_progress_stalled_or_unverified')
            if source_gate is not None and not report['source_first_capture_freshness_verified']:
                raise owner_source_gate.SourceGateError('source_first_capture_missing')
    except (Exception, KeyboardInterrupt) as failure:
        report['driver_failure_class']=type(failure).__name__
        labels = {'formal_gate_failed','formal_session_active','private_login_missing',
                  'target_App_process_active_skip','target_App_process_check_unavailable_skip',
                  'existing_UI_attempt_busy','saved_UI_credential_unavailable','saved_UI_account_unavailable',
                  'saved_UI_route_unverified','saved_UI_restore_unavailable',
                  'instrumentation_timeout','guest_receipt_not_focused',
                  'isolated_App_uid_readback','test_coordinate_file_bound',
                  'test_coordinate_bound','kernel_test_dedicated_phone_only',
                  'kernel_touch_capability_mismatch','surface_submit_readback_unverified',
                  'steady_sampler_failed','steady_window_readback_unverified','stage_diagnostics_readback_unverified',
                  'steady_media_progress_stalled_or_unverified','codec_startup_readback_unverified',
                  'nps_network_profile_readback_unverified','exit_confirmation_readback_unverified',
                  'nps_physical_network_binding_unverified','credential_save_readback_unverified',
                  'credential_source_readback_unverified',
                  'v50_profile_readback_unverified'}
        labels.update(owner_source_gate.SOURCE_GATE_LABELS, TRACE_LABELS)
        labels.update(owner_source_stats_gate.LABELS)
        labels.update(source_window_control.LABELS)
        labels.update(('source_Stats_endpoint_mismatch', 'source_recovery_report_unverified',
                       'source_recovery_audio_cleanup_unverified', 'snapshot_selection_rejected'))
        labels.update(source_authenticated_driver.LABELS)
        if str(failure) in labels:report['driver_failure_label']=str(failure)
    finally:
        failed='driver_failure_class' in report
        if source_stats_before is not None and 'source_pause_receipt' not in report:
            # Still under the caller's continuous admission lease. This routine
            # independently rechecks identity/focus/session before any keyevent.
            attempt_cleanup(report, 'pause_owned_source_window',
                lambda: report.update(source_pause_receipt=source_window_control.transition(
                    'adb', args.guest, source_stats_before['identity'], 'paused')))
        if (source_native is None and proc is not None and failed and not report.get('existing_phone_UI_attempt_busy_skip')
                and not report.get('saved_UI_unavailable_skip')):
            # Terminating the local adb client alone does not end Android
            # instrumentation. Stop only these two known isolated packages.
            for package in ('local.huoguo.lanuitest','local.remoteandroid.direct.experiment'):
                attempt_cleanup(report, 'stop_'+package,
                                lambda package=package: adb(args.phone,'am force-stop '+package,False))
        if proc is not None and not instrumentation_reaped:
            # Native source failures let the helper's bounded wait expire and
            # its finally cancel only the captured Attempt. Never force-stop a
            # later UI session to compensate for an observer failure.
            cleanup_output = reap_owned_process(proc, report, 'instrumentation',
                args.steady_seconds+25 if source_native is not None and failed else 2,
                terminate=failed and source_native is None)
            report['instrumentation_exit_code']=proc.returncode
            if failed:
                try:
                    report['instrumentation_cleanup_diagnostic'] = instrumentation_cleanup_diagnostic(cleanup_output)
                except Exception as failure:
                    record_cleanup_failure(report, 'instrumentation_cleanup_diagnostic', failure=failure)
        for sampler in samplers:
            reap_owned_process(sampler, report, 'sampler', 1 if failed else args.steady_seconds+5, terminate=failed)
            report.setdefault('sampler_exit_codes',[]).append(sampler.returncode)
        if snapshot_selection is not None:
            report['direct_source_snapshot'] = snapshot_selection.status()
            if not report['direct_source_snapshot']['remote_scope_clear_verified']:
                record_cleanup_failure(report, 'source_snapshot_remote_scope_unconfirmed')
        if source_reader is not None:
            try:
                source_reader.close()
            except Exception as failure:
                record_cleanup_failure(report, 'close_source_trace_prefix', failure=failure)
        if source_markers is not None:
            cleanup = source_markers.cleanup()
            report['authenticated_source_marker_cleanup'] = cleanup
            if not cleanup['owned_marker_cleanup_confirmed']:
                record_cleanup_failure(report, 'owned_source_markers', failure=RuntimeError('source_marker_cleanup'))
        cleanup_paths = ([PRIVATE+'udp-test-login.json'] if args.credential_source == 'private-file' else [])
        # A pre-instrument saved-mode refusal owns no App markers or input.
        if args.credential_source == 'private-file' or proc is not None:
            cleanup_paths.extend(PRIVATE+f for f in flags)
            attempt_cleanup(report, 'remove_private_test_input' if args.credential_source == 'private-file' else 'remove_owned_phase_markers',
                            lambda: root('rm -f '+' '.join(cleanup_paths),False))
    (args.output/'ui-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 1 if 'driver_failure_class' in report or report.get('cleanup_failures') else 0


if __name__=='__main__':
    sys.exit(main())
