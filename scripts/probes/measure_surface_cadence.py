#!/usr/bin/env python3
"""Poll a selected app's SurfaceFlinger video layer, without saving screen pixels.

The present timestamps measure the selected source or phone layer. The
first poll is a baseline: historical ring entries are excluded from the window.
"""
import argparse
import json
import math
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

INT64_MAX = 2**63-1
MAX_RING_ROWS = 256
POLL_MS = 500
BLOCKED_CALL_MS = 1000
TAIL_BUDGET_MIN_MS = 200
TAIL_BUDGET_MAX_MS = 500


def next_poll_budget_ms(recent_adb_ms):
    """Prevent a tail-started call; never forgive a call that actually timed out.

    Twice the largest of the last four list+latency durations is a bounded
    estimate, not a guarantee about a future ADB call or the SF ring capacity.
    """
    if any(type(value) not in (int,float) or not math.isfinite(value) or value < 0
           for value in recent_adb_ms):
        raise ValueError('poll_budget_numeric_bound')
    estimate=math.ceil(2*max(recent_adb_ms[-4:],default=0))
    return max(TAIL_BUDGET_MIN_MS,min(TAIL_BUDGET_MAX_MS,estimate))


def enough_tail_budget(remaining_ms,recent_adb_ms):
    if type(remaining_ms) not in (int,float) or not math.isfinite(remaining_ms) or remaining_ms < 0:
        raise ValueError('remaining_poll_budget_bound')
    return remaining_ms >= next_poll_budget_ms(recent_adb_ms)


def parse_ring(raw):
    """Keep only numeric SF actual endpoints; capacity is observed output slots, not configured capacity."""
    if not isinstance(raw, str) or len(raw) > 65536:
        raise ValueError('ring_output_bound')
    lines = raw.splitlines()
    if len(lines) > MAX_RING_ROWS+1:
        raise ValueError('ring_row_bound')
    header_valid = bool(lines and re.fullmatch(r'\d+', lines[0].strip()))
    vsync = int(lines[0].strip()) if header_valid else None
    if header_valid and not 0 <= vsync < INT64_MAX:
        header_valid, vsync = False, None
    stamps, slots, malformed, pending = set(), 0, 0, 0
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3 or not all(re.fullmatch(r'\d+', value) for value in parts):
            malformed += 1
            continue
        values = [int(value) for value in parts]
        if any(value > INT64_MAX for value in values):
            malformed += 1
            continue
        slots += 1
        actual = values[1]
        if 0 < actual < INT64_MAX:
            stamps.add(actual)
        elif actual == INT64_MAX:
            pending += 1
    return {'stamps': stamps, 'slots': slots, 'malformed_rows': malformed,
            'pending_slots': pending, 'header_valid': int(header_valid), 'vsync_ns': vsync}


