"""Fixed numeric, best-effort host timing observations; never media policy.

The caller opts in locally and supplies an asynchronous bounded sink. No payload,
command value, account, network tuple or arbitrary exception text is accepted.
Trace clock is CLOCK_MONOTONIC; budget.updated is separately named Python
time.monotonic. Neither is the packetizer's CLOCK_UPTIME_RAW epoch.
"""
import math
import os
from pathlib import Path
import secrets
import stat
import time


FIELDS = {
    'raw_loop': frozenset(('iteration', 'begin_ns', 'end_ns', 'prior_end_ns',
        'prior_write_end_ns', 'condition_begin_ns', 'condition_end_ns',
        'condition_wait_begin_ns', 'condition_wait_end_ns',
        'condition_waited', 'queued_before', 'queued_after', 'native_ready',
        'control_begin_ns', 'control_end_ns', 'control_count', 'dequeue_ns',
        'capture_seq', 'source_pts_us', 'skipped', 'outcome', 'raw_frames',
        'frames_submitted', 'pending_frames_replaced', 'idle_repeats')),
    'raw_budget': frozenset(('iteration', 'check_begin_ns', 'check_end_ns',
        'tokens_before_milli', 'tokens_after_delay_milli', 'tokens_after_consume_milli',
        'updated_python_monotonic_ns', 'requested_wait_ns', 'wait_begin_ns',
        'wait_end_ns', 'consume_begin_ns', 'consume_end_ns', 'consumed')),
    'raw_write': frozenset(('iteration', 'capture_seq', 'source_pts_us', 'raw_bytes',
        'write_begin_ns', 'write_end_ns', 'flush_complete', 'idle_repeat',
        'idle_flush_begin_ns', 'idle_flush_end_ns')),
    'feed_read': frozenset(('read_seq', 'begin_ns', 'end_ns', 'record_bytes_before',
        'record_bytes_after', 'target_bytes', 'recv_calls', 'recv_timeouts',
        'outcome', 'cancelled', 'media_records', 'published_bytes')),
    'feed_publish': frozenset(('publish_seq', 'begin_ns', 'end_ns', 'kind',
        'has_source_pts', 'source_pts_us', 'record_bytes', 'write_calls',
        'write_end_ns', 'flush_begin_ns', 'flush_end_ns', 'written_bytes',
        'flush_complete', 'published', 'cancel_before', 'cancel_after',
        'codec_records', 'geometry_records', 'config_records', 'media_records',
        'published_bytes', 'publish_failures')),
    'feed_cancel': frozenset(('at_ns', 'record_bytes', 'partial',
        'cancelled_unpublished_records', 'cancelled_partial_records',
        'cancelled_unpublished_bytes')),
    'host_timing_summary': frozenset(('clock_errors', 'emit_errors', 'schema_errors',
        'emit_attempts', 'producer_quiescent')),
}


def private_trace_attempt(directory):
    """Require an existing private owner directory; reject symlink components.

    Caller-controlled directory is never obtained from an HTTP/App parameter.
    No credentials are copied into it. Exclusive random subdirectory contains
    only this attempt's traces, so cancellation/reconnect cannot reuse a file.
    """
    path = Path(directory).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('trace_directory_symlink_or_nondirectory')
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('trace_directory_requires_private_owner')
    # dir_fd/no-follow narrows the final parent lookup and makes each attempt
    # exclusive. Ancestor substitution by the same owner remains out of scope.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('trace_directory_changed')
        name = 'attempt-' + secrets.token_hex(8)
        os.mkdir(name, 0o700, dir_fd=fd)
    finally:
        os.close(fd)
    return path / name


class HostTimingTrace:
    """Observation failures are counted and cannot replace a media exception."""
    def __init__(self, sink, clock=None):
        self.sink = sink
        self.clock = clock or (lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC))
        self.clock_errors = self.emit_errors = self.schema_errors = self.emit_attempts = 0
        self.raw_seq = self.read_seq = self.publish_seq = 0
        self.prior_end = self.prior_write_end = 0
        try:
            sink.emit('host_timing_contract',
                clock_domain='host_clock_gettime_CLOCK_MONOTONIC_ns',
                budget_clock_domain='python_time_monotonic_seconds_scaled_ns',
                scope='host_regions_only_not_phone_or_network_latency',
                raw_outcomes='1submitted_2emptyflush_3ptsreject_4cancel_5unset_6failure',
                feed_read_outcomes='1complete_2cancel_3failure',
                feed_kinds='1codec_2geometry_3config_4media',
                counters_scope='best_effort_cumulative_snapshots_not_atomic_capture_cohort')
        except Exception:
            self.emit_errors += 1

    def stamp(self):
        try:
            value = self.clock()
            if type(value) is not int or value <= 0:
                raise ValueError('invalid_diagnostic_clock')
            return value
        except Exception:
            self.clock_errors += 1
            return 0

    def emit(self, event, **fields):
        self.emit_attempts += 1
        if (event not in FIELDS or set(fields) != FIELDS[event]
                or any(type(v) not in (int, float, bool) or
                    isinstance(v, float) and not math.isfinite(v) for v in fields.values())):
            self.schema_errors += 1
            return
        try:
            self.sink.emit(event, **fields)
        except Exception:
            self.emit_errors += 1

    def begin_raw(self):
        try:
            return self._begin_raw()
        except Exception:
            self.emit_errors += 1
            return None

    def _begin_raw(self):
        self.raw_seq += 1
        row = {key: 0 for key in FIELDS['raw_loop']}
        row.update(iteration=self.raw_seq, begin_ns=self.stamp(), prior_end_ns=self.prior_end,
                   prior_write_end_ns=self.prior_write_end, outcome=5)
        budget = {key: 0 for key in FIELDS['raw_budget']}
        write = {key: 0 for key in FIELDS['raw_write']}
        budget['iteration'] = write['iteration'] = self.raw_seq
        return row, budget, write

    def end_raw(self, rows, counters):
        try:
            self._end_raw(rows, counters)
        except Exception:
            # Even an unexpected observation/schema bug in this finally block
            # must not replace the raw writer's original exception.
            self.emit_errors += 1

    def _end_raw(self, rows, counters):
        row, budget, write = rows
        row['end_ns'] = self.stamp()
        for key in ('raw_frames', 'frames_submitted', 'pending_frames_replaced', 'idle_repeats'):
            row[key] = counters[key]
        self.prior_end = row['end_ns']
        if write['write_end_ns']:
            self.prior_write_end = write['write_end_ns']
        self.emit('raw_loop', **row)
        self.emit('raw_budget', **budget)
        self.emit('raw_write', **write)

    def summary(self, producer_quiescent):
        self.emit('host_timing_summary', clock_errors=self.clock_errors,
                  emit_errors=self.emit_errors, schema_errors=self.schema_errors,
                  emit_attempts=self.emit_attempts, producer_quiescent=producer_quiescent)
