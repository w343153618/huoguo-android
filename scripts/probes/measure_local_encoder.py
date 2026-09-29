#!/usr/bin/env python3
"""Measure the existing guest encoder on a synthetic scene, without server accounts.

Run on the Mac hosting emulator-5554. This is a local capture/encode measurement;
it does not measure a real phone's decoder, Internet transit, or touch latency.
"""
import argparse
import json
import pathlib
import random
import re
import socket
import statistics
import struct
import subprocess
import threading
import time
import uuid

HOME = pathlib.Path.home()
ADB = str(HOME / 'Library/Android/sdk/platform-tools/adb')
BASE = HOME / 'Library/Application Support/AndroidRemote'
SERIAL = 'emulator-5554'
SCENE = 'local.remoteandroid.benchmark/.DiagnosticSourceActivity'


def adb(*args, timeout=20):
    return subprocess.run([ADB, '-s', SERIAL, *args], text=True,
                          capture_output=True, check=True, timeout=timeout).stdout


def guest_cpu():
    fields = adb('shell', 'head', '-n', '1', '/proc/stat').split()
    values = list(map(int, fields[1:9]))
    return sum(values), values[3] + values[4]


def qemu_pid(avd='phone17-root', serial=SERIAL):
    port = serial.removeprefix('emulator-')
    rows = subprocess.run(['pgrep', '-f', 'qemu-system-aarch64'],
                          text=True, capture_output=True).stdout.split()
    for pid in rows:
        args = subprocess.run(['ps', '-p', pid, '-o', 'command='],
                              text=True, capture_output=True).stdout
        if '@' + avd in args and '-port ' + port in args:
            return pid
    raise RuntimeError('running target qemu process not found')


def process_cpu_seconds(pid):
    value = subprocess.run(['ps', '-p', pid, '-o', 'time='], text=True,
                           capture_output=True, check=True).stdout.strip()
    parts = [float(x) for x in value.split(':')]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def percentile(values, q):
    return round(sorted(values)[int(q * (len(values) - 1))], 3) if values else None


