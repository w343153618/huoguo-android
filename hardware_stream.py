"""Apple hardware video adapter; audio and input retain scrcpy's guest service.

The gateway imports only standard-library code. A separate local worker uses
the pinned gRPC environment. No new network listener or credential is created.
Video uses complete frames, a one-frame pending slot and mandatory hardware;
audio timestamps are mapped onto the framebuffer's Unix microsecond clock.
"""
import argparse
import collections
import json
import os
import pathlib
import re
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

CONFIG_FLAG = 1 << 62
PTS_MASK = (1 << 61) - 1


def read_exact(source, count):
    data = bytearray()
    while len(data) < count:
        part = source.recv(count - len(data))
        if not part:
            raise EOFError('local media channel closed')
        data.extend(part)
    return bytes(data)


def audio_timestamp(flagged_pts, offset_us):
    if flagged_pts & CONFIG_FLAG:
        return flagged_pts
    pts = (flagged_pts & PTS_MASK) + offset_us
    if not 0 <= pts <= PTS_MASK:
        raise ValueError('invalid normalized audio timestamp')
    return (flagged_pts & ~PTS_MASK) | pts


def clock_mapping(sample, started_us, ended_us):
    if ended_us < started_us or sample['sample_span_us'] > 5000:
        raise ValueError('uncertain local audio clock sample')
    host_midpoint = (started_us + ended_us) // 2
    return {'offset_us': host_midpoint - sample['monotonic_us'],
            'roundtrip_us': ended_us - started_us,
            'uncertainty_us': (ended_us - started_us + 1) // 2 + sample['sample_span_us'],
            'guest_wall_skew_ms': round((sample['unix_us'] - host_midpoint) / 1000, 3)}


def guest_server_pids(processes, scid):
    found = []
    for row in processes.splitlines():
        words = row.split()
        if words and words[0].isdigit() and 'com.genymobile.scrcpy.Server' in words and 'scid=' + scid in words:
            found.append(words[0])
    return found


def read_control(source):
    kind = read_exact(source, 1)[0]
    sizes = {0: 13, 2: 31, 3: 20, 4: 1, 5: 0, 6: 0, 7: 0, 8: 1,
             10: 1, 11: 0, 14: 2, 15: 0, 17: 0, 18: 1,
             19: 0, 20: 0, 21: 4, 240: 4}
    if kind in sizes:
        return bytes([kind]) + read_exact(source, sizes[kind])
    if kind in (1, 9, 22):
        prefix = read_exact(source, 9) if kind == 9 else b''
        length_bytes = read_exact(source, 4)
        length = struct.unpack('>I', length_bytes)[0]
        if length > (300 if kind == 1 else 262130):
            raise ValueError('oversized input message')
        return bytes([kind]) + prefix + length_bytes + read_exact(source, length)
    if kind == 16:
        size = read_exact(source, 1)
        return bytes([kind]) + size + read_exact(source, size[0])
    if kind in (12, 13):
        prefix = read_exact(source, 6 if kind == 12 else 2)
        if kind == 12:
            size = read_exact(source, 1)
            prefix += size + read_exact(source, size[0])
        length_bytes = read_exact(source, 2)
        length = struct.unpack('>H', length_bytes)[0]
        return bytes([kind]) + prefix + length_bytes + read_exact(source, length)
    raise ValueError('unsupported input message')


def scale_touch(frame, physical_size):
    if frame[0] not in (2, 3):
        return frame
    offset = 10 if frame[0] == 2 else 1
    x, y, width, height = struct.unpack_from('>iiHH', frame, offset)
    if width <= 0 or height <= 0:
        raise ValueError('invalid input dimensions')
    target_width, target_height = physical_size
    x = max(0, min(target_width - 1, round(x * target_width / width)))
    y = max(0, min(target_height - 1, round(y * target_height / height)))
    return frame[:offset] + struct.pack('>iiHH', x, y, target_width, target_height) + frame[offset + 12:]


class FrameRateBudget:
    """Permit four-frame delivery jitter while bounding the sustained rate."""
    def __init__(self, fps, clock=time.monotonic):
        self.fps, self.clock = fps, clock
        self.tokens, self.updated = 4., clock()

    def delay(self):
        now = self.clock()
        self.tokens = min(4., self.tokens + max(0., now - self.updated) * self.fps)
        self.updated = now
        return max(0., (1. - self.tokens) / self.fps)

    def consume(self):
        self.delay()
        self.tokens = max(0., self.tokens - 1.)


