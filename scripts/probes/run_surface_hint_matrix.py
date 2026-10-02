#!/usr/bin/env python3
"""Interleaved real-video hint-only UDP comparison; no production deployment."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hardware_stream import experimental_raw_submit_arguments, verify_raw_submit_budget_readback
from scripts.probes.source_playback_state import collect
from scripts.probes.compare_udp_iterations import summarize
from scripts.probes.analyze_udp_stalls import analyze
from scripts.probes.source_probe_guards import require_no_source_capture
from scripts.probes.source_player_quality import collect as collect_quality

PLAYBACK_DURATION_TOLERANCE_S = 1.0


def fingerprints():
    paths = ['hardware_stream.py', 'scripts/probes/emulator_hardware_encoder.swift',
             'experiments/nps-transport/phone/UdpVideoProbe.java',
             'app/src/main/java/local/remoteandroid/direct/MainActivity.java',
             'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
             'experiments/moonlight-v2/transport/android-udp/run_phone_udp.py',
             'experiments/moonlight-v2/transport/android-udp/udp_session_sender.py',
             'experiments/moonlight-v2/transport/android-udp/feedback_controller.py',
             'scripts/probes/capture_trace_analysis.py', 'scripts/probes/run_surface_hint_matrix.py']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths}


def client_artifacts(experimental_client=False):
    return {'client_package': ('local.remoteandroid.direct.experiment' if experimental_client
                               else 'local.remoteandroid.direct'),
            'probe_package': ('local.remoteandroid.phoneprobe.experiment' if experimental_client
                              else 'local.remoteandroid.phoneprobe'),
            'probe_apk': ROOT/'experiments/nps-transport/phone/build'/
                         ('experimental/phoneprobe.apk' if experimental_client else 'phoneprobe.apk')}


def matched_experimental_report(report, client):
    return (isinstance(report, dict) and report.get('experimental_client') is True
            and report.get('client_package') == client['client_package']
            and report.get('probe_package') == client['probe_package']
            and report.get('instrumentation_target_verified') is True
            and report.get('surface_submit_report_verified') is True
            and report.get('valid_playback_test') is not False and isinstance(report.get('phone'), dict))


def raw_submit_experiment_verified(report, client, fps, raw_submit_fps):
    """Require worker configuration and unchanged native/phone FPS for this trial."""
    if raw_submit_fps is None:
        return None
    if not matched_experimental_report(report, client) or report.get('experimental_encoder') is not True:
        return False
    if (type(report.get('requested_raw_submit_fps')) is not int
            or report['requested_raw_submit_fps'] != raw_submit_fps
            or type(report.get('requested_encoder_fps')) is not int
            or report['requested_encoder_fps'] != fps):
        return False
    try:
        verify_raw_submit_budget_readback(report.get('raw_submit_budget_readback'), fps, raw_submit_fps)
    except ValueError:
        return False
    value = report['raw_submit_budget_readback']
    phone_fps = report['phone'].get('fps_limit')
    return (type(phone_fps) is int and phone_fps == fps
            and type(value.get('phone_fps_limit')) is int and value['phone_fps_limit'] == fps)


def playback_validation(report, requested_seconds):
    """Validate completion on the phone clock; host cleanup and SF cannot pad it."""
    reasons = []
    coverage = {'requested_seconds': requested_seconds, 'tolerance_s': PLAYBACK_DURATION_TOLERANCE_S,
                'observed_seconds': None, 'basis': None, 'complete': False}
    if not isinstance(report, dict):
        return {'passed': False, 'reasons': ['missing_or_invalid_child_report'], 'coverage': coverage}
    if report.get('valid_playback_test') is False:
        reasons.append('child_report_marked_invalid')
    if report.get('report_failure_class') not in (None, ''):
        reasons.append('child_report_failure')
    if not isinstance(report.get('host_failures'), list):
        reasons.append('missing_or_invalid_host_failure_readback')
    elif report['host_failures']:
        reasons.append('host_failures_present')
    phone = report.get('phone')
    if not isinstance(phone, dict):
        reasons.append('missing_phone_report')
        return {'passed': False, 'reasons': reasons, 'coverage': coverage}
    for key, reason in (('failure_class', 'phone_failure'),
                        ('video_worker_failure_class', 'video_worker_failure')):
        if phone.get(key) not in (None, ''):
            reasons.append(reason)
    audio = phone.get('udp_audio')
    if isinstance(audio, dict) and audio.get('failure_class') not in (None, ''):
        reasons.append('audio_worker_failure')
    for key in ('video_worker_alive', 'video_worker_join_timed_out'):
        if phone.get(key) is not None and phone.get(key) is not False:
            reasons.append(key)
    if phone.get('running_at_end') is not True:
        reasons.append('phone_not_running_at_end')
    if type(phone.get('requested_seconds')) is not int or phone['requested_seconds'] != requested_seconds:
        reasons.append('phone_requested_duration_mismatch')
    if any(type(phone.get(key)) is not int or phone[key] <= 0 for key in
           ('received_media_frames', 'queued_media_frames', 'codec_callback_count')):
        reasons.append('missing_video_progress')
    first = phone.get('first_server_packet_ns')
    end = phone.get('receive_end_ns')
    # Prefer the receive window. A long teardown observation must never rescue
    # a receive window that ended early. Older reports may omit receive_end_ns.
    if 'receive_end_ns' not in phone or (type(end) is int and end == 0):
        end = phone.get('observation_end_ns')
        coverage['basis'] = 'phone_first_packet_to_observation_end'
    else:
        coverage['basis'] = 'phone_first_packet_to_receive_end'
    if type(first) is not int or first <= 0 or type(end) is not int or end <= first:
        reasons.append('missing_or_invalid_phone_monotonic_window')
    else:
        coverage['observed_seconds'] = round((end-first)/1_000_000_000, 6)
        coverage['complete'] = end-first >= int((requested_seconds-PLAYBACK_DURATION_TOLERANCE_S)*1_000_000_000)
        if not coverage['complete']:
            reasons.append('phone_observation_too_short')
        observation_end = phone.get('observation_end_ns')
        if observation_end is not None and (type(observation_end) is not int or observation_end < end):
            reasons.append('invalid_phone_monotonic_order')
    return {'passed': not reasons, 'reasons': reasons, 'coverage': coverage}


def surface_measurement_status(report, companion):
    """Surface sampling is separate from whether the playback test completed."""
    surfaces = report.get('actual_surface_samples', {}) if isinstance(report, dict) else {}
    if not isinstance(surfaces, dict):
        surfaces = {}
    status = {}
    for role in ('source', 'phone'):
        value = surfaces.get(role)
        available = (isinstance(value, dict) and value.get('measurement_available') is not False
                     and value.get('timed_out') is not True
                     and type(value.get('presented_frames')) is int and value['presented_frames'] > 0
                     and type(value.get('seconds')) in (int, float) and value['seconds'] > 0
                     and type(value.get('display_vsync_ns')) is int and value['display_vsync_ns'] > 0)
        status[role+'_surface_fps'] = 'available' if available else 'measurement_missing'
    status['phone_surface_timeline'] = 'available' if companion.is_file() else 'measurement_missing'
    return status


def real_source_validation(before, after, report, requested_seconds=35):
    """A completed phone receive loop can be an idle-screen heartbeat test.

    Require independently observed player and video-layer progress before using
    it as a real-video comparison. This is not proof of a 60fps media file or
    continuous playback between the two snapshots.
    """
    reasons = []
    for phase, state in (('before', before), ('after', after)):
        if not (isinstance(state, dict) and state.get('command_ok') is True
                and state.get('unknown') is False and state.get('state_known') is True
                and type(state.get('active_sessions')) is int and state['active_sessions'] == 1
                and type(state.get('state')) is int
                and state['state'] == 3):
            reasons.append('source_playing_unverified_' + phase)
    surfaces = report.get('actual_surface_samples') if isinstance(report, dict) else None
    source = surfaces.get('source') if isinstance(surfaces, dict) else None
    if not (isinstance(source, dict) and source.get('measurement_available') is not False
            and source.get('timed_out') is not True
            and type(source.get('presented_frames')) is int and source['presented_frames'] > 0
            and type(source.get('seconds')) in (int, float) and math.isfinite(source['seconds'])
            and source['seconds'] >= requested_seconds - 4
            and type(source.get('display_vsync_ns')) is int and source['display_vsync_ns'] > 0):
        reasons.append('source_video_surface_progress_unverified')
    return {'passed': not reasons, 'reasons': reasons,
            'source_minimum_observation_seconds': requested_seconds - 4,
            'scope': 'Player state snapshots and source video-layer presents; not media-file FPS or optical display proof'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--encoder', type=Path, required=True)
    parser.add_argument('--serial', default='3B15AL00M9U00000',
                        help='Phone ADB serial; the video source remains emulator-5556')
    parser.add_argument('--phone-label', default='physical Android phone',
                        help='Requested test-device label recorded in evidence; not a device readback')
    parser.add_argument('--bind-ip', default='192.168.9.128')
    parser.add_argument('--peer-ip', default='192.168.9.6')
    parser.add_argument('--interface', choices=('en7', 'en0'), default='en7')
    parser.add_argument('--experimental-client', action='store_true',
                        help='Use the isolated matched experiment client and probe APK')
    parser.add_argument('--encoder-prioritize-speed', choices=('true', 'false'), default=None,
                        help='Optional VT speed/quality hint for the explicitly selected experimental encoder')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--rounds', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--seconds', type=int, choices=(35, 45, 60, 120), default=35)
    parser.add_argument('--drop-video-every', type=int, choices=(0, 50, 100), default=0,
                        help='Probe-only periodic 0/2/1 percent UDP video datagram loss')
    parser.add_argument('--exercise-touch', action='store_true', help='One bounded real phone swipe; results include source interaction')
    parser.add_argument('--disable-socket-pacing-bundle', action='store_true',
                        help='LAN experiment only: native pacing stays; socket pacing AND SocketVideoGate guard disabled together')
    parser.add_argument('--disable-socket-wait', action='store_true',
                        help='LAN single-variable experiment: skip only socket timed waits, '
                             'retaining deadline/reference guard and native pacing')
    parser.add_argument('--fps', type=int, choices=(60, 120), default=60,
                        help='Requested encoder FPS cap; source and displayed FPS require readback')
    parser.add_argument('--raw-submit-fps', type=int, choices=(30, 60, 120), default=None,
                        help='Independent experimental host raw submission budget; --fps stays unchanged '
                             'for encoder and phone, and gRPC sampling is unchanged')
    parser.add_argument('--display-hz', type=int, choices=(60, 90, 120), default=120)
    parser.add_argument('--buffer-ms', type=int, choices=(30, 60, 80, 100), default=60)
    parser.add_argument('--hints', nargs='+', type=int, choices=(0, 60, 120), default=[60, 0])
    parser.add_argument('--buffers', nargs='+', type=int, choices=(30, 60, 80, 100), default=None)
    parser.add_argument('--raw-policies', nargs='+', choices=('fifo','latest'), default=None)
    parser.add_argument('--surface-submit-leads', nargs='+', type=int, choices=(0, 8, 16), default=None)
    parser.add_argument('--no-trace', action='store_true')
    parser.add_argument('--restart-source', action='store_true',
                        help='Restart only the test player before each public-video intent; keep its data')
    parser.add_argument('--source-warmup-seconds', type=int, choices=(2, 8, 15), default=2)
    parser.add_argument('--wire-bitrate', type=int, default=16000000,
                        help='UDP wire pacing ceiling; independent of the video target')
    parser.add_argument('--video-bitrate', type=int, choices=(4000000, 8000000, 12000000, 16000000), default=4000000)
    parser.add_argument('--max-size', type=int, choices=(960, 1280, 1920), default=960,
                        help='Requested encoded long edge; actual dimensions come from encoder readback')
    parser.add_argument('--source-quality-label', choices=('unverified', '1080p60-ui', '720p60-ui'),
                        default='unverified', help='Observed player setting; not a decoded-frame FPS assertion')
    parser.add_argument('--verify-source-quality', action='store_true',
                        help='Read the actual player menu each run and require the selected 60FPS format')
    args = parser.parse_args()
    if args.disable_socket_wait and (not args.experimental_client or args.disable_socket_pacing_bundle):
        parser.error('--disable-socket-wait requires the isolated client and retained socket pacing guard')
    if args.disable_socket_pacing_bundle and not args.experimental_client:
        parser.error('Single native pacer experiment requires matched experimental client')
    try:
        experimental_raw_submit_arguments(args.encoder, args.raw_submit_fps, args.experimental_client)
    except ValueError as error:
        parser.error(str(error))
    if not 8000000 <= args.wire_bitrate <= 40000000:
        parser.error('wire bitrate must be between 8 and 40 Mbps')
    buffers = args.buffers or [args.buffer_ms]
    policies = args.raw_policies or [None]
    submit_leads = args.surface_submit_leads or [None]
    if any(lead is not None and lead > 0 for lead in submit_leads) and not args.experimental_client:
        parser.error('--surface-submit-leads > 0 requires --experimental-client')
    conditions = list(itertools.product(args.hints, buffers, policies, submit_leads))
    if len(conditions) > 4 or len(set(conditions)) != len(conditions):
        parser.error('At most four unique controlled conditions per matrix')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if (args.output_dir/'matrix.json').exists():
        parser.error('Existing evidence retained; choose a new output directory')
    adb = Path.home()/'Library/Android/sdk/platform-tools/adb'
    runner = ROOT/'experiments/moonlight-v2/transport/android-udp/run_phone_udp.py'
    client = client_artifacts(args.experimental_client)
    manifest = {'scope': f'Real M1 Morphe YouTube over encrypted UDP on physical LAN to {args.phone_label}; not WAN/V50',
                'source_serial': 'emulator-5556',
                'phone': {'requested_serial': args.serial, 'requested_label': args.phone_label},
                'transport': {'bind_ip': args.bind_ip, 'peer_ip': args.peer_ip, 'interface': args.interface},
                'experimental_client': args.experimental_client,
                'client_package': client['client_package'], 'probe_package': client['probe_package'],
                'conditions': {'requested_seconds': args.seconds,
                               'video_fps_cap': args.fps, 'video_bps': args.video_bitrate, 'wire_bps': args.wire_bitrate,
                               'requested_raw_submit_fps': args.raw_submit_fps,
                               'expected_effective_raw_submit_fps': (args.fps if args.raw_submit_fps is None
                                                                     else args.raw_submit_fps),
                               'requested_max_size': args.max_size, 'display_hz': args.display_hz, 'buffer_ms': args.buffer_ms,
                               'socket_wait_enabled_requested': not (args.disable_socket_wait or args.disable_socket_pacing_bundle),
                               'socket_guard_enabled_requested': not args.disable_socket_pacing_bundle,
                               'requested_encoder_prioritize_speed': (None if args.encoder_prioritize_speed is None
                                                                     else args.encoder_prioritize_speed == 'true'),
                               'capture_trace': not args.no_trace, 'hint_order_first_round': args.hints,
                               'buffer_order_first_round': buffers, 'raw_queue_order_first_round': policies,
                               'surface_submit_lead_order_first_round': submit_leads,
                               'real_media_file_fps_verified': False,
                               'source_restarted_each_run': args.restart_source,
                               'source_warmup_seconds': args.source_warmup_seconds,
                               'player_quality_setting_label': args.source_quality_label},
                'source_fingerprints_start': fingerprints(),
                'binaries': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                             [('encoder',args.encoder),('packetizer',args.packetizer),
                              ('probe', client['probe_apk'])]},
                'runs': []}
    for number in range(1, args.rounds+1):
        order = conditions if number % 2 else list(reversed(conditions))
        for hint, buffer_ms, policy, submit_lead in order:
            label = f'hint{hint}'
            if args.buffers is not None:
                label += f'-buffer{buffer_ms}'
            if policy is not None:
                label += f'-{policy}'
            if submit_lead is not None:
                label += f'-submit{submit_lead}'
            if args.encoder_prioritize_speed is not None:
                label += f'-speed{args.encoder_prioritize_speed}'
            if args.raw_submit_fps is not None:
                label += f'-rawsubmit{args.raw_submit_fps}'
            path = args.output_dir/f'{label}-{number:02}.json'
            if path.exists():
                raise RuntimeError('Existing experiment output is retained')
            # Fixed public source; keep all failures and verify numeric state.
            require_no_source_capture()
            if args.restart_source:
                subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'am', 'force-stop',
                                'app.morphe.android.youtube'], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            result = subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', shlex.join(['am', 'start',
                '-a', 'android.intent.action.VIEW', '-d', 'https://youtu.be/aqz-KE-bpKQ?t=60',
                '-p', 'app.morphe.android.youtube'])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            time.sleep(args.source_warmup_seconds)
            before = collect(adb, 'emulator-5556', 8)
            if before.get('state') in (1, 2):
                subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'input', 'keyevent', 'KEYCODE_MEDIA_PLAY'],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                time.sleep(1)
                before = collect(adb, 'emulator-5556', 8)
            quality = collect_quality(adb) if args.verify_source_quality else None
            if args.verify_source_quality and not (quality.get('known') is True and quality.get('fps') == 60):
                manifest['runs'].append({'source_format': quality, 'source_state_before': before,
                                         'valid_real_video_test': False, 'failure': 'source_60fps_quality_unverified'})
                manifest['matrix_failed'] = True
                (args.output_dir/'matrix.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
                return 1
            command = [sys.executable, str(runner), '--serial', args.serial, '--bind-ip', args.bind_ip, '--peer-ip', args.peer_ip,
                '--interface', args.interface, '--packetizer', str(args.packetizer), '--native-encoder', str(args.encoder),
                '--burst-bytes', '100000', '--burst-seconds', '.08', '--seconds', str(args.seconds), '--fps', str(args.fps),
                '--bitrate', str(args.video_bitrate), '--wire-bitrate', str(args.wire_bitrate), '--max-size', str(args.max_size), '--buffer', str(buffer_ms),
                '--display-hz', str(args.display_hz), '--content-hint-fps', str(hint),
                '--audio', '--touch', '--async-video', '--arrival-clock', '--adapt-budget', '--network-feedback',
                '--pacing-burst-bytes', '2048', '--sample-surfaces', '--diagnostic-events',
                '--source', f'Real public Morphe YouTube BBB requested seek60s; {args.source_quality_label}; requested cap{args.fps}; hint{hint}; M1 physical LAN to {args.phone_label}',
                '--output', str(path)]
            if not args.disable_socket_pacing_bundle:
                command.append('--socket-pacing')
            if args.disable_socket_wait:
                command.append('--disable-socket-wait')
            if args.drop_video_every:
                command.extend(['--drop-video-every', str(args.drop_video_every)])
            if args.exercise_touch:
                command.append('--exercise-touch')
            if not args.no_trace:
                command.append('--capture-trace')
            if args.experimental_client:
                command.append('--experimental-client')
            if policy is not None:
                command.extend(['--raw-queue-policy',policy])
            if submit_lead is not None:
                command.extend(['--surface-submit-lead-ms',str(submit_lead)])
            if args.encoder_prioritize_speed is not None:
                command.extend(['--encoder-prioritize-speed', args.encoder_prioritize_speed])
            if args.raw_submit_fps is not None:
                command.extend(['--raw-submit-fps', str(args.raw_submit_fps)])
            started = time.monotonic()
            run = subprocess.run(command, cwd=ROOT, timeout=args.seconds+100, stdout=subprocess.DEVNULL)
            row = {'hint_fps': hint, 'round': number, 'report': str(path), 'exit_code': run.returncode,
                   'buffer_ms': buffer_ms, 'raw_queue_policy': policy,
                   'surface_submit_lead_ms': submit_lead,
                   'experimental_client': args.experimental_client,
                   'client_package': client['client_package'], 'probe_package': client['probe_package'],
                   'requested_encoder_prioritize_speed': (None if args.encoder_prioritize_speed is None
                                                         else args.encoder_prioritize_speed == 'true'),
                   'requested_encoder_fps': args.fps, 'requested_raw_submit_fps': args.raw_submit_fps,
                   'source_prepare_exit': result.returncode, 'source_state_before': before,
                   'source_format': quality,
                   'source_state_after': collect(adb, 'emulator-5556', 8),
                   'elapsed_s': round(time.monotonic()-started,3)}
            if path.is_file():
                try:
                    report = json.loads(path.read_text())
                except (OSError, ValueError) as error:
                    report = None
                    row['report_read_failure_class'] = type(error).__name__
                matched = (matched_experimental_report(report, client) if args.experimental_client else True)
                row['matched_experimental_client_verified'] = matched if args.experimental_client else None
                raw_submit_verified = raw_submit_experiment_verified(
                    report, client, args.fps, args.raw_submit_fps)
                row['raw_submit_budget_verified'] = raw_submit_verified
                if raw_submit_verified is True:
                    row['raw_submit_budget_readback'] = dict(verify_raw_submit_budget_readback(
                        report['raw_submit_budget_readback'], args.fps, args.raw_submit_fps),
                        phone_fps_limit=report['phone']['fps_limit'])
                companion = path.with_name(path.stem+'-phone-surface.json')
                validation = playback_validation(report, args.seconds)
                if not matched:
                    validation['reasons'].append('failed_client_or_submission_readback')
                if raw_submit_verified is False:
                    validation['reasons'].append('failed_raw_submit_budget_readback')
                if run.returncode != 0:
                    validation['reasons'].append('child_exit_nonzero')
                validation['passed'] = not validation['reasons']
                row['playback_validation'] = validation
                row['valid_playback_test'] = validation['passed']
                source_validation = real_source_validation(row['source_state_before'],
                                                           row['source_state_after'], report, args.seconds)
                row['real_source_validation'] = source_validation
                row['valid_real_video_test'] = validation['passed'] and source_validation['passed']
                row['experiment_validation_status'] = ('passed_playback_acceptance' if validation['passed']
                                                        else 'failed_playback_acceptance')
                row['surface_measurement_status'] = surface_measurement_status(report, companion)
                row['missing_measurements'] = [name for name, status in row['surface_measurement_status'].items()
                                               if status == 'measurement_missing']
                if row['valid_real_video_test']:
                    row['summary'] = summarize(path)
                if row['valid_real_video_test'] and companion.is_file():
                    stalls = analyze(report, json.loads(companion.read_text()))
                    path.with_name(path.stem+'-stalls.json').write_text(json.dumps(stalls,indent=2)+'\n')
                    row['stalls_over_60ms'] = stalls['stalls_count']
            else:
                row['valid_playback_test'] = False
                row['experiment_validation_status'] = 'missing_child_report'
            manifest['runs'].append(row)
            manifest['matrix_failed'] = any(item['exit_code'] != 0 or item.get('valid_playback_test') is False
                                            or item.get('valid_real_video_test') is False
                                            or 'summary' not in item for item in manifest['runs'])
            manifest['source_fingerprints_latest'] = fingerprints()
            manifest['source_unchanged'] = manifest['source_fingerprints_start'] == manifest['source_fingerprints_latest']
            (args.output_dir/'matrix.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'hint': hint, 'round': number, 'exit_code': run.returncode,
                              'buffer_ms': buffer_ms, 'raw_queue_policy': policy,
                              'surface_submit_lead_ms': submit_lead,
                              'steady': row.get('summary',{}).get('phone_steady_5_to_30_s'),
                              'source_unchanged': manifest['source_unchanged']}), flush=True)
    return 1 if manifest['matrix_failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
