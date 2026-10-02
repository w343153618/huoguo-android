#!/usr/bin/env python3
"""Bounded real M1 H264 -> authenticated UDP -> installed phone decoder probe.

No TCP carries media to the phone. The existing AVD is not restarted or switched
to a synthetic source. Audio/touch can be enabled explicitly; this component
does not establish WAN/P2P or full-product acceptance.
"""
import argparse
import base64
from collections import deque
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from hardware_stream import (HostHardwareSession, experimental_burst_arguments,
                             experimental_prioritize_speed_arguments, experimental_raw_submit_arguments,
                             verify_raw_submit_budget_readback, read_exact, read_control)
from udp_probe_protocol import CLIENT_NONCE, ReplayWindow, open_packet
from udp_session_sender import AuthenticatedSender, SocketPacer, SocketVideoGate
from recovery_controller import RecoveryController
from feedback_controller import NetworkFeedbackController

PACKAGE = 'local.remoteandroid.direct'
PROBE_PACKAGE = 'local.remoteandroid.phoneprobe'
SESSION_FILE = '/data/user/0/' + PACKAGE + '/files/udp-video-session.json'
REPORT_NAME = 'udp-video-report.json'
REPORT_FILE = '/data/user/0/' + PACKAGE + '/files/' + REPORT_NAME
COMPONENT = 'local.remoteandroid.phoneprobe/local.remoteandroid.direct.UdpVideoProbe'
TOUCH_SOURCE_SERIAL = 'emulator-5556'


def client_configuration(experimental_client=False):
    """Choose one of two fixed package pairs; never accept arbitrary data paths."""
    if type(experimental_client) is not bool:
        raise ValueError('Experimental client selection must be boolean')
    package = 'local.remoteandroid.direct.experiment' if experimental_client else PACKAGE
    probe = 'local.remoteandroid.phoneprobe.experiment' if experimental_client else PROBE_PACKAGE
    data = '/data/user/0/' + package + '/files/'
    return {'client_package': package, 'probe_package': probe,
            'component': probe + '/local.remoteandroid.direct.UdpVideoProbe',
            'session_file': data + 'udp-video-session.json', 'report_file': data + REPORT_NAME}


def verify_instrumentation_target(output, experimental_client=False):
    """Require exactly one installed fixed component with its expected target."""
    if isinstance(output, bytes):
        if len(output) > 65536:
            raise ValueError('Instrumentation listing outside bounds')
        try:
            output = output.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Instrumentation listing is not UTF-8') from None
    if not isinstance(output, str) or len(output) > 65536:
        raise ValueError('Instrumentation listing outside bounds')
    config = client_configuration(experimental_client)
    prefix = 'instrumentation:'
    matches = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        words = line[len(prefix):].split(None, 1)
        if not words or words[0] != config['component']:
            continue
        match = re.fullmatch(re.escape(prefix + config['component']) +
                             r'[ \t]+\(target=([A-Za-z0-9_.]+)\)', line)
        matches.append(match.group(1) if match else None)
    if matches != [config['client_package']]:
        raise RuntimeError('Installed UDP instrumentation target mismatch, ambiguous or unavailable')
    return True


def read_instrumentation_target(adb, experimental_client=False):
    result = subprocess.run(list(adb) + ['shell', 'pm', 'list', 'instrumentation'],
                            stdin=subprocess.DEVNULL, check=True, capture_output=True, timeout=5)
    return verify_instrumentation_target(result.stdout, experimental_client)


def initialize_experimental_client_files(root, uid, experimental_client=False):
    """Initialize only the fixed experiment files directory after owner verification."""
    client = client_configuration(experimental_client)
    if not experimental_client:
        return False
    if not isinstance(uid, str) or re.fullmatch(r'[0-9]{1,10}', uid) is None:
        raise ValueError('Experimental client owner must be a validated numeric UID')
    directory = '/data/user/0/' + client['client_package'] + '/files'
    root('mkdir -p ' + directory + ' && chown ' + uid + ':' + uid + ' ' + directory +
         ' && chmod 700 ' + directory + ' && restorecon ' + directory, timeout=5)
    return True


def verify_surface_submit_report(report, requested_lead_ms, experimental_client=False):
    """Require the isolated phone to report the requested, fixed submission mode."""
    if not experimental_client:
        return False
    statuses = ({'disabled_existing_release_path'} if requested_lead_ms == 0
                else {'applied_bounded_wait', 'enabled_no_wait_observed'})
    if (not isinstance(report, dict) or type(report.get('surface_submit_lead_ms')) is not int
            or report['surface_submit_lead_ms'] != requested_lead_ms
            or report.get('surface_submit_status') not in statuses):
        raise ValueError('Experimental Surface submission readback missing or mismatched')
    return True


def raw_submit_experiment_readback(hardware, phone_report, fps, raw_submit_fps):
    """Verify worker configuration and the unchanged phone FPS independently."""
    if raw_submit_fps is None:
        return None
    value = verify_raw_submit_budget_readback(
        getattr(hardware, 'raw_submit_budget_readback', None), fps, raw_submit_fps)
    phone_fps = phone_report.get('fps_limit') if isinstance(phone_report, dict) else None
    if type(phone_fps) is not int or phone_fps != fps:
        raise ValueError('Raw submit experiment phone FPS readback missing or mismatched')
    return dict(value, phone_fps_limit=phone_fps)


