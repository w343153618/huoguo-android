#!/usr/bin/env python3
"""Serial bounded real-video UDP A/B matrix; does not change production services.

USB controls/reads the phone; the existing UDP runner pins the media socket to
the physical LAN interface. Each case opens the same public YouTube source and
retains failures. No passwords, private logs, image pixels or keys are exported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.source_playback_state import collect
from scripts.probes.compare_udp_iterations import summarize
from scripts.probes.analyze_udp_stalls import analyze

CASES = {'diagnostics': [], 'feedback': ['--network-feedback'],
         'paced': ['--socket-pacing'], 'combined': ['--network-feedback', '--socket-pacing'],
         'paced-credit': ['--socket-pacing', '--pacing-burst-bytes', '2048'],
         'combined-credit': ['--network-feedback', '--socket-pacing', '--pacing-burst-bytes', '2048'],
         'combined-credit-120': ['--network-feedback', '--socket-pacing', '--pacing-burst-bytes', '2048', '--display-hz', '120']}


def fingerprints():
    names = ['scripts/probes/run_udp_feedback_matrix.py', 'scripts/probes/analyze_udp_stalls.py',
             'experiments/moonlight-v2/transport/media_datagram.hpp',
             'experiments/moonlight-v2/transport/android-udp/h264_udp_packetizer.cpp',
             'experiments/moonlight-v2/transport/android-udp/run_phone_udp.py',
             'experiments/moonlight-v2/transport/android-udp/udp_session_sender.py',
             'experiments/moonlight-v2/transport/android-udp/feedback_controller.py']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--encoder', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--serial', default='3B15AL00M9U00000')
    parser.add_argument('--bind-ip', default='192.168.9.128')
    parser.add_argument('--peer-ip', default='192.168.9.6')
    parser.add_argument('--interface', choices=('en7', 'en0'), default='en7')
    parser.add_argument('--rounds', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--seconds', type=int, default=35)
    parser.add_argument('--cases', nargs='+', choices=tuple(CASES), default=['diagnostics','feedback','paced','combined'])
    parser.add_argument('--drop-video-every', type=int, default=0)
    parser.add_argument('--display-hz', type=int, choices=(60, 90, 120), default=90)
    parser.add_argument('--pacing-burst-bytes', type=int, choices=(0, 2048, 4096), default=0)
    args = parser.parse_args()
    if not 15 <= args.seconds <= 90 or args.drop_video_every != 0 and args.drop_video_every < 20:
        parser.error('Bounded video duration/loss injection required')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    adb = Path.home()/'Library/Android/sdk/platform-tools/adb'
    runner = ROOT/'experiments/moonlight-v2/transport/android-udp/run_phone_udp.py'
    runs = []
    manifest = {'scope': 'Real M1 public YouTube to real phone over physical WiFi LAN UDP; no WAN/V50 acceptance',
                'conditions': {'fps': 60, 'video_bps': 4_000_000, 'wire_budget_bps': 16_000_000,
                               'stream_size': [540, 960], 'guest_size_unchanged': True,
                               'buffer_ms': 60, 'burst_bytes': 100000, 'burst_seconds': .08,
                               'pacing_catchup_credit_bytes': args.pacing_burst_bytes,
                               'requested_display_hz': args.display_hz, 'video_drop_every': args.drop_video_every},
                'runs': runs}
    manifest['source_fingerprints_at_start'] = fingerprints()
    manifest['tested_binary_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                       for name, path in [('packetizer',args.packetizer),('encoder',args.encoder)]}
    # Sequential interleaving avoids silently testing all controls only before
    # all candidates. It is still not randomized or identical-byte playback.
    for number in range(1, args.rounds+1):
        order = args.cases if number % 2 else list(reversed(args.cases))
        for name in order:
            output = args.output_dir/(name+f'-{number:02}.json')
            if output.exists():
                raise SystemExit('Existing evidence retained: '+str(output))
            prepare = subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', shlex.join([
                'am', 'start', '-a', 'android.intent.action.VIEW', '-d',
                'https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=60s', '-p', 'app.morphe.android.youtube'])],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            time.sleep(2)
            before = collect(adb, 'emulator-5556', 8)
            if before.get('state') in (1, 2):
                subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'input keyevent KEYCODE_MEDIA_PLAY'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
                time.sleep(1)
                before = collect(adb, 'emulator-5556', 8)
            source = 'Real public Morphe YouTube BBB from requested 60s; '+name+'; physical WiFi LAN'
            command = [sys.executable, str(runner), '--serial', args.serial, '--bind-ip', args.bind_ip,
                       '--peer-ip', args.peer_ip, '--interface', args.interface,
                       '--packetizer', str(args.packetizer), '--native-encoder', str(args.encoder),
                       '--burst-bytes', '100000', '--burst-seconds', '.08', '--seconds', str(args.seconds),
                       '--fps', '60', '--bitrate', '4000000', '--wire-bitrate', '16000000',
                       '--max-size', '960', '--buffer', '60', '--display-hz', str(args.display_hz),
                       '--audio', '--touch', '--async-video', '--arrival-clock', '--adapt-budget',
                       '--sample-surfaces', '--diagnostic-events', '--drop-video-every', str(args.drop_video_every),
                       '--pacing-burst-bytes', str(args.pacing_burst_bytes),
                       '--source', source, '--output', str(output), *CASES[name]]
            started = time.monotonic()
            # Inherit the runner's metadata-only result; no raw adb output.
            result = subprocess.run(command, cwd=ROOT, timeout=args.seconds+90)
            switches = CASES[name]
            credit = int(switches[switches.index('--pacing-burst-bytes')+1]) if '--pacing-burst-bytes' in switches else args.pacing_burst_bytes
            hz = int(switches[switches.index('--display-hz')+1]) if '--display-hz' in switches else args.display_hz
            row = {'case': name, 'round': number, 'report': str(output), 'exit_code': result.returncode,
                   'effective_pacing_credit_bytes': credit, 'effective_requested_display_hz': hz,
                   'elapsed_host_s': round(time.monotonic()-started, 3), 'prepare_exit': prepare.returncode,
                   'source_state_before': before, 'source_state_after': collect(adb, 'emulator-5556', 8)}
            if output.exists():
                row['summary'] = summarize(output)
                surface_path = output.with_name(output.stem+'-phone-surface.json')
                report = json.loads(output.read_text())
                if surface_path.exists() and isinstance(report.get('phone'), dict):
                    stalls = analyze(report, json.loads(surface_path.read_text()))
                    output.with_name(output.stem+'-stalls.json').write_text(json.dumps(stalls, indent=2)+'\n')
                    row['stalls_over_60ms'] = stalls['stalls_count']
            runs.append(row)
            manifest['source_fingerprints_at_latest_result'] = fingerprints()
            manifest['source_unchanged_during_matrix'] = (
                manifest['source_fingerprints_at_start'] == manifest['source_fingerprints_at_latest_result'])
            (args.output_dir/'matrix.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps({'case': name, 'round': number, 'exit_code': result.returncode,
                              'completed': len(runs)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
