#!/usr/bin/env python3
"""Local synthetic adapter validation; optional existing-account TLS, no WAN or saved pixels."""
import argparse
import base64
import http.client
import json
import os
import pathlib
import plistlib
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = pathlib.Path(os.environ.get('DIRECT_STATE_DIR', str(pathlib.Path.home() / 'Documents/ChatGPT/others/android-remote/m1-compare')))
sys.path.insert(0, str(ROOT))
from hardware_stream import CONFIG_FLAG, PTS_MASK, HostHardwareSession, read_exact


class GatewaySession:
    """The same authenticated loopback TLS session/channel API used by the App."""
    def __init__(self, fps, mode, bitrate):
        password = os.environ.get('HUOGUO_TEST_PASSWORD')
        if not password:
            raise RuntimeError('existing account password missing from test environment')
        certificate = os.environ.get('DIRECT_CERT', str(pathlib.Path.home() / '.config/sunshine/credentials/cacert.pem'))
        self.context = ssl.create_default_context(cafile=certificate)
        self.context.check_hostname = False  # Exact private CA is pinned; LAN IPs may change.
        self.authorization = 'Basic ' + base64.b64encode(('huoguo:' + password).encode()).decode()
        self.clients = {}
        connection = http.client.HTTPSConnection('127.0.0.1', 15556, context=self.context, timeout=20)
        try:
            connection.request('POST', '/session', json.dumps({'max_size': 1200, 'max_fps': fps,
                                'video_bit_rate': bitrate, 'bitrate_mode': mode}),
                               {'Authorization': self.authorization, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError('authenticated gateway session rejected: ' + str(response.status))
            result = json.loads(response.read(2048))
        finally:
            connection.close()
        self.sid = result['session']
        if len(self.sid) != 32 or not all(c in '0123456789abcdef' for c in self.sid):
            raise ValueError('invalid gateway session identifier')
        self.metadata = {key: result[key] for key in ('video_backend', 'hardware_required', 'max_fps')}

    def channel(self, role):
        stream = self.context.wrap_socket(socket.create_connection(('127.0.0.1', 15556), timeout=20), server_hostname='localhost')
        self.clients[role] = stream
        request = 'CONNECT /stream/' + self.sid + '/' + role + ' HTTP/1.1\r\nHost: localhost\r\nAuthorization: ' + self.authorization + '\r\n\r\n'
        stream.sendall(request.encode())
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            if len(header) >= 8192:
                raise ValueError('oversized channel response')
            header.extend(read_exact(stream, 1))
        if not header.split(b'\r\n')[0].startswith(b'HTTP/1.1 200 '):
            raise RuntimeError('gateway channel rejected')
        stream.settimeout(None)
        return stream

    def start(self):
        pass

    def close(self):
        for stream in self.clients.values():
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fps', type=int, choices=(30, 60, 120), default=60)
    parser.add_argument('--duration', type=int, default=30)
    parser.add_argument('--bitrate', type=int, default=4000000)
    parser.add_argument('--mode', choices=('CBR', 'VBR', 'ADAPTIVE_VBR'), default='ADAPTIVE_VBR')
    parser.add_argument('--gateway', action='store_true')
    args = parser.parse_args()
    initial_rate = min(2500000, args.bitrate)
    if not 10 <= args.duration <= 120:
        parser.error('requires 10-120 seconds')
    if not 500000 <= args.bitrate <= 40000000:
        parser.error('requires 0.5-40 Mbps')
    adb_path = str(pathlib.Path.home() / 'Library/Android/sdk/platform-tools/adb')

    def adb(*words):
        return subprocess.run([adb_path, '-s', 'emulator-5556', *words],
                              capture_output=True, text=True, check=True, timeout=20).stdout.strip()

    if 'com.genymobile.scrcpy.Server' in adb('shell', 'ps', '-A', '-o', 'NAME,ARGS'):
        raise RuntimeError('existing guest stream')
    run_id = str(uuid.uuid4())
    log_file = BASE / 'hardware/worker.log'
    log_start = log_file.stat().st_size if log_file.exists() else 0
    session = None
    done = threading.Event()
    errors, video, audio, acknowledgements, sizes = [], [], [], [], []
    compressed = bytearray()

    def launch(function):
        def guarded():
            try:
                function()
            except Exception as exc:
                if not done.is_set():
                    errors.append(type(exc).__name__)
                    done.set()
        thread = threading.Thread(target=guarded, daemon=True)
        thread.start()
        return thread

    try:
        adb('shell', 'input', 'keyevent', '224')
        adb('shell', 'am', 'force-stop', 'local.remoteandroid.benchmark')
        adb('shell', 'am', 'start', '-W', '-n', 'local.remoteandroid.benchmark/.DiagnosticSourceActivity',
            '--es', 'run_id', run_id, '--ei', 'source_fps', str(args.fps), '--es', 'source_clock', 'nearest',
            '--ez', 'audio_probe', 'true')
        session = (GatewaySession(args.fps, args.mode, args.bitrate) if args.gateway else
                   HostHardwareSession(BASE, 'emulator-5556', 'RemoteAndroid17Compare',
                                       1200, args.bitrate, args.fps, args.mode))
        channels = {role: session.channel(role) for role in ('video', 'audio', 'control')}

        def receive_video():
            stream = channels['video']
            if read_exact(stream, 4) != b'h264':
                raise ValueError('video codec mismatch')
            while not done.is_set():
                hi = struct.unpack('>I', read_exact(stream, 4))[0]
                if hi & 0x80000000:
                    sizes.append(list(struct.unpack('>II', read_exact(stream, 8))))
                    continue
                low, size = struct.unpack('>II', read_exact(stream, 8))
                if size > 8 * 1024 * 1024:
                    raise ValueError('oversized compressed frame')
                pts = (hi << 32) | low
                data = read_exact(stream, size)
                if len(compressed) < 32 * 1024 * 1024:
                    compressed.extend(data)
                if not pts & CONFIG_FLAG:
                    video.append((time.monotonic(), pts & PTS_MASK, size))

        def receive_audio():
            stream = channels['audio']
            codec = struct.unpack('>I', read_exact(stream, 4))[0]
            if codec != 0x00616163:
                raise ValueError('AAC codec unavailable')
            while not done.is_set():
                pts, size = struct.unpack('>QI', read_exact(stream, 12))
                if size > 1024 * 1024:
                    raise ValueError('oversized audio packet')
                read_exact(stream, size)
                if not pts & CONFIG_FLAG:
                    audio.append((time.monotonic(), pts & PTS_MASK,
                                  (time.time_ns() // 1000 - (pts & PTS_MASK)) / 1000))

        def receive_control():
            while not done.is_set():
                kind = read_exact(channels['control'], 1)[0]
                if kind != 240:
                    raise ValueError('unexpected synthetic-scene reply')
                acknowledgements.append(struct.unpack('>I', read_exact(channels['control'], 4))[0])

        readers = [launch(f) for f in (receive_video, receive_audio, receive_control)]
        session.start()
        if args.mode == 'ADAPTIVE_VBR':
            # Send before the first encoder frame to exercise the startup race.
            channels['control'].sendall(struct.pack('>BI', 240, initial_rate))
        deadline = time.monotonic() + 12
        while not (video and audio) and not done.is_set() and time.monotonic() < deadline:
            time.sleep(.05)
        if not video or not audio or done.is_set():
            raise RuntimeError('audio/video startup failed: ' + repr(errors))
        # Exercise actual guest input in a known synthetic scene.
        for action, pressure in ((0, 65535), (1, 0)):
            channels['control'].sendall(struct.pack('>BBQiiHHHII',
                                                   2, action, 0, 270, 600, 540, 1200, pressure, 0, 0))
        time.sleep(2)
        begin = time.monotonic()
        for second in range(args.duration):
            if done.wait(1):
                raise RuntimeError('adapter stopped: ' + repr(errors))
            if second == args.duration // 2 and args.mode == 'ADAPTIVE_VBR':
                channels['control'].sendall(struct.pack('>BI', 240, args.bitrate))
        end = time.monotonic()
        logs = adb('shell', 'logcat', '-d', '-v', 'brief', '-s', 'DiagnosticSource:I', '*:S')
        touch = [line[line.index('synthetic_touch'):] for line in logs.splitlines()
                 if 'synthetic_touch' in line and run_id in line]
        key = struct.pack('>BBiii', 0, 0, 4, 0, 0) + struct.pack('>BBiii', 0, 1, 4, 0, 0)
        channels['control'].sendall(key)
        time.sleep(.3)
        focus = adb('shell', 'dumpsys', 'window')
        back_exited = not any('local.remoteandroid.benchmark/' in row
                              for row in focus.splitlines() if 'mCurrentFocus=' in row)
        selected = [v for v in video if begin <= v[0] <= end]
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(selected, selected[1:])]
        audio_selected = [v for v in audio if begin <= v[0] <= end]
        if not touch or not back_exited:
            raise RuntimeError('guest touch or navigation verification failed')
        if args.mode == 'ADAPTIVE_VBR' and not all(v in acknowledgements for v in (initial_rate, args.bitrate)):
            raise RuntimeError('hardware adaptive bitrate was not acknowledged')
        decode = subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-f', 'h264',
                                 '-i', 'pipe:0', '-f', 'null', '-'], input=bytes(compressed),
                                capture_output=True, timeout=30)
        if decode.returncode:
            raise RuntimeError('H264 stream failed local decode')
        with log_file.open('rb') as stream:
            stream.seek(log_start)
            events = [json.loads(line) for line in stream if line.startswith(b'{')]
        hardware = [v for v in events if v.get('event') == 'ready']
        if not hardware or not all(v.get('using_hardware') for v in hardware):
            raise RuntimeError('required hardware use was not verified')

        def p95(values):
            return round(sorted(values)[int((len(values) - 1) * .95)], 3) if values else None

        steady_windows = [sum(begin + offset <= v[0] < begin + offset + 5 for v in selected) / 5
                          for offset in range(0, int(end - begin) - 4, 5)]
        print(json.dumps({'probe': 'local-production-hardware-adapter-v1', 'mode': args.mode,
                          'target_bitrate_bps': args.bitrate,
                          'transport': 'authenticated_loopback_tls' if args.gateway else 'private_socketpairs',
                          'gateway_metadata': session.metadata if args.gateway else None,
                          'target_fps': args.fps, 'measurement_seconds': round(end - begin, 3),
                          'encoded_received_fps': round(len(selected) / (end - begin), 3),
                          'received_video_mbps': round(sum(v[2] for v in selected) * 8 / (end - begin) / 1e6, 3),
                          'output_gap_p95_ms': p95(gaps), 'gaps_over_33ms': sum(v > 33.4 for v in gaps),
                          'output_gap_max_ms': round(max(gaps), 3) if gaps else None,
                          'gaps_over_two_frame_intervals': sum(v > 2000 / args.fps for v in gaps),
                          'complete_5s_windows': len(steady_windows),
                          'complete_5s_window_min_fps': min(steady_windows) if steady_windows else None,
                          'dimensions': sizes, 'audio_packets': len(audio_selected),
                          'audio_timestamp_age_p95_ms': p95([v[2] for v in audio_selected]),
                          'audio_clock_calibration': [v for v in events if v.get('event') == 'audio_clock_mapping'],
                          'adaptive_acknowledgements': acknowledgements,
                          'touch_events': touch, 'back_key_exited': back_exited,
                          'hardware_readback': hardware, 'local_h264_decode_exit': decode.returncode,
                          'scope': 'M1 local production adapter and optional loopback TLS with synthetic video, AAC and input; excludes WAN and real-phone playback',
                          'pixels_or_compressed_video_saved': False}, indent=2))
    finally:
        done.set()
        if session:
            session.close()
        adb('shell', 'am', 'force-stop', 'local.remoteandroid.benchmark')


if __name__ == '__main__':
    main()
