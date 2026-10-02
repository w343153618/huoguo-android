"""Bounded experimental UDP feedback policy, not a GCC implementation.

HGUF reports receive-loop counters. Delivery ratios are interval heuristics,
not packet-loss measurements. RTT is measured entirely on the host clock;
phone timestamps only describe echo processing. No sockets or devices open.
"""
from collections import deque
from dataclasses import asdict, dataclass
import math
import statistics
import struct
import time

FEEDBACK = struct.Struct('>4sI13Q')
PING = struct.Struct('>4sIQ')
PONG = struct.Struct('>4sIQQQ')
FIELDS = ('sequence', 'start_us', 'end_us', 'datagrams', 'wire_bytes',
          'completed', 'expired', 'reference_lost', 'recovered_shards',
          'receive_over80ms', 'processing_max_us', 'socket_wait_max_us',
          'highest_frame')
CUMULATIVE = ('datagrams', 'wire_bytes', 'completed', 'expired',
              'reference_lost', 'recovered_shards', 'receive_over80ms', 'highest_frame')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def parse_feedback(payload):
    if len(payload) != FEEDBACK.size:
        raise ValueError('HGUF exact length')
    magic, version, *values = FEEDBACK.unpack(payload)
    value = dict(zip(FIELDS, values))
    if magic != b'HGUF' or version != 1 or not value['sequence']:
        raise ValueError('HGUF prefix/sequence')
    duration = value['end_us'] - value['start_us']
    if not 50_000 <= duration <= 2_000_000:
        raise ValueError('HGUF interval outside 50ms..2s')
    if value['processing_max_us'] > 5_000_000 or value['socket_wait_max_us'] > 5_000_000:
        raise ValueError('HGUF loop duration bound')
    return value


@dataclass(frozen=True)
class FeedbackDecision:
    reason: str
    previous_bitrate_bps: int
    requested_bitrate_bps: int

    def command_bytes(self):
        return b'\xf0' + struct.pack('>I', self.requested_bitrate_bps)

    def report(self):
        return asdict(self)


