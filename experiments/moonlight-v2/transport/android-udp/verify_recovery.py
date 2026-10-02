#!/usr/bin/env python3
"""Real C++ packetizer regression with CPU H.264 and explicit fault injection.

The source is a CPU test pattern, NOT real M1 YouTube or a phone test. One
standards-defined H.264 filler NAL makes the first IDR exceed the 8 Mbps / 80 ms
wire budget without changing its decoded pixels. A scripted encoder responds
to the actual controller command using a separately encoded small IDR. This
proves the admission/recovery policy, not VideoToolbox rate-control efficiency.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time

from recovery_controller import RecoveryController


HERE = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def exact(stream, count):
    result = bytearray()
    while len(result) < count:
        part = stream.read(count-len(result))
        if not part:
            if not result:
                raise EOFError
            raise ValueError('partial record')
        result.extend(part)
    return bytes(result)


def configuration(data):
    starts = list(re.finditer(b'\x00\x00\x00?\x01', data))
    return b''.join(data[match.start():starts[index+1].start() if index+1 < len(starts) else len(data)]
                    for index, match in enumerate(starts) if data[match.end()] & 31 in (7, 8))


class Packetizer:
    def __init__(self, path):
        self.started = time.monotonic()
        self.process = subprocess.Popen([str(path), '8000000'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.packets = bytearray()
        self.events = []
        self.mailbox = queue.Queue()
        self.first_output_s = None
        self.errors = []
        self.threads = []
        def output():
            try:
                while True:
                    header = exact(self.process.stdout, 2)
                    size = struct.unpack('>H', header)[0]
                    if not 56 < size <= 1080:
                        raise ValueError('packet size')
                    body = exact(self.process.stdout, size)
                    if self.first_output_s is None:
                        self.first_output_s = time.monotonic()-self.started
                    self.packets.extend(header+body)
            except EOFError:
                pass
            except Exception as error:
                self.errors.append(type(error).__name__)
        def events():
            try:
                for line in self.process.stderr:
                    if len(line) > 8192:
                        raise ValueError('event size')
                    event = json.loads(line)
                    elapsed = time.monotonic()-self.started
                    self.events.append((elapsed, event))
                    self.mailbox.put((elapsed, event))
            except Exception as error:
                self.errors.append(type(error).__name__)
        for function in (output, events):
            thread = threading.Thread(target=function, daemon=True)
            thread.start(); self.threads.append(thread)

    def write(self, payload, pts, config=False):
        self.process.stdin.write(struct.pack('>QI', (1 << 62) if config else pts, len(payload))+payload)
        self.process.stdin.flush()

    def initial(self, config):
        self.process.stdin.write(b'h264'+struct.pack('>III', 0x80000000, 720, 1280))
        self.write(config, 0, True)

    def finish(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        for thread in self.threads:
            thread.join(timeout=2)
            if thread.is_alive():
                raise ValueError('reader did not finish')
        if self.process.returncode or self.errors:
            raise ValueError('packetizer regression process failed')
        summaries = [event for _, event in self.events if event['event'] == 'summary']
        return summaries[-1]


def decoded_hashes(ffmpeg, path):
    completed = run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'h264', '-i', path,
                    '-f', 'framemd5', '-', capture_output=True, text=True, timeout=20)
    if completed.stderr:
        raise ValueError('fixture decode warning')
    return [line.rsplit(',', 1)[-1].strip() for line in completed.stdout.splitlines()
            if line and not line.startswith('#')]


def parse_bodies(data):
    result, pts, position = bytearray(), [], 0
    while position < len(data):
        size = struct.unpack_from('>I', data, position)[0]; position += 4
        body = data[position:position+size]; position += size
        width, height, stamp, config_size = struct.unpack_from('>IIQI', body)
        if (width, height) != (720, 1280) or len(body) != size or config_size > 65536:
            raise ValueError('returned geometry/body')
        result.extend(body[20:]); pts.append(stamp & ((1 << 61)-1))
    return result, pts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-udp-recovery-build/host'))
    parser.add_argument('--output', type=Path, default=HERE/'evidence/recovery-regression-20261001.json')
    args = parser.parse_args()
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SystemExit('Existing ffmpeg and ffprobe required; no installs attempted')
    evidence = {'created_at': datetime.now(timezone.utc).isoformat(),
                'validation_layer': 'offline_CPU_H264_actual_native_packetizer_stdio_controller_fault_injection',
                'source': 'testsrc2 720x1280 60 fps, CPU libx264 Baseline single-ref no B',
                'wire_bitrate_bps': 8_000_000, 'assembly_budget_ms': 80,
                'fault': '120000 byte valid H264 filler NAL added to first real IDR',
                'not_measured': ['M1 YouTube', 'VideoToolbox adaptation', 'UDP sockets', 'phone',
                                 'WAN NAT', 'audio', 'native touch', 'capture-to-display latency'], 'samples': []}
    with tempfile.TemporaryDirectory(prefix='huoguo-recovery-fixture-', dir='/private/tmp') as directory:
        folder = Path(directory)
        source = folder/'source.h264'
        run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=720x1280:rate=60',
            '-t', '2', '-c:v', 'libx264', '-threads', '4', '-preset', 'ultrafast', '-tune', 'zerolatency',
            '-profile:v', 'baseline', '-refs', '1', '-bf', '0', '-b:v', '2M', '-maxrate', '2M', '-bufsize', '500k',
            '-g', '120', '-sc_threshold', '0', '-pix_fmt', 'yuv420p', '-f', 'h264', source)
        data = source.read_bytes()
        info = json.loads(run(ffprobe, '-v', 'error', '-show_packets', '-select_streams', 'v', '-of', 'json',
                              source, capture_output=True, text=True).stdout)['packets']
        frames = [data[int(packet['pos']):int(packet['pos'])+int(packet['size'])] for packet in info]
        config = configuration(frames[0])
        oversized_idr = frames[0] + b'\x00\x00\x00\x01\x0c' + b'\xff'*120000 + b'\x80'
        inflated = folder/'inflated.h264'; inflated.write_bytes(oversized_idr+b''.join(frames[1:]))
        original = decoded_hashes(ffmpeg, source)
        if decoded_hashes(ffmpeg, inflated) != original:
            raise ValueError('filler injection changed decoded frame pixels')

        for label, adaptive in [('unadapted_wait_for_natural_2s_IDR', False), ('adaptive_prompt_small_IDR', True)]:
            sender = Packetizer(args.build/'h264_udp_packetizer')
            controller = RecoveryController(8_000_000, 8_000_000)
            sender.initial(config); sender.write(oversized_idr, 1_000_000)
            deadline = time.monotonic()+2
            decisions = []
            while time.monotonic() < deadline:
                event_elapsed, event = sender.mailbox.get(timeout=2)
                decision = controller.consume(event)
                if event['event'] == 'encoder_budget_feedback':
                    if decision is None or decision.requested_bitrate_bps >= 8_000_000:
                        raise ValueError('budget feedback did not reduce encoder target')
                    decisions.append(decision.report())
                    feedback_elapsed = event_elapsed
                    break
            else:
                raise ValueError('missing immediate native budget feedback')
            origin = time.monotonic()
            if adaptive:
                # The actual control bytes request a lower target then an IDR.
                # This scripted encoder chooses an already encoded small IDR;
                # only the parent real M1 experiment can validate VT acceptance.
                if decision.command_bytes() != b'\xf0'+struct.pack('>I', 4_000_000)+b'\x11':
                    raise ValueError('incorrect hardware control command')
                sender.write(frames[0], 2_000_000)
                for index, frame in enumerate(frames[1:], 1):
                    time.sleep(max(0, origin+index/60-time.monotonic()))
                    sender.write(frame, 2_000_000+index*1_000_000//60)
            else:
                for index in range(1, 120):
                    time.sleep(max(0, origin+index/60-time.monotonic()))
                    sender.write(frames[index], 1_000_000+index*1_000_000//60)
                time.sleep(max(0, origin+2-time.monotonic()))
                sender.write(frames[0], 3_000_000)
            summary = sender.finish()
            returned = run(args.build/'phone_receiver_probe', input=bytes(sender.packets), capture_output=True, timeout=20)
            body, stamps = parse_bodies(returned.stdout)
            path = folder/(label+'.h264'); path.write_bytes(body)
            hashes = decoded_hashes(ffmpeg, path)
            expected = original if adaptive else original[:1]
            if hashes != expected or (adaptive and (not stamps or stamps[0] != 2_000_000)):
                raise ValueError('recovered output differs or leaked broken P chain')
            request_times = [elapsed for elapsed, event in sender.events if event['event'] == 'request_idr']
            completions = [elapsed for elapsed, event in sender.events if event['event'] == 'recovery_complete']
            if len(completions) != 1:
                raise ValueError('missing native complete IDR recovery')
            complete_after_feedback = completions[0]-feedback_elapsed
            if adaptive and (sender.first_output_s is None or complete_after_feedback >= .5):
                raise ValueError('adaptive recovery waited for natural GOP')
            if not adaptive and (len(request_times) < 2 or sender.first_output_s is None or complete_after_feedback < 1.9):
                raise ValueError('bounded retry / natural GOP comparison failed')
            evidence['samples'].append({'label': label, 'first_output_s': round(sender.first_output_s, 6),
                                        'first_budget_feedback_s': round(feedback_elapsed, 6),
                                        'complete_IDR_after_budget_feedback_ms': round(complete_after_feedback*1000, 3),
                                        'request_times_s': [round(value, 6) for value in request_times],
                                        'sender': summary, 'controller_decisions': decisions,
                                        'decoded_frames': len(hashes), 'exact_frame_digest_match': hashes == expected,
                                        'broken_chain_not_forwarded': True})

        sender = Packetizer(args.build/'h264_udp_packetizer'); sender.initial(config)
        origin = time.monotonic()
        for index in range(68):
            time.sleep(max(0, origin+index*.05-time.monotonic()))
            sender.write(oversized_idr, 1_000_000+index*50_000)
        summary = sender.finish()
        requests = [elapsed for elapsed, event in sender.events if event['event'] == 'request_idr']
        if len(requests) != 6 or summary['recovery_exhausted'] != 1 or summary['output_frames'] != 0:
            raise ValueError('failed IDRs reset or bypassed finite retry bound')
        if any(b-a < .49 for a, b in zip(requests, requests[1:])):
            raise ValueError('retry cooldown exceeded')
        evidence['samples'].append({'label': 'continuous_oversized_IDRs_bounded',
                                    'request_times_s': [round(value, 6) for value in requests], 'sender': summary,
                                    'no_broken_chain_output': not sender.packets})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