class RingCoverage:
    """Closed numeric continuity evidence; no-overlap alone never invents missing frames.

    Status: 1 first baseline, 2 overlap (including idle), 3 full disjoint risk,
    4 non-full disjoint unverified, 5 empty, 6 layer missing, 7 layer changed,
    8 ADB failure, 9 malformed, 10 actual endpoint regression.
    Layer continuity is exact selected list-name continuity, not a native generation proof.
    """
    def __init__(self):
        self.previous = None
        self.baseline_max = None
        self.seen = set()
        self.rows = []
        self.last_elapsed_ms = None
        self.invalid = False
        self.unverified = False

    def observe(self, ring, *, elapsed_ms, layer_state=1, list_ms=0, latency_ms=0, adb_error=0):
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (elapsed_ms,list_ms,latency_ms)) or elapsed_ms < 0
                or self.last_elapsed_ms is not None and elapsed_ms < self.last_elapsed_ms
                or type(layer_state) is not int or layer_state not in (0, 1, 2)
                or type(adb_error) is not int or adb_error not in (0, 1, 2)
                or min(list_ms, latency_ms) < 0):
            raise ValueError('poll_numeric_bound')
        previous = self.previous or set()
        stamps = ring['stamps'] if ring is not None else set()
        before = len(self.seen)
        row = {'elapsed_s': round(elapsed_ms/1000, 3),
               'poll_interval_ms': round(elapsed_ms-self.last_elapsed_ms, 3) if self.last_elapsed_ms is not None else 0,
               'list_adb_ms': round(list_ms, 3), 'latency_adb_ms': round(latency_ms, 3),
               'blocked_adb_call': int(max(list_ms, latency_ms) >= BLOCKED_CALL_MS),
               'adb_error_code': adb_error, 'layer_state_code': layer_state,
               'previous_ring_presentations': len(previous), 'ring_presentations': len(stamps),
               'ring_observed_slots': ring['slots'] if ring is not None else 0,
               'ring_pending_slots': ring['pending_slots'] if ring is not None else 0,
               'malformed_rows': ring['malformed_rows'] if ring is not None else 0,
               'ring_overlap_presentations': len(previous & stamps),
               'previous_actual_last_ns': max(previous) if previous else 0,
               'current_actual_first_ns': min(stamps) if stamps else 0,
               'current_actual_last_ns': max(stamps) if stamps else 0,
               'ring_actual_span_ms': round((max(stamps)-min(stamps))/1e6, 3) if stamps else 0,
               'endpoint_forward_gap_ms': round((min(stamps)-max(previous))/1e6, 3) if previous and stamps and not previous & stamps else 0,
               'first_poll': int(not self.rows), 'idle_ring': int(bool(previous) and stamps == previous),
               'new_presentations': 0}
        if adb_error:
            status = 8; self.invalid = True
        elif layer_state == 0:
            status = 6; self.invalid = True
        elif layer_state == 2:
            status = 7; self.invalid = True
        elif ring is None or not ring['header_valid'] or ring['malformed_rows']:
            status = 9; self.invalid = True
        elif not stamps:
            status = 5; self.unverified = True
        elif previous and max(stamps) < max(previous):
            status = 10; self.invalid = True
        else:
            if self.baseline_max is None:
                self.baseline_max = max(stamps)
                status = 1
            elif previous & stamps:
                status = 2
            else:
                # A full new output can have wrapped; a non-full new output
                # could instead be cleared/recreated or sparse. Neither gives
                # an observed count of lost frames, and sparse isn't an error.
                status = 3 if ring['slots'] > 0 and len(stamps) >= ring['slots'] else 4
                self.unverified = True
            self.seen.update(value for value in stamps if value > self.baseline_max)
            row['new_presentations'] = len(self.seen)-before
            self.previous = stamps
        row['continuity_status_code'] = status
        self.rows.append(row)
        self.last_elapsed_ms = elapsed_ms
        return row

    def summary(self):
        codes = [row['continuity_status_code'] for row in self.rows]
        return {'schema_version': 1, 'poll_count': len(codes), 'baseline_observed': int(self.baseline_max is not None),
                'same_selected_layer_all_polls': int(bool(codes) and all(row['layer_state_code'] == 1 for row in self.rows)),
                'overlap_comparisons': codes.count(2), 'full_ring_no_overlap_risk': codes.count(3),
                'nonfull_ring_no_overlap_unverified': codes.count(4), 'empty_ring_polls': codes.count(5),
                'layer_missing_polls': codes.count(6), 'layer_changed_polls': codes.count(7),
                'adb_failure_polls': codes.count(8), 'malformed_polls': codes.count(9),
                'actual_endpoint_regression_polls': codes.count(10),
                'blocked_adb_calls': sum(row['blocked_adb_call'] for row in self.rows),
                'observed_slots_max': max((row['ring_observed_slots'] for row in self.rows), default=0),
                'poll_interval_max_ms': max((row['poll_interval_ms'] for row in self.rows), default=0),
                'adb_call_max_ms': max((max(row['list_adb_ms'], row['latency_adb_ms']) for row in self.rows), default=0),
                'sampling_invalid_observed': int(self.invalid), 'continuity_unverified': int(self.unverified),
                'continuity_complete_observed': int(len(codes) > 1 and self.baseline_max is not None and not self.invalid and not self.unverified)}


