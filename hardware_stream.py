"""Apple hardware video adapter; audio and input retain scrcpy's guest service.

The gateway imports only standard-library code. A separate local worker uses
the pinned gRPC environment. No new network listener or credential is created.
Video uses complete frames, a small pending queue and mandatory hardware;
audio timestamps are mapped onto the framebuffer's Unix microsecond clock.
"""
import argparse
import collections
import json
import math
import os
import pathlib
import queue
import re
import secrets
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from stream_settings import RESOLUTION_MAX_SIZES
from host_timing_trace import HostTimingTrace

CONFIG_FLAG = 1 << 62
PTS_MASK = (1 << 61) - 1

TEARDOWN_SCHEMA = 'owned-process-teardown-parent-v1'
TEARDOWN_ROLES = ('worker-parent', 'encoder-parent')
TEARDOWN_MAX_BYTES = 4096
TEARDOWN_MAX_ROWS = 24
TEARDOWN_OPS = frozenset(range(1, 16))
TEARDOWN_EXIT_RESULT_OPS = frozenset((3, 5, 6, 8, 11, 13, 15))


def teardown_failure_code(failure):
    """Closed classes only; never exception text, argv or a foreign class name."""
    if isinstance(failure, ProcessLookupError):
        code = 1
    elif isinstance(failure, subprocess.TimeoutExpired):
        code = 2
    elif isinstance(failure, PermissionError):
        code = 3
    elif isinstance(failure, OSError):
        code = 4
    elif isinstance(failure, Exception):
        code = 5
    else:
        code = 6
    try:
        value = getattr(failure, 'errno', None)
    except BaseException:
        # A diagnostic attribute getter must not replace the action's primary.
        value = None
    return code, value if type(value) is int and 0 <= value <= 4095 else 0


def _teardown_integer(value, signed=False):
    return type(value) is int and (-(1 << 31) if signed else 0) <= value < (1 << 31)


def validate_teardown_receipt(value, role):
    """Validate parent observations only; none is native producer acceptance."""
    expected = {'schema', 'role', 'clock_domain', 'parent_pid', 'child_pid',
        'identity_bound', 'pgid_known', 'observed_pgid', 'pgid_failure_code',
        'pgid_errno', 'cleanup_entered', 'cleanup_returned', 'observer_errors',
        'operations_dropped', 'operation_rows'}
    if (type(value) is not dict or set(value) != expected or role not in TEARDOWN_ROLES
            or value['schema'] != TEARDOWN_SCHEMA or value['role'] != role
            or value['clock_domain'] != 'host_clock_gettime_CLOCK_MONOTONIC_ns'):
        return False
    for key in ('identity_bound', 'pgid_known', 'cleanup_entered', 'cleanup_returned'):
        if type(value[key]) is not bool:
            return False
    for key in ('parent_pid', 'child_pid', 'observed_pgid', 'pgid_failure_code',
                'pgid_errno', 'observer_errors', 'operations_dropped'):
        if not _teardown_integer(value[key]):
            return False
    if (value['parent_pid'] == 0 or value['identity_bound'] != (value['child_pid'] > 0)
            or value['pgid_known'] != (value['observed_pgid'] > 0)
            or value['pgid_failure_code'] > 6 or value['pgid_errno'] > 4095
            or (value['pgid_known'] and (not value['identity_bound'] or value['pgid_failure_code']))
            or (value['pgid_errno'] and not value['pgid_failure_code'])
            or (value['cleanup_returned'] and not value['cleanup_entered'])):
        return False
    rows = value['operation_rows']
    if type(rows) is not list or len(rows) > TEARDOWN_MAX_ROWS:
        return False
    for row in rows:
        if (type(row) is not list or len(row) != 9 or type(row[0]) is not int
                or row[0] not in TEARDOWN_OPS
                or (role == 'worker-parent' and row[0] > 10)
                or (role == 'encoder-parent' and row[0] < 11)
                or any(type(row[index]) is not bool for index in (3, 5))
                or any(type(row[index]) is not int or not 0 <= row[index] < (1 << 63)
                       for index in (1, 2))
                or type(row[4]) is not int or row[4] not in (0, 1)
                or not _teardown_integer(row[6], signed=True)
                or type(row[7]) is not int or not 0 <= row[7] <= 6
                or type(row[8]) is not int or not 0 <= row[8] <= 4095):
            return False
        if ((row[3] != bool(0 < row[1] <= row[2]))
                or (row[5] and row[0] not in TEARDOWN_EXIT_RESULT_OPS)
                or (row[4] == 0 and (row[7] or row[8]))
                or (row[4] == 1 and (row[7] == 0 or row[5]))
                or (not row[5] and row[6] != 0)):
            return False
    return not rows or value['cleanup_entered']