def measure(duration, pid, fps):
    scid = random.randint(1, 0x7fffffff)
    name = f'scrcpy_{scid:08x}'
    port = None
    process = None
    sockets = []
    file = None
    reader = None
    stopped = threading.Event()
    packets = []
    errors = []
    try:
        port = int(adb('forward', 'tcp:0', 'localabstract:' + name).strip())
        options = [f'scid={scid:x}', 'tunnel_forward=true', 'send_device_meta=false',
                   'send_dummy_byte=false', 'video_codec=h264', 'audio=false',
                   'video_bit_rate=4000000', f'max_fps={fps}', 'max_size=1200',
                   'video_codec_options=bitrate-mode=1', 'control=true', 'cleanup=false']
        server = ('CLASSPATH=/data/local/tmp/remoteandroid-bench.jar app_process / '
                  'com.genymobile.scrcpy.Server 4.1 ' + ' '.join(options))
        process = subprocess.Popen([ADB, '-s', SERIAL, 'shell', server],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(60):
            if name in adb('shell', 'cat', '/proc/net/unix'):
                break
            if process.poll() is not None:
                out, err = process.communicate()
                raise RuntimeError('scrcpy failed: ' + (out + err).decode()[-1800:])
            time.sleep(.1)
        else:
            raise TimeoutError('scrcpy local socket did not appear')
        for _ in range(2):
            sock = socket.create_connection(('127.0.0.1', port), timeout=5)
            sock.settimeout(5)
            sockets.append(sock)
        file = sockets[0].makefile('rb', 65536)

        def read(count):
            data = file.read(count)
            if len(data) != count:
                raise EOFError('short stream read')
            return data

        if read(4) != b'h264':
            raise ValueError('unexpected codec')
        marker, width, height = struct.unpack('>III', read(12))
        if not marker & 0x80000000:
            raise ValueError('missing scrcpy size metadata')

        def receive():
            try:
                while not stopped.is_set():
                    pts, size = struct.unpack('>QI', read(12))
                    if size > 8 * 1024 * 1024:
                        raise ValueError('invalid video packet length')
                    read(size)
                    if not pts & (1 << 62):
                        packets.append((time.monotonic(), pts & ((1 << 61) - 1), size))
            except Exception as exc:
                if not stopped.is_set():
                    errors.append(type(exc).__name__ + ': ' + str(exc))

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        time.sleep(2)  # Let initial codec and activity startup settle.
        total0, idle0 = guest_cpu()
        cpu0 = process_cpu_seconds(pid)
        start = time.monotonic()
        time.sleep(duration)
        end = time.monotonic()
        cpu1 = process_cpu_seconds(pid)
        total1, idle1 = guest_cpu()
        selected = [p for p in packets if start <= p[0] <= end]
        arrival = [(b[0] - a[0]) * 1000 for a, b in zip(selected, selected[1:])]
        pts = [(b[1] - a[1]) / 1000 for a, b in zip(selected, selected[1:])]
        elapsed = end - start
        return {
            'source': 'fixed 30 FPS synthetic scene; no private screen content',
            'stream_size': [width, height], 'duration_seconds': round(elapsed, 3),
            'frames': len(selected), 'encoded_fps': round(len(selected) / elapsed, 3),
            'arrival_gap_p50_ms': percentile(arrival, .5),
            'arrival_gap_p95_ms': percentile(arrival, .95),
            'arrival_gap_p99_ms': percentile(arrival, .99),
            'arrival_gaps_over_50ms': sum(x > 50 for x in arrival),
            'pts_gap_p50_ms': percentile(pts, .5), 'pts_gap_p95_ms': percentile(pts, .95),
            'actual_mbps': round(sum(x[2] for x in selected) * 8 / elapsed / 1e6, 3),
            'guest_cpu_busy_percent_of_8_vcpus': round(100 * (1 - (idle1 - idle0) / (total1 - total0)), 3),
            'host_qemu_cpu_percent_one_core_equals_100': round(100 * (cpu1 - cpu0) / elapsed, 3),
            'errors': errors,
        }
    finally:
        stopped.set()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if reader:
            reader.join(timeout=2)
        if file:
            file.close()
        if process:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if port:
            adb('forward', '--remove', 'tcp:' + str(port))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--fps', type=int, choices=(30, 60), default=30)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 4 or not 3 <= args.duration <= 20:
        parser.error('bounded test: 1-4 repeats of 3-20 seconds')
    existing = adb('shell', 'ps', '-A', '-o', 'NAME,ARGS')
    if 'com.genymobile.scrcpy.Server' in existing:
        raise RuntimeError('a stream is already active; benchmark refused')
    pid = qemu_pid()
    adb('push', str(BASE / 'direct/scrcpy-server-v4.1'), '/data/local/tmp/remoteandroid-bench.jar')
    adb('shell', 'input', 'keyevent', '224')
    run_id = str(uuid.uuid4())
    report = {'label': args.label, 'run_id': run_id,
              'physical_size': adb('shell', 'wm', 'size').strip(),
              'physical_density': adb('shell', 'wm', 'density').strip(),
              'encoder': 'c2.android.avc.encoder (guest software)',
              'settings': {'max_size': 1200, 'fps': args.fps, 'bitrate_bps': 4000000,
                           'mode': 'VBR', 'audio': False, 'network': 'localhost ADB'},
              'samples': []}
    try:
        for index in range(args.repeats):
            adb('shell', 'am', 'force-stop', 'local.remoteandroid.benchmark')
            adb('shell', 'am', 'start', '-W', '-n', SCENE, '--es', 'run_id', run_id)
            sample = measure(args.duration, pid, args.fps)
            report['samples'].append(sample)
            print(json.dumps({'label': args.label, 'sample': index + 1, **sample}), flush=True)
    finally:
        adb('shell', 'am', 'force-stop', 'local.remoteandroid.benchmark')
        adb('shell', 'input', 'keyevent', '3')
        adb('shell', 'input', 'keyevent', '223')
    directory = BASE / 'validation/display-540-20260929'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (re.sub(r'[^a-zA-Z0-9_-]', '_', args.label) + '.json')
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'saved': str(path)}), flush=True)


if __name__ == '__main__':
    main()
