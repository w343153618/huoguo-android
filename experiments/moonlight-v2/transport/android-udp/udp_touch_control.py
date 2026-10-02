"""Authenticated-UDP touch snapshots -> existing local scrcpy input service.

Call only after the enclosing session AES-GCM/replay/peer checks. This module
does not open any network listener, spawn shell input, or control the Mac mouse.
Each snapshot has unique per-contact tracking tokens. Missing DOWN/UP datagrams
are repaired by later complete snapshots; a bounded lease releases stuck fingers.
"""
from dataclasses import dataclass
import queue
import struct
import threading
import time

MAGIC = b'HGUT'
ACK_MAGIC = b'HGTA'
VERSION = 1
DOWN, UP, MOVE, CANCEL, HOLD = range(5)
APPLIED, SUPERSEDED, EXPIRED = range(3)
HEADER = struct.Struct('>4sBBBBQQI')
POINT = struct.Struct('>IHHH')
ACK = struct.Struct('>4sBBHQ')
MAX_POINTERS = 10
MAX_PAYLOAD = HEADER.size + MAX_POINTERS * POINT.size


@dataclass(frozen=True)
class TouchSnapshot:
    action: int
    rotation: int
    sequence: int
    sender_us: int
    changed: int
    points: tuple


def parse(payload):
    if not isinstance(payload, bytes) or not HEADER.size <= len(payload) <= MAX_PAYLOAD:
        raise ValueError('touch payload bound')
    magic, version, action, count, rotation, sequence, sender_us, changed = HEADER.unpack_from(payload)
    if (magic != MAGIC or version != VERSION or action not in range(5) or count > MAX_POINTERS
            or rotation > 3 or not 0 < sequence < (1 << 63) or not 0 < sender_us < (1 << 63)
            or len(payload) != HEADER.size + count * POINT.size):
        raise ValueError('touch header')
    points = tuple(POINT.unpack_from(payload, HEADER.size + i * POINT.size) for i in range(count))
    tokens = [p[0] for p in points]
    if any(token == 0 for token in tokens) or len(set(tokens)) != count:
        raise ValueError('touch tracking token')
    if action == DOWN and (changed == 0 or changed not in tokens):
        raise ValueError('down snapshot missing changed contact')
    if action == UP and (changed == 0 or changed in tokens):
        raise ValueError('up snapshot still contains lifted contact')
    if action in (MOVE, CANCEL, HOLD) and changed != 0:
        raise ValueError('unexpected changed contact')
    if action == CANCEL and count:
        raise ValueError('cancel snapshot must be empty')
    return TouchSnapshot(action, rotation, sequence, sender_us, changed, points)


def encode(action, sequence, sender_us, points=(), rotation=0, changed=0):
    """Test/signaling helper. points=(tracking_token, normalized_x/y/pressure_u16)."""
    payload = HEADER.pack(MAGIC, VERSION, action, len(points), rotation, sequence, sender_us, changed)
    payload += b''.join(POINT.pack(*point) for point in points)
    parse(payload)
    return payload


def parse_ack(payload):
    if len(payload) != ACK.size:
        raise ValueError('touch ack bound')
    magic, version, status, reserved, sequence = ACK.unpack(payload)
    if magic != ACK_MAGIC or version != VERSION or status not in range(3) or reserved or not 0 < sequence < 1 << 63:
        raise ValueError('touch ack header')
    return sequence, status


