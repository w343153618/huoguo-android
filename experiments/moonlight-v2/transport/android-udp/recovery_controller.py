"""Experimental encoder recovery after native UDP whole-frame admission fails.

This is a bounded local encoding policy, not a WAN congestion controller. It
changes neither the packetizer's wire budget nor the phone's 80 ms assembly
deadline. The caller serializes command writes and uses ADAPTIVE_VBR. No socket,
account, emulator, or device is opened here.
"""
from dataclasses import asdict, dataclass
import math
import struct
import time


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class RecoveryDecision:
    reason: str
    requested_bitrate_bps: int
    previous_requested_bitrate_bps: int
    request_idr: bool
    capacity_bytes: int = 0
    full_wire_bytes: int = 0

    def command_bytes(self):
        """Existing HostHardwareSession commands: target 240, then IDR 17.

        0x10 is a different, variable-length scrcpy command and MUST NOT be
        used to update the target. Target acknowledgement is 240 + uint32 BE.
        """
        result = b''
        if self.requested_bitrate_bps != self.previous_requested_bitrate_bps:
            result += b'\xf0' + struct.pack('>I', self.requested_bitrate_bps)
        if self.request_idr:
            result += b'\x11'
        return result

    def report(self):
        return asdict(self)


class RecoveryController:
    """Downward-only recovery with a per-loss epoch limit and monotonic clock.

    consume() accepts whitelisted packetizer events, returning None or a
    RecoveryDecision. The target is the last requested bitrate, not proof that
    VideoToolbox accepted it; acknowledge() records the encoder's actual reply.
    A successful complete IDR resets retry count, never the lowered bitrate.
    """
    def __init__(self, bitrate_bps, wire_bitrate_bps, *, cooldown_s=.5,
                 max_requests=6, minimum_bitrate_bps=1_000_000, clock=time.monotonic):
        if not _integer(bitrate_bps, 1_000_000, 24_000_000):
            raise ValueError('initial target outside experiment range')
        if not _integer(wire_bitrate_bps, 500_000, 40_000_000):
            raise ValueError('wire budget outside packetizer range')
        if not _integer(minimum_bitrate_bps, 1_000_000, bitrate_bps):
            raise ValueError('minimum target outside experiment range')
        if type(cooldown_s) not in (int, float) or not math.isfinite(cooldown_s) or not .1 <= cooldown_s <= 5:
            raise ValueError('cooldown must be finite and at least 100 ms; default remains 500 ms')
        if not _integer(max_requests, 1, 6):
            raise ValueError('request bound outside experiment range')
        self.target_bps = bitrate_bps
        self.initial_bitrate_bps = bitrate_bps
        self.minimum_bitrate_bps = minimum_bitrate_bps
        self.wire_bitrate_bps = wire_bitrate_bps
        self.cooldown_s = float(cooldown_s)
        self.native_cooldown_us = min(500_000, round(self.cooldown_s * 1_000_000))
        self.max_requests = max_requests
        self.clock = clock
        self.last_action = None
        self.last_observed = None
        self.requests = 0
        self.accepted_bitrate_bps = None
        self.rejected_acknowledgements = 0
        self.invalid_events = 0
        self.exhausted = False
        self.pending_budget = None

    def _invalid(self):
        self.invalid_events += 1
        return None

    def acknowledge(self, value):
        if not _integer(value, 0, self.initial_bitrate_bps):
            raise ValueError('invalid bitrate acknowledgement')
        if value == 0:
            self.rejected_acknowledgements += 1
            return False
        # Old asynchronous acknowledgements may arrive after a newer target.
        # They are evidence of acceptance, never permission to raise the target.
        self.accepted_bitrate_bps = value
        return True

    def consume(self, event, now=None):
        if not isinstance(event, dict):
            return self._invalid()
        kind = event.get('event')
        if kind not in ('encoder_budget_feedback', 'request_idr', 'recovery_complete', 'recovery_exhausted'):
            return None
        at = self.clock() if now is None else now
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0 or (self.last_observed is not None and at < self.last_observed):
            return self._invalid()
        self.last_observed = at
        if kind == 'recovery_complete':
            if not _integer(event.get('frame'), 1, (1 << 64)-1) or not _integer(event.get('attempts'), 1, 6):
                return self._invalid()
            self.requests = 0
            self.exhausted = False
            self.pending_budget = None
            # Keep cooldown across successful recovery to prevent alternating
            # loss/success events causing unlimited rapid IDR spikes.
            return None
        if kind == 'recovery_exhausted':
            if event.get('reason') != 'bounded_idr_requests' or event.get('attempts') != 6:
                return self._invalid()
            self.exhausted = True
            return None
        capacity = full_wire = 0
        if kind == 'encoder_budget_feedback':
            if (event.get('reason') != 'idr_exceeds_assembly_budget'
                or event.get('action') != 'smaller_idr_or_lower_encoder_bitrate_required'
                or not _integer(event.get('wire_bitrate'), self.wire_bitrate_bps, self.wire_bitrate_bps)
                or not _integer(event.get('budget_us'), 80_000, 80_000)
                or not _integer(event.get('full_wire_bytes'), 1, 2_000_000)):
                return self._invalid()
            full_wire = event['full_wire_bytes']
            capacity = self.wire_bitrate_bps * event['budget_us'] // 8_000_000
            # Keep the observation even during cooldown. Otherwise a failed
            # IDR arriving just after each request could always be ignored,
            # causing retries at the same excessive bitrate until exhaustion.
            self.pending_budget = (capacity, full_wire)
        elif (event.get('reason') != 'local_dependency_or_admission'
              or not _integer(event.get('attempt'), 1, 6)
              or not _integer(event.get('cooldown_us'), self.native_cooldown_us, self.native_cooldown_us)):
            return self._invalid()
        if self.exhausted or self.requests >= self.max_requests:
            self.exhausted = True
            return None
        if self.last_action is not None and at-self.last_action < self.cooldown_s:
            return None
        previous = self.target_bps
        reason = kind
        if self.pending_budget is not None:
            capacity, full_wire = self.pending_budget
            self.pending_budget = None
            if kind != 'encoder_budget_feedback':
                reason = 'pending_encoder_budget_feedback'
        if full_wire and previous > self.minimum_bitrate_bps:
            # Leave 15% scheduling margin, lower at least 15%, but never reduce
            # by more than half at a time. Proportional prediction is imperfect:
            # an intra frame is not guaranteed linear in AverageBitRate.
            numerator = min(85 * full_wire, 85 * capacity)
            proposed = max(previous // 2, previous * numerator // (100 * full_wire))
            proposed = (proposed // 100_000) * 100_000
            proposed = max(proposed, ((previous+199_999)//200_000)*100_000)
            self.target_bps = max(self.minimum_bitrate_bps, min(previous-1, proposed))
        self.requests += 1
        self.last_action = at
        return RecoveryDecision(reason, self.target_bps, previous, True, capacity, full_wire)

    def report(self):
        return {'scope': 'experimental_local_encoder_recovery_not_WAN_congestion_control',
                'initial_bitrate_bps': self.initial_bitrate_bps,
                'last_requested_bitrate_bps': self.target_bps,
                'encoder_accepted_bitrate_bps': self.accepted_bitrate_bps,
                'rejected_acknowledgements': self.rejected_acknowledgements,
                'invalid_events': self.invalid_events, 'epoch_requests': self.requests,
                'exhausted': self.exhausted, 'wire_bitrate_bps': self.wire_bitrate_bps,
                'pending_budget_feedback': self.pending_budget is not None,
                'assembly_budget_ms': 80, 'recovery_cooldown_ms': self.cooldown_s * 1000,
                'maximum_requests_per_loss_epoch': self.max_requests, 'automatic_increase': False}