class HostHardwareSession:
    """Own a worker and three private socket pairs, matching the existing App."""
    def __init__(self, base, serial, avd, max_size, bit_rate, max_fps, mode):
        self.base = pathlib.Path(base)
        directory = self.base / 'hardware'
        self.clients = {}
        self.proc = None
        self.started = False
        pairs = [socket.socketpair() for _ in range(3)]
        try:
            self.clients = dict(zip(('video', 'audio', 'control'), [p[0] for p in pairs]))
            worker_python = directory / 'venv/bin/python'
            native = directory / 'macos-h264'
            server = directory / 'scrcpy-audio-control'
            if not all(p.is_file() for p in (worker_python, native, server)):
                raise RuntimeError('hardware runtime unavailable')
            arguments = [str(worker_python), str(pathlib.Path(__file__).resolve()),
                         '--serial', serial, '--avd', avd, '--directory', str(directory),
                         '--max-size', str(max_size), '--bitrate', str(bit_rate), '--fps', str(max_fps),
                         '--mode', mode]
            for role, pair in zip(('video', 'audio', 'control'), pairs):
                arguments += ['--' + role + '-fd', str(pair[1].fileno())]
            log = (directory / 'worker.log').open('ab', buffering=0)
            try:
                self.proc = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=log, pass_fds=tuple(p[1].fileno() for p in pairs),
                                             start_new_session=True)
            finally:
                log.close()
            if not select.select([self.proc.stdout], [], [], 15)[0]:
                raise TimeoutError('hardware worker initialization')
            reply = json.loads(self.proc.stdout.readline(2048))
            if not reply.get('initialized'):
                raise RuntimeError('hardware worker failed initialization')
        except Exception:
            self.close()
            raise
        finally:
            for _, remote in pairs:
                remote.close()

    def channel(self, role):
        if self.proc.poll() is not None:
            raise RuntimeError('hardware worker stopped')
        return self.clients[role]

    def start(self):
        if not self.started:
            self.started = True
            self.proc.stdin.write(b'1')
            self.proc.stdin.flush()
            self.proc.stdin.close()

    def close(self):
        for channel in self.clients.values():
            try:
                channel.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            channel.close()
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait(timeout=2)
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream and not stream.closed:
                    stream.close()