def read_teardown_receipt(capture_path, role):
    """Bounded offline read; an absent/partial sibling is never substituted."""
    result = {'role': role, 'status': 'unknown', 'native_producer_final': False}
    if role not in TEARDOWN_ROLES:
        return result
    directory = fd = None
    try:
        path = pathlib.Path(str(capture_path)+'.'+role+'.json')
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if (info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700):
            return result
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.getuid() or before.st_nlink != 1
                or before.st_size > TEARDOWN_MAX_BYTES):
            return result
        data = os.read(fd, TEARDOWN_MAX_BYTES+1)
        after = os.fstat(fd)
        if (len(data) > TEARDOWN_MAX_BYTES or not data.endswith(b'\n')
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                   (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != after.st_size):
            return result
        def closed_object(pairs):
            output = {}
            for key, item in pairs:
                if key in output:
                    raise ValueError('duplicate receipt key')
                output[key] = item
            return output
        def reject_constant(_):
            raise ValueError('nonfinite receipt value')
        value = json.loads(data, object_pairs_hook=closed_object, parse_constant=reject_constant)
        if validate_teardown_receipt(value, role):
            result.update(status='observed_parent_receipt', receipt=value)
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                result = {'role': role, 'status': 'unknown', 'native_producer_final': False}
        if directory is not None:
            try:
                os.close(directory)
            except OSError:
                result = {'role': role, 'status': 'unknown', 'native_producer_final': False}
    return result


class ParentTeardownReceipt:
    """Opt-in parent observations; async publication adds no teardown wait.

    Only existing poll/wait return values are observed. PGID is sampled once
    after this parent's actual Popen creation and is not an ownership credential.
    Signal method return is not proof of an internal syscall or target delivery.
    """
    def __init__(self, capture_path, role):
        if role not in TEARDOWN_ROLES:
            raise ValueError('unknown teardown role')
        self.path = pathlib.Path(str(capture_path)+'.'+role+'.json')
        self.finished = False
        self.value = dict(schema=TEARDOWN_SCHEMA, role=role,
            clock_domain='host_clock_gettime_CLOCK_MONOTONIC_ns', parent_pid=os.getpid(),
            child_pid=0, identity_bound=False, pgid_known=False, observed_pgid=0,
            pgid_failure_code=0, pgid_errno=0, cleanup_entered=False,
            cleanup_returned=False, observer_errors=0, operations_dropped=0, operation_rows=[])

    def bind(self, proc):
        if self.finished or self.value['identity_bound']:
            return
        try:
            pid = proc.pid
            if not _teardown_integer(pid) or pid == 0:
                raise ValueError('bounded Popen pid required')
            self.value.update(child_pid=pid, identity_bound=True)
            try:
                pgid = os.getpgid(pid)
                if not _teardown_integer(pgid) or pgid == 0:
                    raise ValueError('bounded observed pgid required')
                self.value.update(pgid_known=True, observed_pgid=pgid)
            except Exception as failure:
                code, errno = teardown_failure_code(failure)
                self.value.update(pgid_failure_code=code, pgid_errno=errno)
        except Exception:
            self.value['observer_errors'] += 1

    def stamp(self):
        try:
            value = capture_trace_clock_ns()
            if type(value) is not int or not 0 < value < (1 << 63):
                raise ValueError('bounded diagnostic clock required')
            return value
        except Exception:
            self.value['observer_errors'] += 1
            return 0

    def call(self, operation, action):
        if self.finished:
            return action()
        begin = self.stamp()
        outcome = known = code = errno = returned = 0
        try:
            answer = action()
            if operation in TEARDOWN_EXIT_RESULT_OPS and _teardown_integer(answer, signed=True):
                known, returned = True, answer
            return answer
        except BaseException as failure:
            outcome = 1
            code, errno = teardown_failure_code(failure)
            raise
        finally:
            try:
                end = self.stamp()
                row = [operation, begin, end, bool(begin and end and end >= begin),
                       outcome, bool(known), returned, code, errno]
                if len(self.value['operation_rows']) < TEARDOWN_MAX_ROWS:
                    self.value['operation_rows'].append(row)
                else:
                    self.value['operations_dropped'] += 1
            except Exception:
                # Observation never replaces the original cleanup exception.
                pass

    def finish(self, returned):
        try:
            if self.finished:
                return
            self.finished = True
            self.value['cleanup_returned'] = returned
            if not validate_teardown_receipt(self.value, self.value['role']):
                return
            encoded = json.dumps(self.value, separators=(',', ':')).encode('utf-8')+b'\n'
            if len(encoded) > TEARDOWN_MAX_BYTES:
                return
            # Snapshot bytes before dispatch. No join, new deadline or retry.
            threading.Thread(target=self._publish, args=(self.path, encoded),
                name='owned-teardown-receipt', daemon=True).start()
        except Exception:
            pass

    @staticmethod
    def _publish(path, encoded):
        directory = fd = None
        staged = path.name+'.tmp'
        created = False
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(directory)
            if (info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o700):
                return
            fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            created = True
            os.fchmod(fd, 0o600)
            view = memoryview(encoded)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('receipt write incomplete')
                view = view[count:]
            closing, fd = fd, None
            os.close(closing)
            # Publish only after successful close. link cannot replace a receipt.
            os.link(staged, path.name, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
        except Exception:
            pass
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if created and directory is not None:
                try:
                    os.unlink(staged, dir_fd=directory)
                except Exception:
                    pass
            if directory is not None:
                try:
                    os.close(directory)
                except Exception:
                    pass


def parent_teardown_receipt(capture_path, role):
    if capture_path is None:
        return None
    try:
        return ParentTeardownReceipt(capture_path, role)
    except Exception:
        return None


def observed_teardown(diagnostic, operation, action):
    if diagnostic is None:
        return action()
    return diagnostic.call(operation, action)


def capture_trace_clock_ns():
    """Same explicitly named host clock as the opt-in native trace."""
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def raw_writer_trace_arguments(enabled, capture_trace, native_encoder):
    if type(enabled) is not bool:
        raise ValueError('Raw writer diagnostics selection must be boolean')
    if not enabled:
        return []
    if capture_trace is None or native_encoder is None:
        raise ValueError('Raw writer diagnostics require capture trace and explicit encoder')
    return ['--raw-writer-diagnostics']


class BoundedCaptureTrace:
    """Opt-in metadata only, asynchronous and bounded; never backpressure media."""
    def __init__(self, path, max_records=24000, max_bytes=16 * 1024 * 1024, pending_limit=256):
        if (not 1 <= max_records <= 24000 or not 4096 <= max_bytes <= 16 * 1024 * 1024
                or not 1 <= pending_limit <= 1024):
            raise ValueError('invalid capture trace bounds')
        self.path = pathlib.Path(path)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(self.fd, 0o600)
        self.max_records, self.max_bytes = max_records, max_bytes
        self.pending = queue.Queue(maxsize=pending_limit)
        self.lock = threading.Lock()
        self.accepted = self.written = self.bytes = self.dropped = 0
        self.clock_errors = 0
        self.closed = self.failed = self.byte_capped = False
        self.writer = threading.Thread(target=self._write, name='capture-trace', daemon=True)
        try:
            self.writer.start()
        except Exception:
            os.close(self.fd)
            raise
        self._emit_clock('start')

    @staticmethod
    def clock_sample():
        before = capture_trace_clock_ns()
        unix_ns = time.time_ns()
        after = capture_trace_clock_ns()
        return {'clock_domain': 'host_clock_gettime_CLOCK_MONOTONIC_ns',
                'clock_before_ns': before, 'clock_after_ns': after, 'unix_ns': unix_ns,
                'source_pts_scope': 'emulator_estimated_screenshot_generation_unix_us_not_guest_media_pts'}

    def _emit_clock(self, phase):
        try:
            self.emit('trace_clock', **self.clock_sample(), phase=phase)
        except Exception:
            # A diagnostic clock fault is lost coverage, never a media failure.
            self.clock_errors += 1

    def emit(self, event, **fields):
        if (not isinstance(event, str) or len(event) > 64 or len(fields) > 32 or
                any(not isinstance(key, str) or len(key) > 64 or
                    type(value) not in (int, float, str, bool) or
                    isinstance(value, str) and len(value) > 192
                    for key, value in fields.items())):
            with self.lock:
                self.dropped += 1
            return
        record = {'schema': 'capture-vt-trace-v1', 'process': 'python', 'event': event, **fields}
        with self.lock:
            if self.closed:
                return
            if self.failed or self.accepted >= self.max_records:
                self.dropped += 1
                return
            try:
                self.pending.put_nowait(record)
                self.accepted += 1
            except queue.Full:
                self.dropped += 1

    def _line(self, record):
        encoded = json.dumps(record, separators=(',', ':')).encode() + b'\n'
        # Reserve room for a final summary even if the byte ceiling is reached.
        if self.bytes + len(encoded) > self.max_bytes - 2048:
            self.byte_capped = True
            return
        view = memoryview(encoded)
        while view:
            written = os.write(self.fd, view)
            if written <= 0:
                raise OSError('capture trace write failed')
            view = view[written:]
        self.bytes += len(encoded)
        self.written += 1

    def _write(self):
        try:
            while True:
                record = self.pending.get()
                if record is None:
                    break
                self._line(record)
            summary = {'schema': 'capture-vt-trace-v1', 'process': 'python', 'event': 'trace_summary',
                       'accepted_records': self.accepted, 'written_records': self.written,
                       'dropped_records': self.dropped, 'byte_capped': self.byte_capped,
                       'clock_errors': self.clock_errors,
                       'record_limit': self.max_records, 'byte_limit': self.max_bytes,
                       'clean_close': True}
            encoded = json.dumps(summary, separators=(',', ':')).encode() + b'\n'
            view = memoryview(encoded)
            while view:
                written = os.write(self.fd, view)
                if written <= 0:
                    raise OSError('capture trace summary write failed')
                view = view[written:]
        except OSError:
            self.failed = True
        finally:
            os.close(self.fd)

    def close(self):
        self._emit_clock('end')
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if not self.writer.is_alive():
            return
        try:
            self.pending.put(None, timeout=2)
        except queue.Full:
            return
        self.writer.join(timeout=2)


def experimental_burst_arguments(native_encoder, mode, burst_bytes, burst_seconds):
    """A second VT rate window is opt-in and never sent to the deployed encoder."""
    if burst_bytes is None and burst_seconds is None:
        return []
    if (native_encoder is None or mode not in ('VBR', 'ADAPTIVE_VBR') or
            type(burst_bytes) is not int or not 32768 <= burst_bytes <= 2000000 or
            type(burst_seconds) not in (int, float) or not math.isfinite(burst_seconds) or
            not .02 <= burst_seconds <= 1):
        raise ValueError('Burst window requires an experimental VBR encoder, 32768..2000000 bytes, and .02..1 seconds')
    return ['--burst-bytes', str(burst_bytes), '--burst-seconds', str(burst_seconds)]


def experimental_prioritize_speed_arguments(native_encoder, prioritize_speed):
    """Leave the encoder default intact unless an independent trial opts in."""
    if prioritize_speed is None:
        return []
    if native_encoder is None or type(prioritize_speed) is not bool:
        raise ValueError('Explicit speed priority requires an independent experimental encoder and a boolean')
    return ['--prioritize-speed', 'true' if prioritize_speed else 'false']


RAW_SUBMIT_BUDGET_SCOPE = 'raw_frame_submit_token_budget_only_not_grpc_sampling_or_display_fps'


def experimental_raw_submit_arguments(native_encoder, raw_submit_fps, matched_experimental_client=False):
    """Override only the raw submit budget after the caller verifies its client."""
    if type(matched_experimental_client) is not bool:
        raise ValueError('Matched experimental client selection must be boolean')
    if raw_submit_fps is None:
        return []
    if (native_encoder is None or matched_experimental_client is not True
            or type(raw_submit_fps) is not int or raw_submit_fps not in (30, 60, 120)):
        raise ValueError('Raw submit FPS requires an independent experimental encoder, '
                         'a matched experimental client, and an integer 30, 60 or 120')
    return ['--raw-submit-fps', str(raw_submit_fps), '--matched-experimental-client']


def raw_submit_budget_configuration(fps, raw_submit_fps=None):
    """Configuration evidence; actual submission cadence still requires a trace."""
    for value in (fps, raw_submit_fps):
        if value is not None and (type(value) is not int or value not in (30, 60, 120)):
            raise ValueError('Raw/native FPS must be an integer 30, 60 or 120')
    if fps is None:
        raise ValueError('Native FPS is required')
    return {'requested_raw_submit_fps': raw_submit_fps,
            'effective_raw_submit_fps': fps if raw_submit_fps is None else raw_submit_fps,
            'native_encoder_fps_arg': fps, 'grpc_fps_limit_applied': False,
            'scope': RAW_SUBMIT_BUDGET_SCOPE}


def verify_raw_submit_budget_readback(value, fps, raw_submit_fps):
    expected = raw_submit_budget_configuration(fps, raw_submit_fps)
    if (not isinstance(value, dict) or any(type(value.get(key)) is not type(item)
            or value.get(key) != item for key, item in expected.items())):
        raise ValueError('Raw submit budget configuration readback missing or mismatched')
    return dict(expected)


def read_exact(source, count):
    data = bytearray()
    read = source.recv if hasattr(source, 'recv') else source.read
    while len(data) < count:
        part = read(count - len(data))
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
    """Permit delivery jitter while bounding the sustained rate."""
    def __init__(self, fps, clock=time.monotonic, burst=2.):
        self.fps, self.clock, self.burst = fps, clock, burst
        self.tokens, self.updated = float(burst), clock()

    def delay(self):
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + max(0., now - self.updated) * self.fps)
        self.updated = now
        return max(0., (1. - self.tokens) / self.fps)

    def consume(self):
        self.delay()
        self.tokens = max(0., self.tokens - 1.)


def raw_frame_distribution(values):
    """Bounded per-window timings, not estimates of network/phone latency."""
    ordered = sorted(values)
    if not ordered:
        return {'count': 0}
    return {'count': len(ordered), 'p50_ms': round(ordered[(len(ordered)-1)//2], 3),
            'p95_ms': round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*.95)-1)], 3),
            'max_ms': round(ordered[-1], 3)}