def parse_touch_geometry(output):
    """Read wm's physical and effective display size without retaining raw text.

    An override is Android's effective size and takes precedence. The bridge
    uses that geometry to convert normalized native multi-pointer snapshots;
    the existing hardware control writer performs its subsequent display map.
    Dimensions must fit the existing scrcpy touch message's unsigned 16 bits.
    """
    if isinstance(output, bytes):
        try:
            output = output.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('Source wm size must be ASCII') from None
    if not isinstance(output, str) or not output.isascii() or len(output) > 1024:
        raise ValueError('Source wm size output outside bounds')
    sizes = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'(Physical|Override) size:[ \t]*([1-9][0-9]{0,4})x([1-9][0-9]{0,4})[ \t]*', line.strip())
        if match is None or match.group(1) in sizes:
            raise ValueError('Source wm size output malformed or ambiguous')
        width, height = int(match.group(2)), int(match.group(3))
        if width > 65535 or height > 65535:
            raise ValueError('Source wm size exceeds touch protocol dimensions')
        sizes[match.group(1)] = (width, height)
    if 'Physical' not in sizes:
        raise ValueError('Source physical size unavailable')
    physical = sizes['Physical']
    effective = sizes.get('Override', physical)
    return {'physical_width': physical[0], 'physical_height': physical[1],
            'effective_width': effective[0], 'effective_height': effective[1],
            'override_present': 'Override' in sizes}


def read_touch_geometry(adb_path):
    """Only query the explicit source emulator; never the phone --serial."""
    result = subprocess.run([str(adb_path), '-s', TOUCH_SOURCE_SERIAL,
                             'shell', 'wm', 'size'],
                            stdin=subprocess.DEVNULL, check=True,
                            capture_output=True, timeout=5)
    return parse_touch_geometry(result.stdout)