def worker(args):
    sys.path.insert(0, str(args.directory / 'proto'))
    import grpc
    import emulator_controller_pb2 as proto
    import emulator_controller_pb2_grpc as proto_grpc
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    sockets = {role: socket.socket(fileno=getattr(args, role + '_fd')) for role in ('video', 'audio', 'control')}
    for channel in sockets.values():
        channel.settimeout(2 if channel is sockets['audio'] else None)
    adb_path = pathlib.Path.home() / 'Library/Android/sdk/platform-tools/adb'
    audio_control = native = channel = None
    port = None
    scid = None
    threads = []
    condition = threading.Condition()
    native_ready = threading.Event()
    latest_frames = collections.deque(maxlen=4)
    controls = collections.deque(maxlen=8)
    physical_size = (540, 1200)
    reply_lock = threading.Lock()
    counters = {'raw_frames': 0, 'pending_frames_replaced': 0, 'frames_submitted': 0, 'idle_repeats': 0,
                'audio_packets': 0, 'audio_startup_discarded': 0}

    def adb(*words):
        result = subprocess.run([str(adb_path), '-s', args.serial, *words],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=True)
        return result.stdout.strip()

    def log(event):
        print(json.dumps(event, separators=(',', ':')), file=sys.stderr, flush=True)

    def fail(exc):
        log({'event': 'worker_failure', 'error_type': type(exc).__name__})
        stop.set()
        with condition:
            condition.notify_all()

    def background(function):
        def guarded():
            try:
                function()
            except Exception as exc:
                if not stop.is_set():
                    fail(exc)
        thread = threading.Thread(target=guarded, daemon=True)
        thread.start()
        threads.append(thread)

    def control_reply(data):
        with reply_lock:
            sockets['control'].sendall(data)

    try:
        if 'com.genymobile.scrcpy.Server' in adb('shell', 'ps', '-A', '-o', 'NAME,ARGS'):
            raise RuntimeError('existing guest stream')
        size = re.search(r'Physical size: (\d+)x(\d+)', adb('shell', 'wm', 'size'))
        if not size:
            raise RuntimeError('physical size unavailable')
        base_width, base_height = map(int, size.groups())
        physical_size = (base_width, base_height)
        discovery = None
        for candidate in (pathlib.Path.home() / 'Library/Caches/TemporaryItems/avd/running').glob('pid_*.ini'):
            values = dict(row.split('=', 1) for row in candidate.read_text().splitlines() if '=' in row)
            if values.get('avd.name') == args.avd and values.get('grpc.token'):
                discovery = values
                break
        if not discovery or not re.fullmatch(r'\d{1,5}', discovery.get('grpc.port', '')):
            raise RuntimeError('authenticated local emulator discovery unavailable')
        grpc_port = int(discovery['grpc.port'])
        if not 1 <= grpc_port <= 65535:
            raise RuntimeError('invalid local gRPC port')
        metadata = (('authorization', 'Bearer ' + discovery['grpc.token']),)
        channel = grpc.insecure_channel('127.0.0.1:' + str(grpc_port),
                                       options=[('grpc.max_receive_message_length', 64 * 1024 * 1024)])
        stub = proto_grpc.EmulatorControllerStub(channel)
        cap = min(args.max_size, max(base_width, base_height))
        request = proto.ImageFormat(format=proto.ImageFormat.RGBA8888, width=cap, height=cap, display=0)
        adb('push', str(args.directory / 'scrcpy-audio-control'), '/data/local/tmp/remoteandroid-host-control.jar')
        clock_command = ('CLASSPATH=/data/local/tmp/remoteandroid-host-control.jar '
                         'app_process / com.genymobile.scrcpy.util.StreamClock interactive')
        clock_proc = subprocess.Popen([str(adb_path), '-s', args.serial, 'shell', '-T', clock_command],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        mappings = []
        try:
            for _ in range(8):
                started_us = time.time_ns() // 1000
                clock_proc.stdin.write(b'p'); clock_proc.stdin.flush()
                if not select.select([clock_proc.stdout], [], [], 3)[0]:
                    raise TimeoutError('guest audio clock calibration')
                sample = json.loads(clock_proc.stdout.readline(2048))
                mappings.append(clock_mapping(sample, started_us, time.time_ns() // 1000))
        finally:
            clock_proc.stdin.close()
            try:
                clock_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                clock_proc.kill(); clock_proc.wait(timeout=2)
            clock_proc.stdout.close()
        clock = min(mappings, key=lambda value: value['roundtrip_us'])
        if clock['uncertainty_us'] > 10000:
            raise RuntimeError('local audio clock uncertainty exceeds 10 ms')
        audio_offset = clock['offset_us']
        log({'event': 'audio_clock_mapping', 'uncertainty_us': clock['uncertainty_us'],
             'guest_wall_skew_ms': clock['guest_wall_skew_ms']})
        name = 'scrcpy_' + format(secrets.randbelow(0x7fffffff), '08x')
        port = int(adb('forward', 'tcp:0', 'localabstract:' + name))
        scid = name.removeprefix('scrcpy_')
        command = ('CLASSPATH=/data/local/tmp/remoteandroid-host-control.jar app_process / '
                   'com.genymobile.scrcpy.Server 4.1 scid=' + scid +
                   ' tunnel_forward=true send_device_meta=false send_dummy_byte=false '
                   'video=false audio=true audio_codec=aac control=true cleanup=false clipboard_autosync=false')
        with (args.directory / 'audio-control.log').open('ab', buffering=0) as guest_log:
            audio_control = subprocess.Popen([str(adb_path), '-s', args.serial, 'shell', command],
                                             stdin=subprocess.DEVNULL, stdout=guest_log, stderr=guest_log)
        for _ in range(50):
            if name in adb('shell', 'cat', '/proc/net/unix'):
                break
            if audio_control.poll() is not None or stop.is_set():
                raise RuntimeError('audio control startup stopped')
            time.sleep(.1)
        else:
            raise TimeoutError('guest audio control startup')
        guest_audio = socket.create_connection(('127.0.0.1', port), timeout=3)
        guest_control = socket.create_connection(('127.0.0.1', port), timeout=3)
        guest_audio.settimeout(30)
        guest_control.settimeout(None)
        print(json.dumps({'initialized': True, 'clock_uncertainty_us': clock['uncertainty_us']}), flush=True)
        # Wait for all App channels before emitting timestamps / media.
        while not stop.is_set():
            if select.select([sys.stdin.buffer], [], [], .5)[0]:
                if sys.stdin.buffer.read(1) != b'1':
                    raise EOFError('gateway closed before media startup')
                break
        if stop.is_set():
            return
        native = subprocess.Popen([str(args.directory / 'macos-h264'), '--service', 'true',
                                   '--fps', str(args.fps), '--bitrate', str(args.bitrate),
                                   '--mode', 'CBR' if args.mode == 'CBR' else 'VBR', '--idle-seconds', '30'],
                                  stdin=subprocess.PIPE, stdout=sockets['video'].fileno(), stderr=subprocess.PIPE)

        def collect():
            nonlocal physical_size
            call = stub.streamScreenshot(request, metadata=metadata)
            try:
                for image in call:
                    if stop.is_set():
                        break
                    width = int(image.format.width or image.width)
                    height = int(image.format.height or image.height)
                    if not width or not height:
                        continue
                    if width % 2 or height % 2 or len(image.image) != width * height * 4:
                        raise ValueError('invalid complete hardware frame')
                    with condition:
                        if len(latest_frames) == 4:
                            counters['pending_frames_replaced'] += 1
                        physical_size = (base_height, base_width) if width > height else (base_width, base_height)
                        latest_frames.append((width, height, int(image.timestampUs), image.image))
                        counters['raw_frames'] += 1
                        condition.notify_all()
            finally:
                call.cancel()

        def native_events():
            for line in native.stderr:
                event = json.loads(line)
                if event.get('event') == 'ready' and event.get('using_hardware'):
                    native_ready.set()
                    with condition:
                        condition.notify_all()
                if event.get('event') == 'bitrate':
                    control_reply(struct.pack('>BI', 240, event['accepted_bitrate']))
                log(event)
            if not stop.is_set():
                stop.set()

        def video_input():
            previous_pts = -1
            last = None
            last_submit = 0.
            budget = FrameRateBudget(args.fps)
            while not stop.is_set():
                with condition:
                    if not latest_frames and not (controls and native_ready.is_set()):
                        condition.wait(timeout=.25)
                    # Initial adaptive requests can precede the first frame.
                    # Retain them until VT has created its required HW session.
                    commands = list(controls) if native_ready.is_set() else []
                    if commands:
                        controls.clear()
                    frame = latest_frames.popleft() if latest_frames else None
                    has_more = len(latest_frames) > 0
                for sequence, kind, value in commands:
                    native.stdin.write(struct.pack('>IIQIII', 0, 0, sequence, 8, kind, value))
                if frame is None:
                    if last is None or time.monotonic() - last_submit < 1:
                        native.stdin.flush()
                        continue
                    frame = (last[0], last[1], time.time_ns() // 1000, last[3])
                    counters['idle_repeats'] += 1
                else:
                    if not has_more:
                        delay = budget.delay()
                        if delay > 0:
                            stop.wait(delay)
                        if stop.is_set():
                            break
                width, height, pts, pixels = frame
                if pts <= previous_pts:
                    continue
                budget.consume()
                native.stdin.write(struct.pack('>IIQI', width, height, pts, len(pixels)))
                native.stdin.write(pixels)
                native.stdin.flush()
                previous_pts, last, last_submit = pts, frame, time.monotonic()
                counters['frames_submitted'] += 1

        def audio_output():
            codec = read_exact(guest_audio, 4)
            sockets['audio'].sendall(codec)
            if struct.unpack('>I', codec)[0] in (0, 1):
                return
            forwarded = False
            started = time.monotonic()
            while not stop.is_set():
                pts, size = struct.unpack('>QI', read_exact(guest_audio, 12))
                if size > 1024 * 1024:
                    raise ValueError('oversized audio packet')
                payload = read_exact(guest_audio, size)
                normalized = audio_timestamp(pts, audio_offset)
                if not pts & CONFIG_FLAG:
                    age_us = time.time_ns() // 1000 - (normalized & PTS_MASK)
                    if counters['audio_packets'] < 3:
                        log({'event': 'audio_clock', 'age_ms': round(age_us / 1000, 3)})
                    counters['audio_packets'] += 1
                    if not forwarded and age_us > 100000 and time.monotonic() - started < .5:
                        counters['audio_startup_discarded'] += 1
                        continue  # Discard startup AAC backlog; retain codec config.
                    forwarded = True
                sockets['audio'].sendall(struct.pack('>QI', normalized, size) + payload)

        def input_events():
            sequence = 0
            while not stop.is_set():
                frame = read_control(sockets['control'])
                if frame[0] == 240:
                    value = struct.unpack_from('>I', frame, 1)[0]
                    if not 500000 <= value <= args.bitrate or args.mode != 'ADAPTIVE_VBR':
                        control_reply(struct.pack('>BI', 240, 0))
                        continue
                    with condition:
                        sequence += 1
                        controls.append((sequence, 1, value))
                        condition.notify_all()
                elif frame[0] == 17:
                    with condition:
                        sequence += 1
                        controls.append((sequence, 2, 0))
                        condition.notify_all()
                else:
                    guest_control.sendall(scale_touch(frame, physical_size))

        def guest_replies():
            while not stop.is_set():
                data = guest_control.recv(65536)
                if not data:
                    raise EOFError('guest input service stopped')
                control_reply(data)

        def sample_pipeline():
            previous_time = time.monotonic()
            previous = {key: counters[key] for key in ('raw_frames', 'pending_frames_replaced',
                                                       'frames_submitted', 'idle_repeats')}
            while not stop.wait(5):
                now = time.monotonic()
                elapsed = max(now - previous_time, 0.001)
                current = {key: counters[key] for key in previous}
                log({'event': 'pipeline_sample', 'unix_ms': time.time_ns() // 1_000_000,
                     'interval_ms': round(elapsed * 1000), 'fps_cap': args.fps,
                     'raw_fps': round((current['raw_frames'] - previous['raw_frames']) / elapsed, 2),
                     'submitted_fps': round((current['frames_submitted'] - previous['frames_submitted']) / elapsed, 2),
                     'replaced_fps': round((current['pending_frames_replaced'] - previous['pending_frames_replaced']) / elapsed, 2),
                     'idle_repeats': current['idle_repeats'] - previous['idle_repeats']})
                previous_time, previous = now, current

        for function in (collect, native_events, video_input, audio_output, input_events, guest_replies,
                         sample_pipeline):
            background(function)
        while not stop.wait(.1):
            if native.poll() is not None or audio_control.poll() is not None:
                raise RuntimeError('media subprocess stopped')
    finally:
        stop.set()
        if channel is not None:
            channel.close()
        for sock in list(sockets.values()) + [locals().get('guest_audio'), locals().get('guest_control')]:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        # A guest AudioRecord may take time to unwind after socket EOF. Retire
        # only this session's exact SCID, so immediate reconnect never collides.
        if scid:
            try:
                for pid in guest_server_pids(adb('shell', 'ps', '-A', '-o', 'PID,ARGS'), scid):
                    adb('shell', 'kill', pid)
            except Exception:
                pass
        for child in (native, audio_control):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        if port:
            try:
                adb('forward', '--remove', 'tcp:' + str(port))
            except Exception:
                pass
        log({'event': 'session_closed', **counters})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--avd', required=True)
    parser.add_argument('--directory', required=True, type=pathlib.Path)
    parser.add_argument('--max-size', required=True, type=int, choices=(960, 1200, 1600, 2400))
    parser.add_argument('--bitrate', required=True, type=int)
    parser.add_argument('--fps', required=True, type=int, choices=(30, 60, 120))
    parser.add_argument('--mode', required=True, choices=('CBR', 'VBR', 'ADAPTIVE_VBR'))
    for role in ('video', 'audio', 'control'):
        parser.add_argument('--' + role + '-fd', required=True, type=int)
    options = parser.parse_args()
    if not re.fullmatch(r'emulator-\d+', options.serial) or not re.fullmatch(r'[a-zA-Z0-9_-]+', options.avd):
        parser.error('requires a local emulator and plain AVD name')
    if not 500000 <= options.bitrate <= 40000000:
        parser.error('invalid bitrate')
    try:
        worker(options)
    except Exception as error:
        print(json.dumps({'event': 'worker_error', 'error_type': type(error).__name__}), file=sys.stderr, flush=True)
        raise SystemExit(1)