def take_raw_frame(frames, policy):
    """Only pre-encode complete raw images may be superseded here."""
    if policy not in ('fifo', 'latest'):
        raise ValueError('Unknown raw frame queue policy')
    if not frames:
        return None, 0
    if policy == 'latest':
        discarded = len(frames) - 1
        frame = frames.pop()
        frames.clear()
        return frame, discarded
    return frames.popleft(), 0


class HostHardwareSession:
    """Own a worker and three private socket pairs, matching the existing App."""
    def __init__(self, base, serial, avd, max_size, bit_rate, max_fps, mode, raw_queue_policy=None,
                 native_encoder=None, low_latency_mode=None, sps_low_delay=None,
                 burst_bytes=None, burst_seconds=None, worker_log=None, capture_trace=None,
                 prioritize_speed=None, raw_submit_fps=None, matched_experimental_client=False,
                 raw_writer_diagnostics=False):
        diagnostic_arguments = raw_writer_trace_arguments(
            raw_writer_diagnostics, capture_trace, native_encoder)
        speed_arguments = experimental_prioritize_speed_arguments(native_encoder, prioritize_speed)
        raw_submit_arguments = experimental_raw_submit_arguments(
            native_encoder, raw_submit_fps, matched_experimental_client)
        if raw_submit_fps is not None:
            raw_submit_budget_configuration(max_fps, raw_submit_fps)
        self.raw_submit_budget_readback = None
        self.base = pathlib.Path(base)
        directory = self.base / 'hardware'
        self.clients = {}
        self.proc = None
        self.started = False
        self.teardown_diagnostic = (parent_teardown_receipt(capture_trace, 'worker-parent')
                                    if capture_trace is not None else None)
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
                         '--mode', mode, '--raw-queue-policy',
                         raw_queue_policy or os.environ.get('DIRECT_RAW_QUEUE_POLICY', 'fifo')]
            if native_encoder is not None:
                candidate = pathlib.Path(native_encoder).resolve()
                if not candidate.is_file() or not os.access(candidate, os.X_OK):
                    raise ValueError('Experimental native encoder must be an executable file')
                arguments += ['--native-encoder', str(candidate)]
            arguments += experimental_burst_arguments(native_encoder, mode, burst_bytes, burst_seconds)
            if speed_arguments:
                arguments += ['--encoder-prioritize-speed', speed_arguments[1]]
            arguments += raw_submit_arguments
            if capture_trace is not None:
                if native_encoder is None:
                    raise ValueError('Capture trace requires an explicit experimental encoder')
                arguments += ['--capture-trace', str(pathlib.Path(capture_trace).resolve())]
            arguments += diagnostic_arguments
            if low_latency_mode is not None:
                if native_encoder is None or not isinstance(low_latency_mode, bool):
                    raise ValueError('Explicit low latency flag requires an experimental encoder')
                arguments += ['--low-latency-mode', 'true' if low_latency_mode else 'false']
            if sps_low_delay is None:
                sps_low_delay = os.environ.get('DIRECT_SPS_LOW_DELAY', 'false') == 'true'
            if not isinstance(sps_low_delay, bool):
                raise ValueError('SPS low delay flag must be boolean')
            arguments += ['--sps-low-delay', 'true' if sps_low_delay else 'false']
            for role, pair in zip(('video', 'audio', 'control'), pairs):
                arguments += ['--' + role + '-fd', str(pair[1].fileno())]
            log = pathlib.Path(worker_log or directory / 'worker.log').open('ab', buffering=0)
            try:
                self.proc = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=log, pass_fds=tuple(p[1].fileno() for p in pairs),
                                             start_new_session=True)
                if self.teardown_diagnostic is not None:
                    self.teardown_diagnostic.bind(self.proc)
            finally:
                log.close()
            if not select.select([self.proc.stdout], [], [], 15)[0]:
                raise TimeoutError('hardware worker initialization')
            reply = json.loads(self.proc.stdout.readline(2048))
            if not reply.get('initialized'):
                raise RuntimeError('hardware worker failed initialization')
            if raw_submit_fps is not None:
                self.raw_submit_budget_readback = verify_raw_submit_budget_readback(
                    reply.get('raw_submit_budget'), max_fps, raw_submit_fps)
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
        diagnostic = getattr(self, 'teardown_diagnostic', None)
        if diagnostic is None:
            return self._close_without_diagnostics()
        diagnostic.value['cleanup_entered'] = True
        returned = False
        try:
            for channel in self.clients.values():
                try:
                    observed_teardown(diagnostic, 1, lambda: channel.shutdown(socket.SHUT_RDWR))
                except OSError:
                    pass
                observed_teardown(diagnostic, 2, channel.close)
            if self.proc is not None and observed_teardown(diagnostic, 3, self.proc.poll) is None:
                try:
                    observed_teardown(diagnostic, 4, lambda: os.killpg(self.proc.pid, signal.SIGTERM))
                    observed_teardown(diagnostic, 5, lambda: self.proc.wait(timeout=5))
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if observed_teardown(diagnostic, 6, self.proc.poll) is None:
                        observed_teardown(diagnostic, 7, lambda: os.killpg(self.proc.pid, signal.SIGKILL))
                        observed_teardown(diagnostic, 8, lambda: self.proc.wait(timeout=2))
            if self.proc is not None:
                for index, stream in enumerate((self.proc.stdin, self.proc.stdout)):
                    if stream and not stream.closed:
                        observed_teardown(diagnostic, 9+index, stream.close)
            returned = True
        finally:
            diagnostic.finish(returned)

    def _close_without_diagnostics(self):
        # The trace-off path retains the pre-diagnostic cleanup verbatim.
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
    speed_option = getattr(args, 'encoder_prioritize_speed', None)
    if speed_option not in (None, 'true', 'false'):
        raise ValueError('encoder-prioritize-speed expects true or false')
    speed_arguments = experimental_prioritize_speed_arguments(
        args.native_encoder, None if speed_option is None else speed_option == 'true')
    raw_submit_fps = getattr(args, 'raw_submit_fps', None)
    experimental_raw_submit_arguments(args.native_encoder, raw_submit_fps,
                                     getattr(args, 'matched_experimental_client', False))
    raw_submit_budget = raw_submit_budget_configuration(args.fps, raw_submit_fps)
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
    trace_path = getattr(args, 'capture_trace', None)
    raw_diagnostics_enabled = getattr(args, 'raw_writer_diagnostics', False)
    raw_writer_trace_arguments(raw_diagnostics_enabled, trace_path, args.native_encoder)
    if trace_path is not None and args.native_encoder is None:
        raise ValueError('Capture trace requires an explicit experimental encoder')
    trace = BoundedCaptureTrace(trace_path) if trace_path is not None else None
    encoder_teardown = (parent_teardown_receipt(trace_path, 'encoder-parent')
                         if trace_path is not None else None)
    raw_diagnostics = HostTimingTrace(trace) if raw_diagnostics_enabled else None
    # Raw RGBA frames may be superseded safely before H.264 encoding. Preserve
    # a small delivery burst without queuing four stale frames behind a gesture.
    latest_frames = collections.deque(maxlen=2)
    controls = collections.deque(maxlen=8)
    physical_size = (540, 1200)
    reply_lock = threading.Lock()
    counters = {'raw_frames': 0, 'pending_frames_replaced': 0, 'frames_submitted': 0, 'idle_repeats': 0,
                'audio_packets': 0, 'audio_startup_discarded': 0}
    phase_lock = threading.Lock()
    phase_samples = {key: collections.deque(maxlen=1024) for key in
                     ('capture_age', 'capture_gap', 'source_pts_gap', 'raw_queue', 'raw_pipe', 'encoded_egress')}

    def timing(name, milliseconds):
        with phase_lock:
            phase_samples[name].append(milliseconds)

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
        # Local scrcpy preview may coexist: each session owns a unique SCID,
        # socket, forwarded port and exact-PID cleanup. Gateway serializes its sessions.
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
        initialized = {'initialized': True, 'clock_uncertainty_us': clock['uncertainty_us']}
        if raw_submit_fps is not None:
            initialized['raw_submit_budget'] = raw_submit_budget
        print(json.dumps(initialized), flush=True)
        # Wait for all App channels before emitting timestamps / media.
        while not stop.is_set():
            if select.select([sys.stdin.buffer], [], [], .5)[0]:
                if sys.stdin.buffer.read(1) != b'1':
                    raise EOFError('gateway closed before media startup')
                break
        if stop.is_set():
            return
        native_arguments = [str(args.native_encoder or args.directory / 'macos-h264'), '--service', 'true',
                            '--fps', str(args.fps), '--bitrate', str(args.bitrate),
                            '--mode', 'CBR' if args.mode == 'CBR' else 'VBR', '--idle-seconds', '30']
        native_arguments += experimental_burst_arguments(args.native_encoder, args.mode,
                                                        args.burst_bytes, args.burst_seconds)
        native_arguments += speed_arguments
        if trace_path is not None:
            native_arguments += ['--trace', str(trace_path) + '.native.jsonl']
        if args.low_latency_mode is not None:
            if args.native_encoder is None:
                raise ValueError('Low latency experiment requires an explicit experimental encoder')
            native_arguments += ['--low-latency-mode', args.low_latency_mode]
        native = subprocess.Popen(native_arguments,
                                  stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE if args.sps_low_delay == 'true' else sockets['video'].fileno(),
                                  stderr=subprocess.PIPE)
        if encoder_teardown is not None:
            encoder_teardown.bind(native)

        def video_output():
            # Only the small config packet is parsed/changed. Media access units,
            # timestamps and keyframe flags pass through byte-for-byte.
            from h264_low_latency import patch_apple_baseline_config
            source = native.stdout
            codec = read_exact(source, 4)
            if codec != b'h264':
                raise ValueError('Expected hardware H264 framing')
            sockets['video'].sendall(codec)
            while not stop.is_set():
                high = read_exact(source, 4)
                if struct.unpack('>I', high)[0] & 0x80000000:
                    sockets['video'].sendall(high + read_exact(source, 8))
                    continue
                remaining = read_exact(source, 8)
                flagged, size = struct.unpack('>QI', high + remaining)
                if not 0 < size <= 8 * 1024 * 1024:
                    raise ValueError('Invalid hardware H264 access unit size')
                payload = read_exact(source, size)
                output_received_ns = capture_trace_clock_ns() if trace is not None else 0
                if flagged & CONFIG_FLAG:
                    payload, metadata = patch_apple_baseline_config(payload)
                    log({'event': 'sps_low_delay', **metadata})
                send_start = time.monotonic_ns()
                sockets['video'].sendall(struct.pack('>QI', flagged, len(payload)) + payload)
                timing('encoded_egress', (time.monotonic_ns()-send_start) / 1e6)
                if trace is not None and not flagged & CONFIG_FLAG:
                    trace.emit('encoded_egress', source_pts_us=flagged & PTS_MASK,
                               encoded_read_complete_ns=output_received_ns,
                               socket_write_end_ns=capture_trace_clock_ns(), au_bytes=len(payload))

        def collect():
            nonlocal physical_size
            previous_received_ns = previous_source_pts = None
            capture_seq = 0
            call = stub.streamScreenshot(request, metadata=metadata)
            try:
                for image in call:
                    grpc_return_ns = capture_trace_clock_ns() if trace is not None else 0
                    if stop.is_set():
                        break
                    width = int(image.format.width or image.width)
                    height = int(image.format.height or image.height)
                    if not width or not height:
                        continue
                    pixels = image.image
                    if width % 2 or height % 2 or len(pixels) != width * height * 4:
                        raise ValueError('invalid complete hardware frame')
                    received_ns = time.monotonic_ns()
                    pts = int(image.timestampUs)
                    timing('capture_age', (time.time_ns() // 1000 - pts) / 1000)
                    if previous_received_ns is not None:
                        timing('capture_gap', (received_ns-previous_received_ns) / 1e6)
                        timing('source_pts_gap', (pts-previous_source_pts) / 1000)
                    previous_received_ns, previous_source_pts = received_ns, pts
                    capture_seq += 1
                    with condition:
                        replaced_seq = latest_frames[0][5] if len(latest_frames) == latest_frames.maxlen else 0
                        if len(latest_frames) == latest_frames.maxlen:
                            counters['pending_frames_replaced'] += 1
                        physical_size = (base_height, base_width) if width > height else (base_width, base_height)
                        enqueue_ns = capture_trace_clock_ns() if trace is not None else 0
                        latest_frames.append((width, height, pts, pixels, received_ns, capture_seq))
                        counters['raw_frames'] += 1
                        condition.notify_all()
                        pending_count = len(latest_frames)
                    if trace is not None:
                        trace.emit('capture_enqueue', capture_seq=capture_seq, source_pts_us=pts,
                                   screenshot_seq=int(image.seq),
                                   grpc_return_ns=grpc_return_ns, enqueue_ns=enqueue_ns,
                                   width=width, height=height, raw_bytes=len(pixels),
                                   pending_count=pending_count, replaced_capture_seq=replaced_seq)
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
            budget = FrameRateBudget(raw_submit_budget['effective_raw_submit_fps'])
            while not stop.is_set():
                observed = raw_diagnostics.begin_raw() if raw_diagnostics is not None else None
                if observed is not None:
                    loop_trace, budget_trace, write_trace = observed
                try:
                    with condition:
                        if observed is not None:
                            loop_trace['condition_begin_ns'] = raw_diagnostics.stamp()
                            loop_trace['queued_before'] = len(latest_frames)
                        if not latest_frames and not (controls and native_ready.is_set()):
                            if observed is not None:
                                loop_trace['condition_waited'] = True
                                loop_trace['condition_wait_begin_ns'] = raw_diagnostics.stamp()
                            condition.wait(timeout=.25)
                            if observed is not None:
                                loop_trace['condition_wait_end_ns'] = raw_diagnostics.stamp()
                        # Initial adaptive requests can precede the first frame.
                        # Retain them until VT has created its required HW session.
                        commands = list(controls) if native_ready.is_set() else []
                        if commands:
                            controls.clear()
                        if observed is not None:
                            loop_trace['condition_end_ns'] = raw_diagnostics.stamp()
                            loop_trace['queued_after'] = len(latest_frames)
                            loop_trace['native_ready'] = native_ready.is_set()
                    if observed is not None:
                        loop_trace['control_count'] = len(commands)
                        loop_trace['control_begin_ns'] = raw_diagnostics.stamp()
                    try:
                        for sequence, kind, value in commands:
                            native.stdin.write(struct.pack('>IIQIII', 0, 0, sequence, 8, kind, value))
                    finally:
                        if observed is not None:
                            loop_trace['control_end_ns'] = raw_diagnostics.stamp()
                    # Apply the budget to every submitted frame, including a backlog.
                    # Waiting first lets the collector replace stale raw frames.
                    if observed is not None:
                        budget_trace['check_begin_ns'] = raw_diagnostics.stamp()
                        budget_trace['tokens_before_milli'] = round(budget.tokens * 1000)
                    delay = budget.delay()
                    if observed is not None:
                        budget_trace['check_end_ns'] = raw_diagnostics.stamp()
                        budget_trace['tokens_after_delay_milli'] = round(budget.tokens * 1000)
                        budget_trace['updated_python_monotonic_ns'] = round(budget.updated * 1e9)
                        budget_trace['requested_wait_ns'] = round(delay * 1e9)
                    if delay > 0:
                        if observed is not None:
                            budget_trace['wait_begin_ns'] = raw_diagnostics.stamp()
                        stop.wait(delay)
                        if observed is not None:
                            budget_trace['wait_end_ns'] = raw_diagnostics.stamp()
                    if stop.is_set():
                        if observed is not None:
                            loop_trace['outcome'] = 4
                        break
                    with condition:
                        frame, skipped = take_raw_frame(latest_frames, args.raw_queue_policy)
                        dequeue_ns = capture_trace_clock_ns() if trace is not None else 0
                        counters['pending_frames_replaced'] += skipped
                    if observed is not None:
                        loop_trace['dequeue_ns'] = dequeue_ns
                        loop_trace['skipped'] = skipped
                    if frame is None:
                        if last is None or time.monotonic() - last_submit < 1:
                            if observed is not None:
                                write_trace['idle_flush_begin_ns'] = raw_diagnostics.stamp()
                            try:
                                native.stdin.flush()
                            finally:
                                if observed is not None:
                                    write_trace['idle_flush_end_ns'] = raw_diagnostics.stamp()
                            if observed is not None:
                                loop_trace['outcome'] = 2
                            continue
                        frame = (last[0], last[1], time.time_ns() // 1000, last[3], time.monotonic_ns(), 0)
                        counters['idle_repeats'] += 1
                    width, height, pts, pixels, received_ns, capture_seq = frame
                    if observed is not None:
                        for row in (loop_trace, write_trace):
                            row['capture_seq'], row['source_pts_us'] = capture_seq, pts
                        write_trace['raw_bytes'] = len(pixels)
                        write_trace['idle_repeat'] = capture_seq == 0
                    if pts <= previous_pts:
                        if trace is not None:
                            trace.emit('raw_drop', capture_seq=capture_seq, source_pts_us=pts,
                                       dequeue_ns=dequeue_ns, reason='nonincreasing_source_pts')
                        if observed is not None:
                            loop_trace['outcome'] = 3
                        continue
                    if observed is not None:
                        budget_trace['consume_begin_ns'] = raw_diagnostics.stamp()
                    budget.consume()
                    if observed is not None:
                        budget_trace['consume_end_ns'] = raw_diagnostics.stamp()
                        budget_trace['consumed'] = True
                        budget_trace['tokens_after_consume_milli'] = round(budget.tokens * 1000)
                    pipe_begin_ns = time.monotonic_ns()
                    trace_pipe_begin_ns = capture_trace_clock_ns() if trace is not None else 0
                    timing('raw_queue', (pipe_begin_ns-received_ns) / 1e6)
                    if observed is not None:
                        write_trace['write_begin_ns'] = raw_diagnostics.stamp()
                    try:
                        native.stdin.write(struct.pack('>IIQI', width, height, pts, len(pixels)))
                        native.stdin.write(pixels)
                        native.stdin.flush()
                        if observed is not None:
                            write_trace['flush_complete'] = True
                    finally:
                        if observed is not None:
                            write_trace['write_end_ns'] = raw_diagnostics.stamp()
                    if trace is not None:
                        trace.emit('raw_submit', capture_seq=capture_seq, source_pts_us=pts,
                                   dequeue_ns=dequeue_ns, pipe_write_begin_ns=trace_pipe_begin_ns,
                                   pipe_write_end_ns=capture_trace_clock_ns(), raw_bytes=len(pixels),
                                   skipped_frames=skipped, idle_repeat=capture_seq == 0)
                    timing('raw_pipe', (time.monotonic_ns()-pipe_begin_ns) / 1e6)
                    previous_pts, last, last_submit = pts, frame, time.monotonic()
                    counters['frames_submitted'] += 1
                    if observed is not None:
                        loop_trace['outcome'] = 1
                except Exception:
                    if observed is not None:
                        loop_trace['outcome'] = 6
                    raise
                finally:
                    if observed is not None:
                        raw_diagnostics.end_raw(observed, counters)

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
                with phase_lock:
                    timings = {key: raw_frame_distribution(values) for key, values in phase_samples.items()}
                    for values in phase_samples.values():
                        values.clear()
                log({'event': 'phase_sample', 'unix_ms': time.time_ns() // 1_000_000,
                     'raw_queue_policy': args.raw_queue_policy, 'timings': timings,
                     'scope': 'Host capture/encoder pipe and encoded socket write; excludes independent phone presentation and WAN transit'})
                previous_time, previous = now, current

        for function in (collect, native_events, video_input, audio_output, input_events, guest_replies,
                         sample_pipeline):
            background(function)
        if args.sps_low_delay == 'true':
            background(video_output)
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
            if encoder_teardown is None or child is not native:
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
            else:
                encoder_teardown.value['cleanup_entered'] = True
                teardown_returned = False
                try:
                    if child is not None and observed_teardown(encoder_teardown, 11, child.poll) is None:
                        observed_teardown(encoder_teardown, 12, child.terminate)
                        try:
                            observed_teardown(encoder_teardown, 13, lambda: child.wait(timeout=3))
                        except subprocess.TimeoutExpired:
                            observed_teardown(encoder_teardown, 14, child.kill)
                            observed_teardown(encoder_teardown, 15, lambda: child.wait(timeout=2))
                    teardown_returned = True
                finally:
                    encoder_teardown.finish(teardown_returned)
        if port:
            try:
                adb('forward', '--remove', 'tcp:' + str(port))
            except Exception:
                pass
        log({'event': 'session_closed', **counters})
        if trace is not None:
            if raw_diagnostics is not None:
                # Existing teardown does not join all worker threads. Report
                # producer coverage honestly without adding a new media wait.
                raw_diagnostics.summary(not any(thread.is_alive() for thread in threads))
            try:
                trace.close()
            except Exception:
                if raw_diagnostics is not None:
                    raw_diagnostics.emit_errors += 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--avd', required=True)
    parser.add_argument('--directory', required=True, type=pathlib.Path)
    parser.add_argument('--max-size', required=True, type=int, choices=RESOLUTION_MAX_SIZES)
    parser.add_argument('--bitrate', required=True, type=int)
    parser.add_argument('--fps', required=True, type=int, choices=(30, 60, 120))
    parser.add_argument('--raw-submit-fps', type=int, choices=(30, 60, 120), default=None,
                        help='Experimental raw submission budget only; never limits gRPC sampling or native FPS')
    parser.add_argument('--matched-experimental-client', action='store_true',
                        help='Internal caller attestation after verifying the fixed experiment client/probe pair')
    parser.add_argument('--mode', required=True, choices=('CBR', 'VBR', 'ADAPTIVE_VBR'))
    parser.add_argument('--raw-queue-policy', choices=('fifo', 'latest'), default='fifo')
    parser.add_argument('--native-encoder', type=pathlib.Path)
    parser.add_argument('--encoder-prioritize-speed', choices=('true', 'false'), default=None,
                        help='Explicit speed/quality hint for an independent experimental encoder')
    parser.add_argument('--capture-trace', type=pathlib.Path,
                        help='Opt-in bounded private JSONL, plus PATH.native.jsonl; requires experimental encoder')
    parser.add_argument('--raw-writer-diagnostics', action='store_true',
                        help='Local opt-in fixed numeric timing; requires capture trace and explicit encoder')
    parser.add_argument('--burst-bytes', type=int)
    parser.add_argument('--burst-seconds', type=float)
    parser.add_argument('--low-latency-mode', choices=('true', 'false'))
    parser.add_argument('--sps-low-delay', choices=('true', 'false'), default='false')
    for role in ('video', 'audio', 'control'):
        parser.add_argument('--' + role + '-fd', required=True, type=int)
    options = parser.parse_args()
    try:
        experimental_raw_submit_arguments(options.native_encoder, options.raw_submit_fps,
                                         options.matched_experimental_client)
        raw_writer_trace_arguments(options.raw_writer_diagnostics, options.capture_trace, options.native_encoder)
    except ValueError as error:
        parser.error(str(error))
    if options.encoder_prioritize_speed is not None and options.native_encoder is None:
        parser.error('--encoder-prioritize-speed requires an independent experimental native encoder')
    if not re.fullmatch(r'emulator-\d+', options.serial) or not re.fullmatch(r'[a-zA-Z0-9_-]+', options.avd):
        parser.error('requires a local emulator and plain AVD name')
    if not 500000 <= options.bitrate <= 40000000:
        parser.error('invalid bitrate')
    try:
        worker(options)
    except Exception as error:
        print(json.dumps({'event': 'worker_error', 'error_type': type(error).__name__}), file=sys.stderr, flush=True)
        raise SystemExit(1)