def allowed_address(text):
    address = ipaddress.IPv4Address(text)
    private = any(address in ipaddress.IPv4Network(network) for network in
                  ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10'))
    if not private:
        raise ValueError('This component probe accepts only explicit LAN or Tailnet peers')
    return str(address)


def read_phone_report(output, fetch):
    name = count = None
    for line in output.decode(errors='replace').splitlines():
        if line.startswith('INSTRUMENTATION_RESULT: report_file='):
            name = line.split('report_file=', 1)[1].strip()
        if line.startswith('INSTRUMENTATION_RESULT: report_bytes='):
            count = line.split('report_bytes=', 1)[1].strip()
    if name != REPORT_NAME or count is None or not count.isdecimal() or not 0 < int(count) <= 64*1024*1024:
        raise RuntimeError('Missing fixed UDP report pointer; instrumentation diagnostics suppressed')
    raw = fetch()
    if len(raw) != int(count):
        raise ValueError('UDP report byte count mismatch')
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError('UDP report must be an object')
    return report


def encoder_readback(path):
    """Retain only typed VT metadata from this probe's private owned worker log."""
    keys = {'event', 'using_hardware', 'required_hardware', 'width', 'height',
            'bitrate_mode', 'encoder_id', 'encoder_registry_hardware',
            'hardware_property_readback', 'hardware_property_read_status',
            'bitrate_target_requested_bps', 'bitrate_target_current_bps',
            'bitrate_target_set_status', 'bitrate_target_read_status', 'bitrate_target_readback',
            'burst_window_requested', 'burst_bytes_requested', 'burst_seconds_requested',
            'vbr_rate_limits_requested', 'vbr_rate_limits_set_attempted',
            'vbr_rate_limits_read_status', 'vbr_rate_limits_readback', 'vbr_rate_limit_status',
            'accepted_bitrate', 'sequence', 'input_frames', 'output_frames',
            'dropped_or_failed', 'pending_at_end', 'output_bitrate_bps', 'output_fps'}
    speed_boolean_fields = {'prioritize_speed_requested', 'prioritize_speed_supported',
                            'prioritize_speed_readback'}
    speed_status_fields = {'prioritize_speed_supported_properties_status',
                          'prioritize_speed_set_status', 'prioritize_speed_read_status'}
    speed_states = {'not_requested', 'api_unavailable', 'supported_properties_query_failed',
                    'unsupported', 'set_failed', 'read_failed', 'readback_not_boolean',
                    'confirmed', 'readback_mismatch'}
    pool_boolean_fields = {'pixel_pool_attributes_verified', 'pixel_pool_buffer_verified'}
    pool_nonnegative_fields = {'pixel_pool_pixel_format_readback', 'pixel_pool_probe_pixel_format'}
    pool_positive_fields = {'pixel_pool_width_readback', 'pixel_pool_height_readback',
                            'pixel_pool_probe_width', 'pixel_pool_probe_height',
                            'pixel_pool_probe_bytes_per_row'}
    events = []
    for line in path.read_text(errors='replace').splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('event') in ('ready', 'bitrate', 'summary'):
            clean = {key: value[key] for key in keys if key in value}
            for key in speed_boolean_fields:
                if key in value and (value[key] is None or type(value[key]) is bool):
                    clean[key] = value[key]
            for key in speed_status_fields:
                if key in value and (value[key] is None or type(value[key]) is int):
                    clean[key] = value[key]
            if (type(value.get('prioritize_speed_status')) is str
                    and value['prioritize_speed_status'] in speed_states):
                clean['prioritize_speed_status'] = value['prioritize_speed_status']
            if (type(value.get('pixel_pool_mode')) is str
                    and value['pixel_pool_mode'] in ('manual', 'session')):
                clean['pixel_pool_mode'] = value['pixel_pool_mode']
            for key in pool_boolean_fields:
                if key in value and type(value[key]) is bool:
                    clean[key] = value[key]
            for key in pool_nonnegative_fields:
                if key in value and type(value[key]) is int and value[key] >= 0:
                    clean[key] = value[key]
            for key in pool_positive_fields:
                if key in value and type(value[key]) is int and value[key] > 0:
                    clean[key] = value[key]
            if type(value.get('fps_expected')) is int and value['fps_expected'] in (30, 60, 120):
                clean['fps_expected'] = value['fps_expected']
            if ('expected_frame_rate_read_status' in value
                    and type(value['expected_frame_rate_read_status']) is int):
                clean['expected_frame_rate_read_status'] = value['expected_frame_rate_read_status']
            if ('expected_frame_rate_readback' in value and
                    (value['expected_frame_rate_readback'] is None or
                     type(value['expected_frame_rate_readback']) in (int, float)
                     and value['expected_frame_rate_readback'] in (30, 60, 120))):
                clean['expected_frame_rate_readback'] = value['expected_frame_rate_readback']
            events.append(clean)
    return events


def worker_timing_samples(path):
    """Owned worker only, using the existing numeric timing sanitizer."""
    from scripts.probes.collect_worker_timings import sanitize
    rows = deque(maxlen=512)
    evicted = 0
    for line in path.read_text(errors='replace').splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        clean = sanitize(value, 0) if isinstance(value, dict) else None
        if clean is not None:
            if len(rows) == rows.maxlen:
                evicted += 1
            rows.append(clean)
    return {'scope': 'owned_host_worker_numeric_pipeline_and_phase_samples_not_phone_latency',
            'samples': list(rows), 'samples_evicted': evicted}


def pacing_configuration(socket_pacer, video_gate, native_events, wire_bitrate):
    """Separate socket waits, dependency guard and observed native budget.

    A requested packetizer budget is not proof of its actual scheduling. Only
    native summary readback and optional frame-output events establish what was
    reported by the process; neither is a WAN packet-capture measurement.
    """
    summaries = [event for event in native_events if event.get('event') == 'summary'
                 and type(event.get('wire_bitrate')) is int]
    budget_readback = summaries[-1]['wire_bitrate'] if summaries else None
    frame_outputs = [event for event in native_events if event.get('event') == 'frame_output'
                     and type(event.get('actual_sleep_us')) is int]
    return {
        'socket_pacing_requested': socket_pacer is not None,
        'socket_wait_enabled': bool(socket_pacer and socket_pacer.wait_enabled),
        'socket_deadline_reference_guard_enabled': bool(video_gate and video_gate.guard),
        'socket_reservation_and_serialization_checks_enabled': socket_pacer is not None,
        'native_pacing_requested': True,
        'native_wire_budget_requested_bps': wire_bitrate,
        'native_wire_budget_readback_bps': budget_readback,
        'native_wire_budget_readback_matches': (budget_readback == wire_bitrate if summaries else None),
        'native_timed_wait_observed': (any(event['actual_sleep_us'] > 0 for event in frame_outputs)
                                       if frame_outputs else None),
        'native_wait_observation_frame_events': len(frame_outputs),
        'native_frame_deadline_requested_us': 80_000,
        'native_bounds_modified_by_socket_wait_experiment': False,
        'scope': 'configuration_and_native_process_readback_not_WAN_pacing_acceptance',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='3B15AL00M9U00000')
    parser.add_argument('--experimental-client', action='store_true',
                        help='Use only the isolated experiment client and instrumentation package pair')
    parser.add_argument('--bind-ip', required=True)
    parser.add_argument('--peer-ip', required=True)
    parser.add_argument('--interface', default='en7')
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--native-encoder', type=Path, help='Independent experimental binary; deployed encoder remains unchanged')
    parser.add_argument('--encoder-prioritize-speed', choices=('true', 'false'), default=None,
                        help='Optional VT speed/quality hint; requires an independent experimental native encoder')
    parser.add_argument('--burst-bytes', type=int)
    parser.add_argument('--burst-seconds', type=float)
    parser.add_argument('--display-hz', type=int, choices=(0, 60, 90, 120), default=0, help='Optional same-resolution physical phone display mode for controlled comparisons')
    parser.add_argument('--content-hint-fps', type=int, choices=(0, 30, 60, 90, 120), default=None,
                        help='Probe-only Surface content hint; omitted preserves installed decoder behavior, 0 clears the hint')
    parser.add_argument('--surface-submit-lead-ms', type=int, choices=(0, 8, 16), default=0,
                        help='Probe-only late Surface submission; 0 preserves immediate future-timestamp submission')
    parser.add_argument('--capture-trace', action='store_true',
                        help='Bounded metadata-only capture/VT trace with an independent experimental encoder')
    parser.add_argument('--raw-queue-policy', choices=('fifo', 'latest'), default=None,
                        help='Explicit per-test source pixel queue; omitted retains existing host/environment default')
    parser.add_argument('--runtime', type=Path, default=Path.home()/'Documents/ChatGPT/others/android-remote/m1-compare')
    parser.add_argument('--seconds', type=int, default=40)
    parser.add_argument('--fps', type=int, choices=(60, 120), default=60)
    parser.add_argument('--raw-submit-fps', type=int, choices=(30, 60, 120), default=None,
                        help='Experimental host raw submission budget; native and phone FPS stay at --fps, '
                             'and gRPC sampling is unchanged')
    parser.add_argument('--bitrate', type=int, default=4000000)
    parser.add_argument('--wire-bitrate', type=int, default=40000000)
    parser.add_argument('--recovery-cooldown-ms', type=int, choices=(100, 200, 500), default=500,
                        help='Optional matching native/encoder/feedback cooldown; epoch limit remains six')
    parser.add_argument('--max-size', type=int, choices=(960, 1200, 1280, 1600, 1920, 2400), default=1280)
    parser.add_argument('--buffer', type=int, default=80)
    parser.add_argument('--audio', action='store_true', help='Carry AAC and shared-clock playback over UDP')
    parser.add_argument('--touch', action='store_true', help='Enable native touchscreen snapshots over UDP')
    parser.add_argument('--async-video', action='store_true', help='Experimental bounded decoder-input worker; receive loop never waits for codec input')
    parser.add_argument('--arrival-clock', action='store_true', help='Experimental shared arrival clock without decoder-driven reanchoring')
    parser.add_argument('--adapt-budget', action='store_true', help='Reduce encoder target for rejected recovery frames')
    parser.add_argument('--network-feedback', action='store_true', help='Experimental authenticated interval-pressure policy and host-clock UDP RTT; not GCC')
    parser.add_argument('--socket-pacing', action='store_true', help='Enforce estimated full IPv4 wire pacing at actual video socket sends; audio/touch have priority')
    parser.add_argument('--disable-socket-wait', action='store_true',
                        help='Single-variable experiment with --socket-pacing: skip only Python socket timed waits; '
                             'retain socket deadlines/reference guard, reservations and native pacing')
    parser.add_argument('--pacing-burst-bytes', type=int, choices=(0, 2048, 4096), default=0,
                        help='Explicit bounded catch-up credit for native and optional socket pacing; default strict')
    parser.add_argument('--diagnostic-events', action='store_true', help='Bounded numeric native frame, socket frame and phone inbox events')
    parser.add_argument('--sample-surfaces', action='store_true', help='Autonomously sample both actual SurfaceFlinger timelines')
    parser.add_argument('--exercise-touch', action='store_true', help='One bounded OS-injected swipe on the phone during the real UDP session')
    parser.add_argument('--drop-video-every', type=int, default=0, help='Probe-only deterministic datagram loss: 50 means 2 percent; 0 disables')
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.disable_socket_wait and not args.socket_pacing:
        parser.error('--disable-socket-wait requires --socket-pacing so deadline/reference guards stay enabled')
    if args.surface_submit_lead_ms > 0 and not args.experimental_client:
        parser.error('--surface-submit-lead-ms > 0 requires --experimental-client')
    prioritize_speed = None if args.encoder_prioritize_speed is None else args.encoder_prioritize_speed == 'true'
    try:
        experimental_prioritize_speed_arguments(args.native_encoder, prioritize_speed)
        experimental_raw_submit_arguments(args.native_encoder, args.raw_submit_fps, args.experimental_client)
    except ValueError as error:
        parser.error(str(error))
    if args.capture_trace and args.native_encoder is None:
        parser.error('--capture-trace requires an independent experimental native encoder')
    if args.exercise_touch and not args.touch:
        parser.error('--exercise-touch requires --touch')
    if args.drop_video_every != 0 and args.drop_video_every < 20:
        parser.error('Loss injection must be disabled or at most 5 percent')
    if not 10 <= args.seconds <= 120 or not 30 <= args.buffer <= 100 or not 4_000_000 <= args.bitrate <= 24_000_000:
        parser.error('Experiment duration/buffer/bitrate outside authorized bounded matrix')
    if not 500_000 <= args.wire_bitrate <= 40_000_000:
        parser.error('Experiment wire budget outside 0.5..40 Mbps')
    if not args.packetizer.is_file() or not os.access(args.packetizer, os.X_OK):
        parser.error('Build the independently verified native packetizer first')
    try:
        experimental_burst_arguments(args.native_encoder, 'VBR', args.burst_bytes, args.burst_seconds)
    except ValueError as error:
        parser.error(str(error))
    if args.native_encoder is not None and (not args.native_encoder.is_file() or not os.access(args.native_encoder, os.X_OK)):
        parser.error('Experimental native encoder is not executable')
    bind_ip, peer_ip = allowed_address(args.bind_ip), allowed_address(args.peer_ip)
    if sys.platform != 'darwin':
        parser.error('This M1 runner requires Darwin IP_BOUND_IF to prevent proxy fallback')
    adb = [str(Path.home()/'Library/Android/sdk/platform-tools/adb'), '-s', args.serial]
    client = client_configuration(args.experimental_client)
    instrumentation_target_verified = read_instrumentation_target(adb, args.experimental_client)
    # Validate the source before writing the phone's experiment configuration
    # or opening a touch bridge. Unknown geometry must never fall back to 720p.
    touch_geometry = read_touch_geometry(adb[0]) if args.touch else None

    def root(command, **kwargs):
        return subprocess.run(adb + ['shell', 'su -c ' + shlex.quote(command)],
                              check=True, capture_output=True, **kwargs)

    uid = root('stat -c %u /data/user/0/' + client['client_package']).stdout.decode().strip()
    if not uid.isdigit():
        raise RuntimeError('Cannot determine installed app owner')
    key, session_tag = secrets.token_bytes(32), secrets.randbits(64)
    config = {'key_b64': base64.b64encode(key).decode(), 'session_tag_hex': f'{session_tag:016x}',
              'peer_host': bind_ip, 'peer_port': 15961, 'bind_port': 15960,
              'seconds': args.seconds, 'fps': args.fps, 'buffer_ms': args.buffer,
              'audio_enabled': args.audio, 'touch_enabled': args.touch,
              'async_video': args.async_video,
              'decoder_reanchor_enabled': not args.arrival_clock,
              'display_hz': args.display_hz,
              'surface_submit_lead_ms': args.surface_submit_lead_ms,
              'network_feedback': args.network_feedback,
              'diagnostic_events': args.diagnostic_events,
              'video_release': 'scheduled', 'profile': 'M1 real hardware video / authenticated UDP / FEC10+2'}
    if args.content_hint_fps is not None:
        config['content_hint_fps'] = args.content_hint_fps
    done = threading.Event()
    threads, failures = [], []
    native_events = deque(maxlen=16_000)
    counts = {'sent_datagrams': 0, 'sent_encrypted_bytes': 0, 'invalid_feedback': 0,
              'ready_messages': 0, 'keyframe_feedback': 0, 'keyframes_forwarded': 0,
              'native_keyframe_requests': 0, 'native_keyframes_forwarded': 0,
              'native_events_evicted': 0, 'network_encoder_updates': 0,
              'socket_chain_recovery_requests': 0}
    phone = native = hardware = udp = sender = touch_bridge = video_gate = None
    phone_started = None
    initial_error = report_error = None
    report_written = False
    first_send_ns = None
    started = datetime.now(timezone.utc).isoformat()
    replay = ReplayWindow()
    control_lock = threading.Lock()
    last_idr = 0.0
    recovery_cooldown_s = args.recovery_cooldown_ms / 1000
    recovery = RecoveryController(args.bitrate, args.wire_bitrate, cooldown_s=recovery_cooldown_s)
    network_policy = NetworkFeedbackController(args.bitrate, args.wire_bitrate) if args.network_feedback else None
    socket_pacer = SocketPacer(args.wire_bitrate, burst_bytes=args.pacing_burst_bytes, wait=done.wait,
                              wait_enabled=not args.disable_socket_wait) if args.socket_pacing else None
    last_encoder_target = args.bitrate
    pressure_observed = 0
    recovery_actions = []
    recovery_lock = threading.Lock()
    collectors = []
    surface_reports = {}
    worker_log = None
    trace_directory = None
    capture_trace_path = None

    def background(name, function):
        def run():
            try:
                function()
            except Exception as error:
                if not done.is_set():
                    failures.append({'stage': name, 'error_type': type(error).__name__})
                    done.set()
        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        threads.append(thread)

    try:
        initialize_experimental_client_files(root, uid, args.experimental_client)
        root('rm -f ' + client['report_file'])
        root('umask 077 && cat > ' + client['session_file'] + ' && chown ' + uid + ':' + uid + ' ' + client['session_file'] +
             ' && chmod 600 ' + client['session_file'] + ' && restorecon ' + client['session_file'],
             input=json.dumps(config).encode())
        config = None
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if args.interface not in ('en7', 'en0'):
            raise ValueError('Only explicitly selected M1 physical interfaces are permitted')
        ifindex = socket.if_nametoindex(args.interface)
        udp.setsockopt(socket.IPPROTO_IP, 25, ifindex)
        if udp.getsockopt(socket.IPPROTO_IP, 25) != ifindex:
            raise RuntimeError('Physical UDP interface readback mismatch')
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
        udp.bind((bind_ip, 15961))
        udp.connect((peer_ip, 15960))
        udp.settimeout(.25)
        sender = AuthenticatedSender(udp, key, session_tag, args.drop_video_every, pacer=socket_pacer)
        command = 'am instrument -w ' + client['component']
        phone = subprocess.Popen(adb + ['shell', 'su -c ' + shlex.quote(command)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        phone_started = time.monotonic()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                payload = open_packet(key, session_tag, udp.recv(1401), replay, CLIENT_NONCE)
                if payload == b'READY':
                    counts['ready_messages'] += 1
                    break
            except (socket.timeout, ConnectionRefusedError):
                pass
            except Exception:
                counts['invalid_feedback'] += 1
        else:
            raise TimeoutError('Authenticated UDP READY not received')
        with tempfile.NamedTemporaryFile(prefix='huoguo-udp-worker-', suffix='.log', delete=False) as log:
            worker_log = Path(log.name)
        if args.capture_trace:
            trace_directory = tempfile.TemporaryDirectory(prefix='huoguo-capture-trace-')
            capture_trace_path = Path(trace_directory.name)/'capture.jsonl'
        hardware = HostHardwareSession(args.runtime, 'emulator-5556', 'RemoteAndroid17Compare',
                                       args.max_size, args.bitrate, args.fps,
                                       'ADAPTIVE_VBR' if args.adapt_budget or args.network_feedback else 'VBR', sps_low_delay=True,
                                       native_encoder=args.native_encoder, burst_bytes=args.burst_bytes,
                                       burst_seconds=args.burst_seconds, worker_log=worker_log,
                                       capture_trace=capture_trace_path, raw_queue_policy=args.raw_queue_policy,
                                       prioritize_speed=prioritize_speed,
                                       **({'raw_submit_fps': args.raw_submit_fps,
                                           'matched_experimental_client': args.experimental_client is True
                                           and instrumentation_target_verified is True}
                                          if args.raw_submit_fps is not None else {}))
        channels = {role: hardware.channel(role) for role in ('video', 'audio', 'control')}
        if args.touch:
            from udp_touch_control import UdpTouchBridge
            def write_touch(data):
                with control_lock:
                    channels['control'].sendall(data)
            touch_bridge = UdpTouchBridge(touch_geometry['effective_width'],
                                          touch_geometry['effective_height'], write_touch,
                                          lambda data: sender.send(data, 'touch_ack'))
        native_command = [str(args.packetizer), str(args.wire_bitrate)]
        # Default omits the new parameter for legacy binary compatibility.
        if args.recovery_cooldown_ms != 500 or args.diagnostic_events or args.pacing_burst_bytes:
            native_command.append(str(args.recovery_cooldown_ms * 1000))
        if args.diagnostic_events or args.pacing_burst_bytes:
            native_command.append('1' if args.diagnostic_events else '0')
        if args.pacing_burst_bytes:
            native_command.append(str(args.pacing_burst_bytes))
        native = subprocess.Popen(native_command,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def request_keyframe(origin):
            nonlocal last_idr
            # Both loss paths share a bounded control request rate. In
            # particular, native admission can lose the very first IDR before
            # a phone has any media to trigger its own recovery feedback.
            with control_lock:
                now = time.monotonic()
                if now-last_idr < recovery_cooldown_s:
                    return
                channels['control'].sendall(b'\x11')
                counts['keyframes_forwarded'] += 1
                if origin == 'native':
                    counts['native_keyframes_forwarded'] += 1
                elif origin == 'socket':
                    counts['socket_chain_recovery_requests'] += 1
                last_idr = now

        # The lock order for bitrate updates is always recovery -> control.
        # No pacing wait occurs under either lock. All target changes use the
        # existing 240 command and value ACK; this is not a sequenced ACK API.
        def apply_encoder_target(target, request_idr=False, origin='network'):
            nonlocal last_encoder_target, last_idr
            changed = target != last_encoder_target
            command = (b'\xf0'+struct.pack('>I', target)) if changed else b''
            if request_idr:
                command += b'\x11'
            if not command:
                return
            with control_lock:
                channels['control'].sendall(command)
                if changed:
                    last_encoder_target = target
                    if network_policy:
                        network_policy.requested(target)
                if request_idr:
                    last_idr = time.monotonic()
                    counts['keyframes_forwarded'] += 1
                    counts['native_keyframes_forwarded'] += origin == 'native'
                if changed and origin == 'network':
                    counts['network_encoder_updates'] += 1

        if args.socket_pacing or args.diagnostic_events:
            video_gate = SocketVideoGate(sender, lambda reason: request_keyframe('socket'), guard=args.socket_pacing)

        def feed():
            while not done.is_set():
                chunk = channels['video'].recv(65536)
                if not chunk:
                    return
                native.stdin.write(chunk)
                native.stdin.flush()

        def send():
            nonlocal first_send_ns
            while not done.is_set():
                size = struct.unpack('>H', read_exact(native.stdout, 2))[0]
                if not 56 < size <= 1080:
                    raise ValueError('Native UDP shard length')
                payload = read_exact(native.stdout, size)
                size = video_gate.send(payload) if video_gate else sender.send(payload, 'video')
                if size and first_send_ns is None:
                    first_send_ns = time.monotonic_ns()
                if size:
                    counts['sent_datagrams'] += 1
                    counts['sent_encrypted_bytes'] += size

        def audio():
            stream = channels['audio']
            codec = struct.unpack('>I', read_exact(stream, 4))[0]
            if codec in (0, 1):
                return
            if args.audio:
                from audio_datagram import packetize_audio, AAC
                if codec != AAC:
                    raise ValueError('The UDP audio experiment requires AAC')
            frame_id = 0
            config_record = None
            last_config_send = 0.0
            while not done.is_set():
                pts, size = struct.unpack('>QI', read_exact(stream, 12))
                if size > 1024*1024:
                    raise ValueError('Audio drain bounds')
                body = read_exact(stream, size)
                if not args.audio:
                    continue
                frame_id += 1
                packets = packetize_audio(pts, body, frame_id, codec=codec)
                if pts & (1 << 62):
                    config_record = packets
                elif config_record is not None and time.monotonic()-last_config_send >= 1:
                    # Configuration only is repeated; each encrypted datagram
                    # gets a fresh sequence. Old audio media is never retransmitted.
                    for packet in config_record:
                        sender.send(packet, 'audio')
                    last_config_send = time.monotonic()
                for packet in packets:
                    sender.send(packet, 'audio')

        def drain_control():
            while not done.is_set():
                reply = read_control(channels['control'])
                if reply[0] == 240:
                    with recovery_lock:
                        value = struct.unpack_from('>I', reply, 1)[0]
                        recovery.acknowledge(value)
                        if network_policy:
                            network_policy.acknowledge(value)

        def feedback():
            nonlocal pressure_observed
            while not done.is_set():
                try:
                    message = open_packet(key, session_tag, udp.recv(1401), replay, CLIENT_NONCE)
                except socket.timeout:
                    continue
                except Exception:
                    counts['invalid_feedback'] += 1
                    continue
                if message == b'READY':
                    counts['ready_messages'] += 1
                elif message == b'KEYFRAME':
                    counts['keyframe_feedback'] += 1
                    request_keyframe('phone')
                elif args.touch and message.startswith(b'HGUT'):
                    touch_bridge.submit(message)
                elif network_policy and message.startswith(b'HGPR'):
                    with recovery_lock:
                        network_policy.pong(message)
                elif network_policy and message.startswith(b'HGUF'):
                    lane = sender.snapshot()['video']
                    sent_wire = lane['encrypted_bytes']+28*lane['datagrams']
                    local_pressure = 0 if video_gate is None else sum(video_gate.counts[k] for k in
                        ('deadline_dropped_frames', 'incomplete_output_frames'))
                    with recovery_lock:
                        network_policy.set_recovery_ceiling(recovery.target_bps)
                        previous_valid = network_policy.counters['valid_feedback']
                        decision = network_policy.consume(message, host_video_wire_bytes=sent_wire,
                            local_pressure=local_pressure > pressure_observed)
                        if network_policy.counters['valid_feedback'] > previous_valid:
                            pressure_observed = local_pressure
                        if decision is not None:
                            apply_encoder_target(min(decision.requested_bitrate_bps, recovery.target_bps))

        def network_ticks():
            while not done.wait(.1):
                with recovery_lock:
                    query = network_policy.ping()
                if query is not None:
                    sender.send(query, 'network_feedback')

        def events():
            nonlocal last_idr
            for line in native.stderr:
                if len(line) > 8192:
                    raise ValueError('Native event length')
                value = json.loads(line)
                if isinstance(value, dict) and value.get('event') in ('summary', 'encoder_budget_feedback', 'frame_rejected', 'request_idr', 'recovery_complete', 'recovery_exhausted', 'frame_source', 'frame_output'):
                    if len(native_events) == native_events.maxlen:
                        counts['native_events_evicted'] += 1
                    native_events.append(value)
                    if value.get('event') == 'request_idr':
                        counts['native_keyframe_requests'] += 1
                    if args.adapt_budget or args.network_feedback:
                        with recovery_lock:
                            decision = recovery.consume(value)
                            if decision is not None:
                                target = decision.requested_bitrate_bps
                                if network_policy:
                                    network_policy.set_recovery_ceiling(target)
                                    target = min(target, network_policy.target_bps)
                                apply_encoder_target(target, decision.request_idr, 'native')
                                recovery_actions.append(dict(decision.report(), actual_combined_target_bps=target))
                    elif value.get('event') == 'request_idr':
                        request_keyframe('native')

        for name, function in (('video_feed', feed), ('udp_send', send), ('audio', audio),
                               ('control_drain', drain_control), ('feedback', feedback), ('native_events', events)):
            background(name, function)
        if network_policy:
            background('network_feedback_ticks', network_ticks)
        hardware.start()
        startup_limit = time.monotonic() + 20
        while first_send_ns is None and not done.is_set() and time.monotonic() < startup_limit:
            done.wait(.05)
        if first_send_ns is None:
            raise RuntimeError('No authenticated UDP video sent')
        if args.sample_surfaces:
            sampler = ROOT/'scripts/probes/measure_surface_cadence.py'
            duration = max(2, args.seconds-3)
            for role, serial, package in (('source', 'emulator-5556', 'app.morphe.android.youtube'),
                                         ('phone', args.serial, client['client_package'])):
                output = args.output.with_name(args.output.stem+'-'+role+'-surface.json')
                command = [sys.executable, str(sampler), '--serial', serial, '--package', package,
                           '--seconds', str(duration), '--wait-layer', '3', '--output', str(output)]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                collectors.append((role, process, output))
        if args.exercise_touch:
            def exercise_touch():
                if done.wait(6):
                    return
                # Phone OS input, not direct host coordinates or a Mac mouse.
                # Fixed OnePlus 15 test geometry; root verified 1080x2354.
                command = ['shell', 'input', 'swipe', '540', '1650', '540', '1250', '450']
                subprocess.run(adb+command, check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=8)
            background('phone_OS_touch_exercise', exercise_touch)
        remaining = args.seconds - (time.monotonic_ns()-first_send_ns)/1e9
        done.wait(max(0, remaining)+.3)
    except Exception as error:
        initial_error = error
        failures.append({'stage': 'startup_or_run', 'error_type': type(error).__name__})
    finally:
        done.set()
        if touch_bridge:
            touch_bridge.close()
        if hardware:
            hardware.close()
        if udp:
            udp.close()
        # A stopped UDP reader can fill the native stdout pipe and block the
        # feed thread inside BufferedWriter.write. Stop this owned subprocess
        # before closing/joining its input writer, so cleanup cannot deadlock.
        if native and native.poll() is None:
            native.terminate()
            try:
                native.wait(timeout=3)
            except subprocess.TimeoutExpired:
                native.kill(); native.wait(timeout=2)
        for thread in threads:
            if thread.name != 'native_events':
                thread.join(timeout=2)
        if native:
            if native.stdin and not native.stdin.closed:
                native.stdin.close()
        for thread in threads:
            if thread.name == 'native_events':
                thread.join(timeout=2)
        for role, process, output in collectors:
            try:
                process.communicate(timeout=5)
                if process.returncode == 0 and output.is_file():
                    value = json.loads(output.read_text())
                    surface_reports[role] = {k: value.get(k) for k in
                        ('scope', 'seconds', 'display_vsync_ns', 'presented_frames', 'fps', 'cadence_fps', 'gaps_ms', 'limitations')}
                    surface_reports[role]['scope'] = ('guest video app SurfaceFlinger actual-present timestamps'
                        if role == 'source' else 'phone client SurfaceFlinger actual-present timestamps; not optical panel or touch latency')
                else:
                    surface_reports[role] = {'measurement_available': False, 'exit_code': process.returncode}
            except subprocess.TimeoutExpired:
                process.terminate()
                try: process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.communicate(timeout=2)
                surface_reports[role] = {'measurement_available': False, 'timed_out': True}
        try:
            if phone is None and initial_error is not None:
                raise initial_error
            if phone:
                try:
                    # A phone without its first media packet waits seconds+30
                    # from launch. Do not truncate that diagnostic window and
                    # hide an earlier hardware startup error behind no report.
                    timeout = max(1, phone_started + args.seconds + 35 - time.monotonic())
                    output, _ = phone.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    phone.terminate(); phone.communicate(timeout=5)
                    raise RuntimeError('Phone UDP probe exceeded bounded deadline')
                phone_report = read_phone_report(output, lambda: root('cat ' + client['report_file']).stdout)
                surface_submit_report_verified = verify_surface_submit_report(
                    phone_report, args.surface_submit_lead_ms, args.experimental_client)
                raw_submit_readback = raw_submit_experiment_readback(
                    hardware, phone_report, args.fps, args.raw_submit_fps)
                report = {'scope': 'Real M1 hardware media over authenticated UDP to phone; isolated component.',
                          'experimental_client': args.experimental_client,
                          'client_package': client['client_package'], 'probe_package': client['probe_package'],
                          'instrumentation_target_verified': instrumentation_target_verified,
                          'surface_submit_report_verified': surface_submit_report_verified,
                          'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
                          'transport': 'AES-256-GCM UDP datagrams with native nanors FEC10+2',
                          'route': 'explicit private peer; physically bound interface',
                          'bind_ip': bind_ip, 'peer_ip': peer_ip, 'interface': args.interface,
                          'requested_video_bps': args.bitrate, 'wire_burst_budget_bps': args.wire_bitrate,
                          'experimental_encoder': args.native_encoder is not None,
                          'requested_encoder_prioritize_speed': prioritize_speed,
                          'requested_encoder_fps': args.fps,
                          'requested_raw_submit_fps': args.raw_submit_fps,
                          'raw_submit_budget_readback': raw_submit_readback,
                          'encoder_burst_bytes': args.burst_bytes, 'encoder_burst_seconds': args.burst_seconds,
                          'recovery_cooldown_ms': args.recovery_cooldown_ms,
                          'requested_phone_display_hz': args.display_hz,
                          'requested_surface_content_hint_fps': args.content_hint_fps,
                          'requested_surface_submit_lead_ms': args.surface_submit_lead_ms,
                          'capture_trace_enabled': args.capture_trace,
                          'requested_raw_queue_policy': args.raw_queue_policy,
                          'audio_enabled': args.audio, 'touch_enabled': args.touch, 'adaptive_recovery_enabled': args.adapt_budget,
                          'network_feedback_enabled': args.network_feedback,
                          'socket_pacing_enabled': args.socket_pacing,
                          'socket_wait_enabled': bool(socket_pacer and socket_pacer.wait_enabled),
                          'socket_guard_enabled': bool(video_gate and video_gate.guard),
                          'pacing_configuration': pacing_configuration(socket_pacer, video_gate,
                                                                       native_events, args.wire_bitrate),
                          'pacing_catchup_credit_bytes': args.pacing_burst_bytes,
                          'diagnostic_events_enabled': args.diagnostic_events,
                          'async_video_enabled': args.async_video,
                          'decoder_reanchor_enabled': not args.arrival_clock,
                          'udp_lanes': sender.snapshot() if sender else {},
                          'encoder_recovery': recovery.report(), 'encoder_recovery_actions': recovery_actions,
                          'network_feedback': network_policy.report() if network_policy else {},
                          'socket_pacing': socket_pacer.snapshot() if socket_pacer else {},
                          'socket_video': video_gate.snapshot() if video_gate else {},
                          'touch': touch_bridge.stats() if touch_bridge else {},
                          'touch_source_geometry': touch_geometry if touch_geometry else {},
                          'actual_surface_samples': surface_reports,
                          'touch_exercise': 'One OS-injected phone swipe; not human digitizer or multi-finger latency acceptance' if args.exercise_touch else 'No automated touch exercise',
                          'test_loss_injection': {'video_drop_every': args.drop_video_every, 'pattern': 'periodic pre-socket drop; not a measured WAN loss distribution'},
                          'host_legacy_counts_scope': 'video datagrams only; udp_lanes includes audio and touch acknowledgements',
                          'source': args.source, 'host': counts, 'host_failures': failures,
                          'native_events': list(native_events), 'phone': phone_report,
                          'hardware_encoder_readback': encoder_readback(worker_log) if worker_log else [],
                          'host_worker_timing_samples': worker_timing_samples(worker_log) if worker_log else {},
                          'not_measured': ['Acoustic audio/video synchronization', 'Touch latency', 'WAN NAT/P2P traversal', 'Remote V50']
                              + ([] if all(surface_reports.get(role, {}).get('presented_frames') is not None
                                           for role in ('source', 'phone')) else ['Concurrent independent source and phone Surface FPS'])}
                if capture_trace_path is not None:
                    from scripts.probes.capture_trace_analysis import read_sanitized_trace, analyze_trace
                    paths = [capture_trace_path, Path(str(capture_trace_path)+'.native.jsonl')]
                    trace = read_sanitized_trace(paths)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    trace_output = args.output.with_name(args.output.stem+'-capture-trace.json')
                    trace_output.write_text(json.dumps(trace, indent=2)+'\n')
                    report['capture_trace_analysis'] = analyze_trace(trace)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
                report_written = True
                print(json.dumps({'output': str(args.output), 'host': counts, 'host_failures': failures,
                                  'phone_failure': phone_report.get('failure_class')}))
        except Exception as error:
            report_error = error
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({
                'scope': 'Failed video-only UDP component attempt; no playback acceptance.',
                'experimental_client': args.experimental_client,
                'client_package': client['client_package'], 'probe_package': client['probe_package'],
                'instrumentation_target_verified': instrumentation_target_verified,
                'surface_submit_report_verified': False,
                'requested_surface_submit_lead_ms': args.surface_submit_lead_ms,
                'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
                'source': args.source, 'host': counts, 'host_failures': failures,
                'report_failure_class': type(error).__name__,
                'native_events': list(native_events), 'phone': None,
                'hardware_encoder_readback': encoder_readback(worker_log) if worker_log else [],
                'host_worker_timing_samples': worker_timing_samples(worker_log) if worker_log else {},
                'network_feedback': network_policy.report() if network_policy else {},
                'socket_pacing_enabled': args.socket_pacing,
                'socket_wait_enabled': bool(socket_pacer and socket_pacer.wait_enabled),
                'socket_guard_enabled': bool(video_gate and video_gate.guard),
                'pacing_configuration': pacing_configuration(socket_pacer, video_gate,
                                                             native_events, args.wire_bitrate),
                'socket_pacing': socket_pacer.snapshot() if socket_pacer else {},
                'socket_video': video_gate.snapshot() if video_gate else {},
                'requested_video_bps': args.bitrate,
                'requested_encoder_prioritize_speed': prioritize_speed,
                'requested_encoder_fps': args.fps,
                'requested_raw_submit_fps': args.raw_submit_fps,
                'raw_submit_budget_readback': getattr(hardware, 'raw_submit_budget_readback', None),
                'valid_playback_test': False,
            }, ensure_ascii=False, indent=2)+'\n')
            report_written = True
        finally:
            if sender:
                sender.close()
            key = None
            try:
                root('rm -f ' + client['session_file'] + ' ' + client['report_file'])
            finally:
                if worker_log:
                    worker_log.unlink(missing_ok=True)
                if trace_directory is not None:
                    trace_directory.cleanup()
    if initial_error is not None:
        if report_written:
            raise SystemExit('UDP component failed: ' + type(initial_error).__name__) from None
        raise initial_error
    if report_error is not None:
        if report_written:
            raise SystemExit('UDP component failed: ' + type(report_error).__name__) from None
        raise report_error


if __name__ == '__main__':
    main()