class TouchState:
    """Pure conversion with a lock; returned frames are 32-byte scrcpy touch messages.

    Geometry is the *streamed* image size, before hardware_stream.scale_touch()
    maps to the guest physical display. Rotation is inverse display rotation.
    sender_us is not a shared clock. Excess arrival delay relative to the best
    local observation can discard old MOVE/HOLD, but is not one-way latency.
    """
    def __init__(self, width, height, lease_seconds=.75, excess_move_delay_seconds=.10):
        if not 1 <= width <= 65535 or not 1 <= height <= 65535:
            raise ValueError('touch geometry')
        if not .1 <= lease_seconds <= 2 or not .02 <= excess_move_delay_seconds <= .5:
            raise ValueError('touch time bound')
        self.width, self.height = width, height
        self.lease_seconds = lease_seconds
        self.excess_move_delay_us = excess_move_delay_seconds * 1e6
        self.active = {}
        self.sequence = 0
        self.sender_us = 0
        self.last_applied = None
        self.minimum_offset_us = None
        self.lock = threading.Lock()
        self.counts = dict(snapshots=0, superseded=0, expired_moves=0, injected_down=0,
                           injected_move=0, injected_up=0, lease_releases=0, cancels=0)

    def _point(self, point, rotation):
        token, x, y, pressure = point
        if rotation == 1:
            x, y = y, 65535 - x
        elif rotation == 2:
            x, y = 65535 - x, 65535 - y
        elif rotation == 3:
            x, y = 65535 - y, x
        return token, min(self.width - 1, x * self.width // 65535), min(self.height - 1, y * self.height // 65535), pressure

    def _frame(self, token, action, point):
        x, y, pressure = point
        # action_button=buttons=0 is a touchscreen, never a Mac mouse.
        return struct.pack('>BBQiiHHHII', 2, action, token, x, y, self.width, self.height,
                           0 if action == 1 else pressure, 0, 0)

    def _release(self):
        frames = [self._frame(token, 1, point) for token, point in self.active.items()]
        self.counts['injected_up'] += len(frames)
        self.active.clear()
        self.last_applied = None
        return frames

    def consume(self, payload, now=None):
        snapshot = parse(payload)
        now = time.monotonic() if now is None else now
        with self.lock:
            self.counts['snapshots'] += 1
            critical = snapshot.action in (DOWN, UP, CANCEL)
            if snapshot.sequence <= self.sequence:
                self.counts['superseded'] += 1
                return (ACK.pack(ACK_MAGIC, VERSION, SUPERSEDED, 0, snapshot.sequence) if critical else None), []
            if snapshot.sender_us < self.sender_us:
                raise ValueError('sender time moved backwards')
            self.sequence, self.sender_us = snapshot.sequence, snapshot.sender_us
            offset = now * 1e6 - snapshot.sender_us
            if self.minimum_offset_us is None or offset < self.minimum_offset_us:
                self.minimum_offset_us = offset
            if (snapshot.action in (MOVE, HOLD)
                    and offset - self.minimum_offset_us > self.excess_move_delay_us):
                self.counts['expired_moves'] += 1
                return None, self._expire(now)
            transformed = [self._point(point, snapshot.rotation) for point in snapshot.points]
            wanted = {point[0]: point[1:] for point in transformed}
            frames = []
            # Release first, so reused MotionEvent IDs cannot combine gestures.
            for token in list(self.active):
                if token not in wanted:
                    frames.append(self._frame(token, 1, self.active.pop(token)))
                    self.counts['injected_up'] += 1
            for token, point in wanted.items():
                if token not in self.active:
                    frames.append(self._frame(token, 0, point))
                    self.counts['injected_down'] += 1
                elif point != self.active[token]:
                    frames.append(self._frame(token, 2, point))
                    self.counts['injected_move'] += 1
                self.active[token] = point
            if snapshot.action == CANCEL:
                self.counts['cancels'] += 1
            self.last_applied = now if self.active else None
            ack = ACK.pack(ACK_MAGIC, VERSION, APPLIED, 0, snapshot.sequence) if critical else None
            return ack, frames

    def _expire(self, now):
        if self.active and self.last_applied is not None and now - self.last_applied >= self.lease_seconds:
            self.counts['lease_releases'] += len(self.active)
            return self._release()
        return []

    def tick(self, now=None):
        with self.lock:
            return self._expire(time.monotonic() if now is None else now)

    def release_all(self):
        with self.lock:
            return self._release()

    def stats(self):
        with self.lock:
            return dict(self.counts, active_pointers=len(self.active))


class UdpTouchBridge:
    """Separate bounded input writer: submit never blocks media receive.

    writer(bytes) must serialize with existing local encoder/IDR writes and have
    bounded I/O. send_ack(bytes) must use the session's shared authenticated UDP
    send lock/sequence. Only acknowledge after local input frames were written.
    Counts contain no coordinates or event timing history.
    """
    def __init__(self, width, height, writer, send_ack, lease_seconds=.75):
        self.state = TouchState(width, height, lease_seconds)
        self.writer, self.send_ack = writer, send_ack
        self.queue = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest_move = None
        self.closed = False
        self.counts = dict(queue_drops=0, move_coalesced=0, invalid=0, writer_errors=0, ack_errors=0)
        self.thread = threading.Thread(target=self._run, name='udp-native-touch-writer', daemon=True)
        self.thread.start()

    def submit(self, payload):
        try:
            snapshot = parse(payload)
        except ValueError:
            with self.lock:
                self.counts['invalid'] += 1
            return False
        with self.lock:
            if self.closed:
                return False
            if snapshot.action in (MOVE, HOLD):
                if self.latest_move is not None:
                    self.counts['move_coalesced'] += 1
                self.latest_move = (payload, time.monotonic())
                return True
            try:
                self.queue.put_nowait((payload, time.monotonic()))
                return True
            except queue.Full:
                self.counts['queue_drops'] += 1
                return False  # No ACK: the phone's bounded edge retransmit can retry.

    def _write(self, frames):
        if frames:
            self.writer(b''.join(frames))

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    item = self.queue.get(timeout=.01)
                except queue.Empty:
                    item = None
                with self.lock:
                    if item is None:
                        item, self.latest_move = self.latest_move, None
                if item is not None:
                    try:
                        # Include time spent in the local writer queue in age.
                        ack, frames = self.state.consume(item[0])
                    except ValueError:
                        with self.lock:
                            self.counts['invalid'] += 1
                        continue
                    self._write(frames)
                    if ack is not None:
                        try:
                            self.send_ack(ack)
                        except Exception:
                            with self.lock:
                                self.counts['ack_errors'] += 1
                self._write(self.state.tick())
        except Exception:
            with self.lock:
                self.counts['writer_errors'] += 1
            self.stop.set()
        finally:
            try:
                self._write(self.state.release_all())
            except Exception:
                with self.lock:
                    self.counts['writer_errors'] += 1

    def close(self):
        with self.lock:
            self.closed = True
        self.stop.set()
        self.thread.join(timeout=.5)

    def stats(self):
        with self.lock:
            counts = dict(self.counts, writer_alive=self.thread.is_alive())
        return dict(counts, **self.state.stats())
