#!/usr/bin/env python3
"""Sequential real-phone ingress/clock experiment; uses an already installed probe APK."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
SOURCE_SERIAL = 'emulator-5556'
VIDEO = 'https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=60'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind-ip', required=True)
    parser.add_argument('--peer-ip', required=True)
    parser.add_argument('--interface', default='en7', choices=('en7', 'en0'))
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--native-encoder', type=Path)
    parser.add_argument('--burst-bytes', type=int)
    parser.add_argument('--burst-seconds', type=float)
    parser.add_argument('--display-hz', type=int, choices=(0, 60, 90, 120), default=0)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=35, choices=(35, 45, 60))
    parser.add_argument('--wire-bitrate', type=int, default=16000000, choices=(12000000, 16000000, 20000000))
    parser.add_argument('--recovery-cooldown-ms', type=int, choices=(100, 200, 500), default=500)
    parser.add_argument('--modes', nargs='+', choices=('sync-legacy-1', 'async-legacy-1', 'async-arrival-1',
                                                    'async-legacy-2', 'async-arrival-2'))
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from hardware_stream import experimental_burst_arguments
    try:
        experimental_burst_arguments(args.native_encoder, 'VBR', args.burst_bytes, args.burst_seconds)
    except ValueError as error:
        parser.error(str(error))
    encoder_options = ([] if args.native_encoder is None else ['--native-encoder', str(args.native_encoder)])
    encoder_options += experimental_burst_arguments(args.native_encoder, 'VBR', args.burst_bytes, args.burst_seconds)
    adb = Path.home()/'Library/Android/sdk/platform-tools/adb'
    runner = Path(__file__).with_name('run_phone_udp.py')
    modes = [('sync-legacy-1', []), ('async-legacy-1', ['--async-video']),
             ('async-arrival-1', ['--async-video', '--arrival-clock']),
             ('async-legacy-2', ['--async-video']),
             ('async-arrival-2', ['--async-video', '--arrival-clock'])]
    if args.modes:
        by_name = dict(modes)
        if len(set(args.modes)) != len(args.modes):
            parser.error('Duplicate experiment mode')
        modes = [(name, by_name[name]) for name in args.modes]
    outputs = [args.output_dir/('phone-udp-ingress-'+name+'.json') for name, _ in modes]
    if any(path.exists() for path in outputs):
        parser.error('Refuse to overwrite an earlier experiment; choose a new evidence directory')
    for (name, options), output in zip(modes, outputs):
        print('Starting '+name, flush=True)
        # The fixed URL is quoted for Android's shell; no account metadata is read.
        subprocess.run([str(adb), '-s', SOURCE_SERIAL, 'shell',
                        "am start -W -a android.intent.action.VIEW -d '"+VIDEO+"' -p app.morphe.android.youtube"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        subprocess.run([str(adb), '-s', SOURCE_SERIAL, 'shell', 'input', 'keyevent', '126'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        subprocess.run([sys.executable, str(runner), '--bind-ip', args.bind_ip, '--peer-ip', args.peer_ip,
                        '--interface', args.interface, '--packetizer', str(args.packetizer),
                        '--seconds', str(args.seconds), '--fps', '60', '--bitrate', '4000000',
                        '--wire-bitrate', str(args.wire_bitrate), '--max-size', '960', '--buffer', '60',
                        '--recovery-cooldown-ms', str(args.recovery_cooldown_ms),
                        '--audio', '--touch', '--adapt-budget', '--sample-surfaces', '--drop-video-every', '50',
                        '--display-hz', str(args.display_hz), *encoder_options,
                        '--source', 'Real public Morphe YouTube BBB; OnePlus WiFi; periodic 2 percent video loss; '+name+'; wire burst budget '+str(args.wire_bitrate),
                        '--output', str(output), *options], cwd=ROOT, check=True, timeout=args.seconds+90)
        print('Completed '+name, flush=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/probes/compare_udp_iterations.py'),
                    *map(str, outputs), '--output', str(args.output_dir/'udp-ingress-matrix-comparison.json')],
                   cwd=ROOT, check=True, timeout=15)


if __name__ == '__main__':
    main()