def percentile(values, q):
    if not values:
        return None
    a = sorted(values)
    n = (len(a) - 1) * q
    i = int(n)
    return round(a[i] + (a[min(i + 1, len(a) - 1)] - a[i]) * (n - i), 3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', default='emulator-5556')
    p.add_argument('--package', default='com.google.android.youtube')
    p.add_argument('--seconds', type=float, default=45)
    p.add_argument('--wait-layer', type=float, default=0, help='bounded wait for the selected app stream to start')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 2 <= args.seconds <= 300:
        p.error('duration must be between 2 and 300 seconds')
    if not 0 <= args.wait_layer <= 30:
        p.error('layer wait must be between 0 and 30 seconds')
    adb = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk'))) / 'platform-tools/adb'

    def shell(command, timeout=8):
        return subprocess.run([str(adb), '-s', args.serial, 'shell', command],
                              capture_output=True, text=True, check=True, timeout=timeout).stdout

    def list_layers(timeout=8):
        candidates = []
        for line in shell('dumpsys SurfaceFlinger --list',timeout).splitlines():
            if args.package in line and 'SurfaceView' in line and '(BLAST)' in line:
                # New Android prints RequestedLayerState{NAME parentId=...}; old
                # Android emits NAME directly. Only the actual layer name is sent.
                if line.startswith('RequestedLayerState{'):
                    line = line[len('RequestedLayerState{'):].split(' parentId=', 1)[0]
                candidates.append(line.strip())
        return candidates

    def pick_layer():
        candidates = list_layers()
        return candidates[-1] if candidates else None

    layer = pick_layer()
    wait_until = time.monotonic() + args.wait_layer
    while not layer and time.monotonic() < wait_until:
        time.sleep(.5)
        layer = pick_layer()
    if not layer:
        raise SystemExit('No selected app video SurfaceView layer found')
    start_ns = time.monotonic_ns()
    coverage = RingCoverage()
    recent_adb_ms=[]
    tail_poll_skipped_count=0
    tail_budget_at_skip_ms=tail_remaining_at_skip_ms=0
    vsync_ns = None
    while (time.monotonic_ns() - start_ns) / 1e9 < args.seconds:
        remaining_ms=max(0,args.seconds*1000-(time.monotonic_ns()-start_ns)/1e6)
        if not enough_tail_budget(remaining_ms,recent_adb_ms):
            tail_poll_skipped_count+=1
            tail_budget_at_skip_ms=next_poll_budget_ms(recent_adb_ms)
            tail_remaining_at_skip_ms=remaining_ms
            # No new observation is created. Preserve the requested wall
            # duration and explicitly retain the unknown trailing interval.
            if remaining_ms > 0:
                time.sleep(remaining_ms/1000)
            break
        ring = None; state = 1; adb_error = 0; list_ms = latency_ms = 0
        called = time.monotonic_ns()
        try:
            remaining = max(.001,args.seconds-(called-start_ns)/1e9)
            candidates = list_layers(min(8,remaining))
        except (subprocess.TimeoutExpired,subprocess.CalledProcessError) as failure:
            adb_error = 1 if isinstance(failure,subprocess.TimeoutExpired) else 2
            candidates = []
        finally:
            list_ms = (time.monotonic_ns()-called)/1e6
        if not adb_error:
            state = 0 if layer not in candidates else 2 if candidates[-1] != layer else 1
        if not adb_error and state == 1:
            called = time.monotonic_ns()
            try:
                remaining = max(.001,args.seconds-(called-start_ns)/1e9)
                ring = parse_ring(shell('dumpsys SurfaceFlinger --latency '+shlex.quote(layer),min(8,remaining)))
                if ring['header_valid']:
                    vsync_ns = ring['vsync_ns']
            except (subprocess.TimeoutExpired,subprocess.CalledProcessError) as failure:
                adb_error = 1 if isinstance(failure,subprocess.TimeoutExpired) else 2
            except ValueError:
                ring = None
            finally:
                latency_ms = (time.monotonic_ns()-called)/1e6
        coverage.observe(ring,elapsed_ms=(time.monotonic_ns()-start_ns)/1e6,
                         layer_state=state,list_ms=list_ms,latency_ms=latency_ms,adb_error=adb_error)
        recent_adb_ms.append(list_ms+latency_ms)
        recent_adb_ms=recent_adb_ms[-4:]
        remaining = args.seconds-(time.monotonic_ns()-start_ns)/1e9
        if remaining > 0:
            time.sleep(min(POLL_MS/1000,remaining))
    seen = coverage.seen
    ordered = sorted(seen)
    gaps = [(b-a)/1e6 for a,b in zip(ordered, ordered[1:])]
    elapsed = (time.monotonic_ns()-start_ns)/1e9
    numeric_coverage = coverage.summary()
    numeric_coverage.update(requested_window_ms=round(args.seconds*1000,3),
        first_poll_elapsed_ms=round(coverage.rows[0]['elapsed_s']*1000,3) if coverage.rows else 0,
        last_poll_elapsed_ms=round(coverage.rows[-1]['elapsed_s']*1000,3) if coverage.rows else 0,
        trailing_unobserved_host_ms=round(max(0,elapsed*1000-coverage.last_elapsed_ms),3) if coverage.last_elapsed_ms is not None else round(elapsed*1000,3),
        tail_poll_skipped_count=tail_poll_skipped_count,
        tail_budget_at_skip_ms=tail_budget_at_skip_ms,
        tail_remaining_at_skip_ms=round(tail_remaining_at_skip_ms,3),
        tail_coverage_unknown=int(coverage.last_elapsed_ms is None or coverage.last_elapsed_ms < elapsed*1000))
    report = {'scope':'selected app SurfaceFlinger layer actual present timestamps; not unique decoded content, network latency or optical measurement',
              'serial':args.serial, 'package':args.package, 'layer':layer,
              'unix_ms':time.time_ns()//1_000_000, 'seconds':round(elapsed,3),
              'display_vsync_ns':vsync_ns, 'presented_frames':len(seen),
              'fps':round(len(seen)/elapsed,3),
              'cadence_fps':round((len(ordered)-1)*1e9/(ordered[-1]-ordered[0]),3) if len(ordered)>1 else None,
              'gaps_ms': {'p50':percentile(gaps,.5),'p95':percentile(gaps,.95),
                          'p99':percentile(gaps,.99),'max':max(gaps) if gaps else None,
                          'over_50':sum(x>50 for x in gaps), 'over_100':sum(x>100 for x in gaps)},
              'polls':coverage.rows, 'presentation_ns':ordered, 'ring_coverage':numeric_coverage,
              'limitations':'continuity_complete_observed covers only the observed poll chain, not every instant of the requested window. Tail budget can prevent a new short-budget call; skipped or trailing time stays unknown and no observed timeout is forgiven. Coverage verifies exact selected list-name and numeric ring overlap, not native layer generation. Observed output slots are not configured capacity. Disjoint or empty rings remain unverified; no missing frame count is invented. FPS remains observed lower-bound cadence if coverage is incomplete. No content hash, media file frame-rate, optical or cross-device clock assertion.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('polls','presentation_ns')},ensure_ascii=False))


if __name__ == '__main__':
    main()
