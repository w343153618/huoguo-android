#!/usr/bin/env python3
"""Real YouTube with/without isolated UDP capture, paired with guest trace/fences.

Does not install APKs, alter VM/display settings, or change production services.
Raw Perfetto paths are returned only to the operator, never stored in evidence.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--condition', choices=('udp', 'source-only'), required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--encoder', type=Path)
    parser.add_argument('--packetizer', type=Path)
    parser.add_argument('--trace-categories', choices=('video', 'gfx,view,video'), default='video',
                        help='video avoids the much heavier global gfx/view observer')
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error('new evidence directory required')
    if args.condition == 'udp' and (not args.encoder or not args.packetizer):
        parser.error('UDP needs explicit experimental binaries')
    adb = Path.home()/'Library/Android/sdk/platform-tools/adb'
    processes = subprocess.run(['ps', '-axo', 'command='], capture_output=True,
                               text=True, check=True, timeout=5).stdout
    if any('hardware_stream.py' in row and '--serial emulator-5556' in row for row in processes.splitlines()):
        parser.error('existing media worker retained; cannot run controlled source test')
    args.output_dir.mkdir(parents=True)
    selected = ['am', 'start', '-a', 'android.intent.action.VIEW', '-d',
                'https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=60s', '-p', 'app.morphe.android.youtube']
    prepared = subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', shlex.join(selected)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    time.sleep(2)
    before = collect(adb, 'emulator-5556', 8)
    if before.get('state') in (1, 2):
        subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'input', 'keyevent', 'KEYCODE_MEDIA_PLAY'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        time.sleep(1)
        before = collect(adb, 'emulator-5556', 8)
    if before.get('state') != 3 or before.get('speed') != 1:
        raise SystemExit('selected source is not playing; no causal test run')
    commands = {
        'perfetto': [sys.executable, str(ROOT/'scripts/probes/guest_perfetto_probe.py'),
                     '--seconds', '45', '--categories', args.trace_categories,
                     '--capture-only', '--output', str(args.output_dir/'perfetto-capture.json')],
        'fences': [sys.executable, str(ROOT/'scripts/probes/measure_source_frame_fences.py'),
                   '--seconds', '45', '--output', str(args.output_dir/'source-fences.json')],
        'resources': [sys.executable, str(ROOT/'scripts/probes/measure_host_resources.py'),
                      '--seconds', '45', '--output', str(args.output_dir/'host-resources.json')],
    }
    if args.condition == 'udp':
        commands['udp'] = [sys.executable, str(ROOT/'experiments/moonlight-v2/transport/android-udp/run_phone_udp.py'),
            '--bind-ip', '192.168.9.128', '--peer-ip', '192.168.9.6', '--interface', 'en7',
            '--packetizer', str(args.packetizer), '--native-encoder', str(args.encoder),
            '--burst-bytes', '100000', '--burst-seconds', '.08', '--seconds', '35', '--fps', '60',
            '--bitrate', '4000000', '--wire-bitrate', '32000000', '--max-size', '960', '--buffer', '60',
            '--display-hz', '90', '--content-hint-fps', '60', '--raw-queue-policy', 'fifo',
            '--audio', '--touch', '--async-video', '--arrival-clock', '--adapt-budget', '--network-feedback',
            '--socket-pacing', '--pacing-burst-bytes', '2048', '--sample-surfaces', '--diagnostic-events', '--capture-trace',
            '--source', 'Real public Morphe YouTube BBB requested seek60s; previously selected1080p60 UI; M1 physical LAN causal trace',
            '--output', str(args.output_dir/'udp.json')]
    files = ['scripts/probes/guest_perfetto_probe.py', 'scripts/probes/measure_source_frame_fences.py',
             'scripts/probes/measure_host_resources.py', 'hardware_stream.py']
    manifest = dict(schema=1, condition=args.condition, source_prepare_exit=prepared.returncode,
        scope='Real public M1 YouTube; UDP condition is physical LAN OnePlus15, source-only has no media capture subscriber',
        source_state_before=before, comparison_requested_fps=60, source_media_fps_known=False,
        production_services_changed=False, host_start_monotonic_ns=time.monotonic_ns(),
        requested_trace_categories=args.trace_categories.split(','),
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}, results={})
    running, stdout = {}, {}
    try:
        for name, command in commands.items():
            running[name] = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True)
        for name, process in running.items():
            output, _ = process.communicate(timeout=100)
            stdout[name] = output
            manifest['results'][name] = dict(exit_code=process.returncode)
    finally:
        for process in running.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
        manifest.update(host_end_monotonic_ns=time.monotonic_ns(), source_state_after=collect(adb, 'emulator-5556', 8))
        (args.output_dir/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    # Perfetto CLI status contains the private raw location only; retain it in
    # operator output so analysis can proceed without copying raw traces to Git.
    for name in ('perfetto', 'fences', 'resources'):
        print(name+': '+stdout.get(name, '').strip())
    print(json.dumps(dict(condition=args.condition, results=manifest['results'])))


if __name__ == '__main__':
    main()
