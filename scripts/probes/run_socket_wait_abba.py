#!/usr/bin/env python3
"""Bounded real-video ABBA blocks, retaining failed trials and CPU limits.

Only socket timed waiting changes. Requires the external encoder/packetizer
from the current testbed and the isolated experiment APK/probe already installed.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes import overnight_suite as suite
from scripts.probes.compare_udp_iterations import surface_window
from scripts.probes.source_probe_guards import require_no_source_capture
from scripts.probes.udp_event_coverage import coverage


def phone_metadata():
    script = 'for p in 0 2 5 7; do cat /sys/devices/system/cpu/cpufreq/policy$p/scaling_max_freq /sys/devices/system/cpu/cpufreq/policy$p/scaling_cur_freq; done'
    numbers = [int(x) for x in suite.shell(suite.PHONE, 'su -c '+shlex.quote(script)).split()]
    battery = suite.shell(suite.PHONE, 'dumpsys battery')
    temp = re.search(r'^\s*temperature:\s*(\d+)\s*$', battery, re.M)
    return {'cpu_limits_written': False,
            'cpu_policies': {str(p): {'max_khz': numbers[2*i], 'current_khz': numbers[2*i+1]}
                             for i, p in enumerate((0,2,5,7))} if len(numbers) == 8 else None,
            'battery_temperature_deci_c': int(temp[1]) if temp else None}


def require_formal_idle():
    require_no_source_capture()
    r = subprocess.run(['lsof', '-nP', '-iTCP:15556', '-sTCP:ESTABLISHED'],
                       capture_output=True, text=True, timeout=5)
    if r.returncode not in (0, 1) or r.stdout.strip():
        raise RuntimeError('formal_connection_active_or_unknown')


def observed_run(command, *, timeout, observer_path=None):
    """Read CPU limits every five seconds without ever writing phone policies.

    A readback is non-atomic and not a continuous lock guarantee. This adds the
    same bounded ADB observer to both conditions; retain errors as evidence.
    """
    if timeout <= 0:
        raise ValueError('Positive observer timeout required')
    if observer_path is not None:
        observer_path = Path(observer_path)
        if observer_path.exists():
            raise RuntimeError('Preserve existing CPU observer evidence')
    samples = []
    evidence = {'scope': 'Host-timed read-only CPU ceiling observation, not a continuous policy lock',
                'target_interval_seconds': 5, 'deadline_seconds': timeout,
                'max_khz_reads_nonatomic': True, 'cpu_limits_written': False,
                'complete': False, 'status': 'starting', 'samples': samples,
                'child_cleanup_scope': 'Only owned parent Popen is stopped here; bounded child runner owns its cleanup'}

    def persist():
        if observer_path is not None:
            # A process interruption must not truncate the last saved snapshot.
            temporary = observer_path.with_name(observer_path.name+'.tmp')
            suite.save(temporary, evidence)
            temporary.replace(observer_path)

    script = 'for p in 0 2 5 7; do cat /sys/devices/system/cpu/cpufreq/policy$p/scaling_max_freq; done'
    start = time.monotonic()
    next_sample = start+5
    process = None
    persist()
    try:
        process = subprocess.Popen([str(x) for x in command], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        evidence['status'] = 'observing'
        while True:
            now = time.monotonic()
            remaining = timeout-(now-start)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                returncode = process.wait(timeout=min(max(0, next_sample-now), remaining))
                evidence.update(complete=True, status='process_finished', exit_code=returncode)
                persist()
                return returncode, samples
            except subprocess.TimeoutExpired:
                remaining = timeout-(time.monotonic()-start)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                row = {'host_before_monotonic_ns': time.clock_gettime_ns(time.CLOCK_MONOTONIC),
                       'scheduled_from_start_seconds': next_sample-start}
                try:
                    numbers = [int(x) for x in suite.shell(suite.PHONE, 'su -c '+shlex.quote(script),
                                                          timeout=min(5, remaining)).split()]
                    if len(numbers) != 4 or any(value <= 0 for value in numbers):
                        raise ValueError('Unexpected CPU limit values')
                    row['max_khz'] = {str(p): numbers[i] for i, p in enumerate((0, 2, 5, 7))}
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
                    row['failure_type'] = type(error).__name__
                row['host_after_monotonic_ns'] = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                samples.append(row)
                persist()
                next_sample += 5
                # Skip missed slots rather than creating burst ADB load after a
                # slow read. Exact host-before/after times remain in the report.
                while next_sample <= time.monotonic():
                    next_sample += 5
    except BaseException as error:
        evidence.update(status='failed', failure_type=type(error).__name__)
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        evidence['owned_parent_reap_timed_out'] = True
            evidence['owned_parent_exit_code'] = process.poll()
        error.cpu_limit_samples = list(samples)
        persist()
        raise


def trial_summary(path):
    report = json.loads(path.read_text())
    phone = report.get('phone', {})
    result = {'pacing_configuration': report.get('pacing_configuration'),
              'hardware_encoder_readback': report.get('hardware_encoder_readback'),
              'socket_pacing': report.get('socket_pacing'),
              'socket_video_counts': report.get('socket_video', {}).get('counts'),
              'event_coverage': coverage(report),
              'source_capture': report.get('capture_trace_analysis'),
              'audio': {key: phone.get('udp_audio', {}).get(key) for key in
                        ('pcm_written_bytes','failure_class','worker_queue_drops','worker_late_drops','pcm_late_drops','assembly_expired')},
              'phone_steady_5_to_110_s': {'available': False},
              'source_steady_5_to_110_s': {'available': False}}
    for role in ('phone', 'source'):
        p = path.with_name(path.stem+'-'+role+'-surface.json')
        if not p.exists():
            continue
        sf = json.loads(p.read_text())
        origin = phone.get('first_server_packet_ns') if role == 'phone' else next(iter(sf.get('presentation_ns', [])), None)
        # Each role's own monotonic domain. The source's first observed present
        # is an independent anchor; never subtract it from a phone timestamp.
        value = surface_window(sf, origin, 5, 110)
        value['origin'] = 'phone_first_authenticated_packet' if role == 'phone' else 'source_first_observed_present'
        result[role+'_steady_5_to_110_s'] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--blocks', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-size', type=int, choices=(1280, 1920), default=1280)
    parser.add_argument('--video-bitrate', type=int, choices=(8000000, 12000000), default=8000000)
    parser.add_argument('--packetizer', type=Path, default=suite.PACKETIZER,
                        help='Explicit separate native binary; preflight records its SHA-256')
    args = parser.parse_args()
    suite.PACKETIZER = args.packetizer.resolve(strict=True)
    out = args.output_dir.resolve()
    if (out/'campaign.json').exists() or (out/'preflight-live.json').exists():
        parser.error('Preserve existing evidence; select a new directory')
    out.mkdir(parents=True, exist_ok=True)
    suite.OUT = out
    require_formal_idle()
    pre = suite.preflight()
    bind = pre['host_ipv4']
    peer = pre['devices'][suite.PHONE]['wifi_ipv4'][0]
    campaign = {'scope': 'Real M1 Morphe YouTube 1080p60 UI format to user-limited OnePlus12 over physical LAN AES-GCM UDP; not WAN/V50',
                'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'order': ['A','B','B','A'] * args.blocks,
                'A': 'socket_wait_enabled=true', 'B': 'socket_wait_enabled=false',
                'fixed': {'socket_guard': True, 'native_wire_bps': 32000000,
                          'video_bps': args.video_bitrate, 'encoded_long_edge': args.max_size,
                          'buffer_ms': 80, 'raw_native_phone_fps_cap': 120,
                          'native_and_socket_catchup_bytes': 2048, 'audio': True, 'touch': True},
                'cpu_observer': {'interval_seconds': 5, 'read_only': True,
                                 'max_khz_reads_nonatomic': True, 'applies_to_both_conditions': True},
                'runs': [], 'completed': False}
    suite.save(out/'campaign.json', campaign)
    for number, condition in enumerate(campaign['order'], 1):
        dest = out/f'{number:02}-{condition}'
        command = [sys.executable, ROOT/'scripts/probes/run_surface_hint_matrix.py',
                   '--experimental-client', '--serial', suite.PHONE, '--phone-label', 'OnePlus12 user CPU limited',
                   '--bind-ip', bind, '--peer-ip', peer, '--interface', 'en7',
                   '--fps', '120', '--display-hz', '120', '--hints', '120', '--buffers', '80',
                   '--raw-policies', 'fifo', '--surface-submit-leads', '0', '--raw-submit-fps', '120',
                   '--packetizer', suite.PACKETIZER, '--encoder', suite.ENCODER,
                   '--max-size', str(args.max_size), '--video-bitrate', str(args.video_bitrate), '--wire-bitrate', '32000000',
                   '--rounds', '1', '--seconds', '120', '--source-warmup-seconds', '15',
                   '--source-quality-label', '1080p60-ui', '--verify-source-quality',
                   '--restart-source', '--output-dir', dest]
        if condition == 'B':
            command.append('--disable-socket-wait')
        row = {'index': number, 'condition': condition, 'valid': False,
               'cpu_limit_samples': [], 'cpu_observer_report': str(dest/'cpu-limit-observer.json')}
        try:
            row['phase'] = 'formal_idle_guard'
            require_formal_idle()
            row['phase'] = 'phone_before'
            row['phone_before'] = phone_metadata()
            row['phase'] = 'observed_process'
            returncode, cpu_samples = observed_run(command, timeout=320,
                                                   observer_path=dest/'cpu-limit-observer.json')
            row.update(exit_code=returncode, cpu_limit_samples=cpu_samples, phase='phone_after')
            row['phone_after'] = phone_metadata()
            row['phase'] = 'readback_validation'
            if (dest/'matrix.json').exists():
                matrix = json.loads((dest/'matrix.json').read_text())
                trials = matrix.get('runs', [])
                row['source_unchanged'] = matrix.get('source_unchanged')
                row['trial_validation'] = trials
                if len(trials) == 1 and trials[0].get('report'):
                    row['summary'] = trial_summary(Path(trials[0]['report']))
            config = row.get('summary', {}).get('pacing_configuration') or {}
            row['isolated_wait_verified'] = (config.get('socket_wait_enabled') == (condition == 'A')
                and config.get('socket_deadline_reference_guard_enabled') is True
                and config.get('socket_reservation_and_serialization_checks_enabled') is True
                and config.get('native_wire_budget_readback_matches') is True)
            row['steady_windows_complete'] = all(
                row.get('summary', {}).get(role+'_steady_5_to_110_s', {}).get('available') is True
                and row.get('summary', {}).get(role+'_steady_5_to_110_s', {}).get('timestamps_cover_entire_window') is True
                for role in ('phone', 'source'))
            row['matrix_playback_verified'] = (len(row.get('trial_validation', [])) == 1
                and row['trial_validation'][0].get('valid_playback_test') is True
                and row['trial_validation'][0].get('valid_real_video_test') is True)
            row['valid'] = (returncode == 0 and row.get('source_unchanged') is True
                            and row['isolated_wait_verified'] and row['steady_windows_complete']
                            and row['matrix_playback_verified'])
            row['phase'] = 'finished'
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as error:
            row.update(valid=False, failure_type=type(error).__name__)
            if hasattr(error, 'cpu_limit_samples'):
                row['cpu_limit_samples'] = error.cpu_limit_samples
        campaign['runs'].append(row)
        suite.save(out/'campaign.json', campaign)
        print(json.dumps({'finished': number, 'condition': condition, 'valid': row['valid'],
                          'phone_steady': row.get('summary', {}).get('phone_steady_5_to_110_s')}), flush=True)
        if not row['valid']:
            print('Stopped: invalid trial retained; no automatic retries or production changes', flush=True)
            return 1
    campaign['completed'] = True
    campaign['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    suite.save(out/'campaign.json', campaign)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