class NetworkFeedbackController:
    """Slow downward pressure response and ACK-gated, bounded upward probing.

    Two bad >=500ms windows are required. A minimum one-second adjustment
    cooldown prevents feedback floods from changing rate. Upward probing needs
    five seconds of stable, recent delivery plus a matching encoder ACK. Local
    recovery can lower a permanent ceiling. Legacy encoder ACKs contain a value
    but no sequence, so equal-value stale ACKs cannot be distinguished perfectly.
    """
    def __init__(self, bitrate_bps, wire_bitrate_bps, *, clock=time.monotonic):
        if type(bitrate_bps) is not int or not 1_000_000 <= bitrate_bps <= 24_000_000:
            raise ValueError('feedback encoding target bound')
        if type(wire_bitrate_bps) is not int or not 500_000 <= wire_bitrate_bps <= 40_000_000:
            raise ValueError('feedback wire budget bound')
        self.clock = clock
        self.initial_bps = self.target_bps = self.ceiling_bps = bitrate_bps
        self.wire_bps = wire_bitrate_bps
        self.minimum_bps = min(1_000_000, bitrate_bps)
        self.last = self.window = None
        self.last_at = self.window_at = None
        self.last_sent_bytes = self.window_sent_bytes = 0
        self.window_local_pressure = False
        self.pending = None
        self.accepted_bps = None
        self.accepted_current = False
        self.last_action = None
        self.healthy_since = None
        self.bad_windows = 0
        self.rate_ewma = self.delivery_ratio = None
        self.ping_id = 0
        self.pings = {}
        self.last_ping_at = None
        self.rtt_min_ms = self.rtt_ewma_ms = None
        self.rtt_latest_ms = None
        self.recent_pongs = deque(maxlen=8)
        self.last_rtt_at = None
        self.last_rtt_processing_ms = None
        self.events = deque(maxlen=128)
        self.samples = deque(maxlen=512)
        self.samples_evicted = 0
        self.counters = dict(valid_feedback=0, invalid_feedback=0, old_feedback=0,
                             feedback_rate_limited=0, feedback_sequence_gaps=0,
                             evaluated_windows=0, downward_updates=0, upward_updates=0,
                             pings_sent=0, pongs_accepted=0, invalid_pongs=0,
                             encoder_ack_matched=0, encoder_ack_unmatched=0,
                             encoder_ack_rejected=0, encoder_ack_timeout=0)

    def tick(self, now=None):
        at = self.clock() if now is None else now
        if not _number(at):
            raise ValueError('feedback clock')
        if self.pending and at - self.pending[1] >= 2:
            self.pending = None
            self.accepted_current = False
            self.counters['encoder_ack_timeout'] += 1
        for identifier, (_, sent_at) in list(self.pings.items()):
            if at - sent_at >= 3:
                del self.pings[identifier]
        while self.recent_pongs and at-self.recent_pongs[0][0] > 3:
            self.recent_pongs.popleft()
        if self.last_at is not None and at-self.last_at > 1:
            self.healthy_since = None
        return at

    def ping(self, now=None):
        at = self.tick(now)
        if self.last_ping_at is not None and at-self.last_ping_at < 1:
            return None
        self.ping_id = (self.ping_id % 0xffffffff) + 1
        stamp = int(at*1_000_000)
        self.pings[self.ping_id] = (stamp, at)
        self.last_ping_at = at
        self.counters['pings_sent'] += 1
        return PING.pack(b'HGPQ', self.ping_id, stamp)

    def pong(self, payload, now=None):
        at = self.tick(now)
        try:
            if len(payload) != PONG.size:
                raise ValueError('HGPR length')
            magic, identifier, stamp, arrival, sent = PONG.unpack(payload)
            outstanding = self.pings.get(identifier)
            if magic != b'HGPR' or outstanding is None or outstanding[0] != stamp:
                raise ValueError('HGPR unissued token')
            elapsed = at-outstanding[1]
            if not 0 <= elapsed <= 3 or sent < arrival or sent-arrival > 500_000:
                raise ValueError('HGPR clock bounds')
            del self.pings[identifier]
        except (ValueError, struct.error):
            self.counters['invalid_pongs'] += 1
            return False
        rtt = elapsed*1000
        self.rtt_min_ms = rtt if self.rtt_min_ms is None else min(self.rtt_min_ms, rtt)
        self.rtt_ewma_ms = rtt if self.rtt_ewma_ms is None else .75*self.rtt_ewma_ms+.25*rtt
        self.rtt_latest_ms = rtt
        self.recent_pongs.append((at, rtt))
        self.last_rtt_at = at
        self.last_rtt_processing_ms = (sent-arrival)/1000
        self.counters['pongs_accepted'] += 1
        return True

    def _rtt_evidence(self, at):
        """Do not reuse startup EWMA memory as current network pressure.

        Three recent authenticated echoes are necessary. Median AND EWMA AND
        the latest raw sample must exceed the baseline margin. The raw condition
        prevents two initial high samples plus one fresh low sample from still
        qualifying merely because their three-sample median has not caught up.
        """
        recent = [rtt for arrived, rtt in self.recent_pongs if 0 <= at-arrived <= 3]
        median = statistics.median(recent) if recent else None
        threshold = None if self.rtt_min_ms is None else self.rtt_min_ms+max(20, self.rtt_min_ms*.5)
        warm = len(recent) >= 3
        fresh = self.last_rtt_at is not None and 0 <= at-self.last_rtt_at < 2
        pressure = bool(warm and fresh and threshold is not None and
                        median > threshold and self.rtt_ewma_ms > threshold and self.rtt_latest_ms > threshold)
        return {'host_clock_rtt_latest_ms': self.rtt_latest_ms,
                'host_clock_rtt_recent_median_ms': median,
                'rtt_recent_pong_count': len(recent), 'rtt_warmup_ready': warm,
                'rtt_latest_fresh': fresh, 'rtt_pressure_threshold_ms': threshold,
                'rtt_recent_window_ms': 3000, 'rtt_pressure': pressure}

    def requested(self, target, now=None):
        """Call after a serialized encoder command write, including recovery."""
        at = self.tick(now)
        if type(target) is not int or not self.minimum_bps <= target <= self.ceiling_bps:
            raise ValueError('requested feedback target exceeds recovery ceiling')
        self.target_bps = target
        self.pending = (target, at)
        self.accepted_current = False
        self.last_action = at

    def set_recovery_ceiling(self, value):
        if type(value) is not int or not self.minimum_bps <= value <= self.initial_bps:
            raise ValueError('recovery ceiling bound')
        if value < self.ceiling_bps:
            self.ceiling_bps = value
            self.target_bps = min(self.target_bps, self.ceiling_bps)
            self.healthy_since = None

    def acknowledge(self, value):
        if type(value) is not int or not 0 <= value <= self.initial_bps:
            raise ValueError('encoder ACK bound')
        if not value:
            self.counters['encoder_ack_rejected'] += 1
            self.pending = None
            self.accepted_current = False
            self.healthy_since = None
            return False
        self.accepted_bps = value
        if self.pending and self.pending[0] == value:
            self.pending = None
            self.accepted_current = value == self.target_bps
            self.counters['encoder_ack_matched'] += 1
            return True
        self.counters['encoder_ack_unmatched'] += 1
        # An old reply is observation only, never permission to raise the target.
        return False

    def consume(self, payload, *, host_video_wire_bytes, local_pressure=False, now=None):
        at = self.tick(now)
        try:
            current = parse_feedback(payload)
            if type(host_video_wire_bytes) is not int or host_video_wire_bytes < self.last_sent_bytes:
                raise ValueError('host send counter regressed')
            if self.last is not None:
                if current['sequence'] <= self.last['sequence'] or current['end_us'] <= self.last['end_us']:
                    self.counters['old_feedback'] += 1
                    return None
                if current['start_us'] < self.last['end_us'] or any(current[k] < self.last[k] for k in CUMULATIVE):
                    raise ValueError('HGUF counters/interval regressed')
                span = current['end_us']-self.last['end_us']
                received = current['wire_bytes']-self.last['wire_bytes']
                if received > self.wire_bps*max(span, 100_000)//8_000_000 + 512_000:
                    raise ValueError('HGUF implausible receive counter jump')
                if at < self.last_at:
                    raise ValueError('host clock regressed')
                if at-self.last_at < .025:
                    self.counters['feedback_rate_limited'] += 1
                    return None
                self.counters['feedback_sequence_gaps'] += current['sequence']-self.last['sequence']-1
        except (ValueError, struct.error):
            self.counters['invalid_feedback'] += 1
            return None
        self.last, self.last_at, self.last_sent_bytes = current, at, host_video_wire_bytes
        self.counters['valid_feedback'] += 1
        self.window_local_pressure |= bool(local_pressure)
        if self.window is None:
            self.window, self.window_at, self.window_sent_bytes = current, at, host_video_wire_bytes
            return None
        duration = (current['end_us']-self.window['end_us'])/1_000_000
        local_duration = at-self.window_at
        if duration < .5 or local_duration < .4:
            return None
        previous = self.window
        received = current['wire_bytes']-previous['wire_bytes']
        host_bytes = host_video_wire_bytes-self.window_sent_bytes
        delivered_bps = received*8/duration
        self.rate_ewma = delivered_bps if self.rate_ewma is None else .7*self.rate_ewma+.3*delivered_bps
        # This is NOT a packet-loss fraction: feedback delays and bursting can
        # shift the two clocks' interval boundaries even with perfect delivery.
        comparable = .75 <= duration/max(local_duration, .001) <= 1.25
        self.delivery_ratio = min(2., received/host_bytes) if comparable and host_bytes > 16_000 else None
        loss_pressure = any(current[k] > previous[k] for k in ('expired', 'reference_lost', 'receive_over80ms'))
        rtt_evidence = self._rtt_evidence(at)
        rtt_pressure = rtt_evidence['rtt_pressure']
        pressure = loss_pressure or self.window_local_pressure or rtt_pressure
        if len(self.samples) == self.samples.maxlen:
            self.samples_evicted += 1
        self.samples.append({'host_monotonic_us': round(at*1_000_000),
            'phone_interval_end_us': current['end_us'], 'interval_us': round(duration*1_000_000),
            'received_video_wire_bytes': received, 'host_interval_video_wire_bytes': host_bytes,
            'delivery_rate_bps': round(delivered_bps), 'delivery_ratio_not_packet_loss': self.delivery_ratio,
            'expired_delta': current['expired']-previous['expired'],
            'reference_lost_delta': current['reference_lost']-previous['reference_lost'],
            'recovered_shards_delta': current['recovered_shards']-previous['recovered_shards'],
            'receive_over80ms_delta': current['receive_over80ms']-previous['receive_over80ms'],
            'phone_processing_max_us': current['processing_max_us'],
            'host_clock_rtt_ewma_ms': self.rtt_ewma_ms,
            **rtt_evidence,
            'loss_or_rx_pressure': loss_pressure, 'local_socket_pressure': self.window_local_pressure,
            'target_bps': self.target_bps})
        self.window_local_pressure = False
        self.window, self.window_at, self.window_sent_bytes = current, at, host_video_wire_bytes
        self.counters['evaluated_windows'] += 1
        if pressure:
            self.healthy_since = None
            self.bad_windows += 1
        else:
            self.bad_windows = 0
            if self.healthy_since is None:
                self.healthy_since = at
        cooldown = self.last_action is None or at-self.last_action >= 1
        decision = None
        if self.pending is None and cooldown and self.bad_windows >= 2:
            # Round upward so quantization never makes the reduction exceed 20%.
            target = max(self.minimum_bps, (self.target_bps*80+9_999_999)//10_000_000*100_000)
            if target < self.target_bps:
                decision = FeedbackDecision('sustained_receive_or_rtt_pressure', self.target_bps, target)
                self.counters['downward_updates'] += 1
                self.bad_windows = 0
        elif (self.pending is None and cooldown and self.accepted_current and
              self.healthy_since is not None and at-self.healthy_since >= 5 and
              self.delivery_ratio is not None and .8 <= self.delivery_ratio <= 1.2 and
              self.rate_ewma >= self.target_bps*.8 and self.target_bps < self.ceiling_bps):
            step = max(1, self.target_bps*5//100)
            if step >= 100_000:
                step = step//100_000*100_000
            target = min(self.ceiling_bps, self.target_bps+step)
            decision = FeedbackDecision('five_seconds_stable_bounded_probe', self.target_bps, target)
            self.counters['upward_updates'] += 1
            self.healthy_since = at
        if decision:
            self.events.append(dict(decision.report(), host_monotonic_s=at))
        return decision

    def report(self, now=None):
        at = self.tick(now)
        return {'scope': 'conservative_interval_pressure_policy_not_GCC',
                'requested_target_bps': self.target_bps, 'initial_target_bps': self.initial_bps,
                'permanent_recovery_ceiling_bps': self.ceiling_bps,
                'encoder_accepted_bps': self.accepted_bps, 'encoder_ack_pending': self.pending is not None,
                'feedback_stale': self.last_at is None or at-self.last_at > 1,
                'video_delivery_rate_ewma_bps': self.rate_ewma,
                'delivery_interval_ratio_not_packet_loss': self.delivery_ratio,
                'host_clock_rtt_min_ms': self.rtt_min_ms, 'host_clock_rtt_ewma_ms': self.rtt_ewma_ms,
                **self._rtt_evidence(at),
                'last_phone_echo_processing_ms': self.last_rtt_processing_ms,
                'counters': dict(self.counters), 'actions': list(self.events),
                'interval_samples': list(self.samples), 'interval_samples_evicted': self.samples_evicted,
                'limitations': ['No transport-cc packet arrival feedback or GCC trendline',
                               'Encoder ACK has value but no sequence; equal-value stale ACK is ambiguous',
                               'Delivery interval ratio is not packet loss or a path capacity measurement',
                               'Recovery ceiling never increases within this session']}
