#!/usr/bin/env python3
"""Inert FIFO2 trace reconstruction; fixed historical decisions, never an FPS forecast.

Only complete pre-encode RGBA frames are considered. Reuse the strict bounded
capture reader; no native/phone timestamps, PTS subtraction or device access.
"""
import argparse
from collections import Counter, deque
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.probes import host_timing_analysis as host
from scripts.probes.capture_trace_analysis import distribution


def _identity(row):
    return row['capture_seq'], row['source_pts_us'], row['dequeue_ns']


def reconstruct(stream, window):
    """Require a complete trace and unambiguous lock-ordered FIFO2 operations.

    The newest alternative is evaluated independently at each *observed* FIFO
    dequeue. It never mutates subsequent historical decisions or queue contents.
    A real latest-policy run could change pacing, empty reads and delivered FPS.
    """
    if not (type(window) in (tuple, list) and len(window) == 2
            and all(host.integer(x) for x in window) and 0 < window[0] < window[1]):
        raise ValueError('invalid_monotonic_window')
    state = host.coverage(stream, 'capture')
    result = dict(schema='raw-fifo2-reconstruction-v1', clock_domain=host.CLOCK,
        scope='capture_only_fixed_historical_dequeue_decisions',
        window_start_ns=window[0], window_end_ns=window[1],
        reconstructed=False, observed_policy='fifo', queue_capacity=2,
        stream=state, rejections={},
        source_pts_identity_only=True, alternative_is_dynamic_replay=False,
        alternative_is_performance_prediction=False, regions_are_not_additive=True,
        native_phone_or_network_latency_measured=False, whole_pipeline_coverage=False)
    bad = Counter()
    if not state['producer_coverage_complete']:
        bad['capture_coverage_not_complete'] += 1
    bounds = state['clock_start_before_ns'], state['clock_end_after_ns']
    if not bounds[0] <= window[0] < window[1] <= bounds[1]:
        bad['window_outside_clock_bracket'] += 1
    if bad:
        result['rejections'] = dict(bad)
        return result

    rows = stream['records']
    enqueues = [x for x in rows if x['event'] == 'capture_enqueue']
    submits = [x for x in rows if x['event'] == 'raw_submit']
    drops = [x for x in rows if x['event'] == 'raw_drop']
    loops = [x for x in rows if x['event'] == 'raw_loop']
    budgets = [x for x in rows if x['event'] == 'raw_budget']
    captures = [x['capture_seq'] for x in enqueues]
    if not enqueues or sorted(captures) != list(range(1, len(captures) + 1)):
        bad['capture_sequence_missing_or_duplicate'] += 1
    removals = [x for x in submits if not x['idle_repeat']] + drops
    removal_counts = Counter(_identity(x) for x in removals)
    loop_counts = Counter(_identity(x) for x in loops
        if x['outcome'] in (1, 3) and x['capture_seq'] > 0)
    if removal_counts != loop_counts or any(n != 1 for n in removal_counts.values()):
        bad['dequeue_identity_not_unique_or_complete'] += 1
    if any(x['outcome'] in (5, 6) or x['skipped'] for x in loops):
        bad['unsupported_or_failed_raw_loop'] += 1
    if any(x['skipped_frames'] or (x['idle_repeat'] != (x['capture_seq'] == 0))
           or not bounds[0] <= x['dequeue_ns'] <= x['pipe_write_begin_ns']
               <= x['pipe_write_end_ns'] <= bounds[1] for x in submits):
        bad['unsupported_or_invalid_submit'] += 1
    loops_by_id = {}
    for row in loops:
        if row['iteration'] in loops_by_id:
            bad['duplicate_loop_iteration'] += 1
        loops_by_id[row['iteration']] = row
    budget_by_id = {}
    for row in budgets:
        if row['iteration'] in budget_by_id:
            bad['duplicate_budget_iteration'] += 1
        budget_by_id[row['iteration']] = row
    if set(loops_by_id) != set(budget_by_id):
        bad['budget_loop_incomplete'] += 1
    decisions = {_identity(x): x for x in loops if x['outcome'] in (1, 3) and x['capture_seq'] > 0}
    if any(decisions.get(_identity(x), {}).get('outcome') != (
            3 if x['event'] == 'raw_drop' else 1) for x in removals):
        bad['dequeue_outcome_conflict'] += 1
    events = [(x['enqueue_ns'], 'enqueue', x) for x in enqueues]
    events += [(x['dequeue_ns'], 'dequeue', x) for x in removals]
    timestamps = [x[0] for x in events]
    if len(set(timestamps)) != len(timestamps):
        # Both timestamps are taken inside the same condition lock, but an
        # equal clock tick does not identify which operation held it first.
        bad['ambiguous_queue_operation_timestamp'] += 1
    if any(not bounds[0] <= x <= bounds[1] for x in timestamps):
        bad['queue_operation_outside_clock_bracket'] += 1
    if bad:
        result['rejections'] = dict(bad)
        return result

    queue = deque()
    depth = Counter()
    values = {key: [] for key in ('observed_age_ms', 'newest_age_at_same_dequeue_ms',
        'different_newest_age_ms', 'different_newest_age_difference_ms',
        'age_budget_wait_overlap_ms', 'age_outside_matched_budget_wait_ms')}
    by_depth = {1: [], 2: []}
    replaced = included = outside = selected_drops = budget_matches = 0
    last_seq = 0
    for at, kind, row in sorted(events, key=lambda x: x[0]):
        if kind == 'enqueue':
            if (row['capture_seq'] != last_seq + 1 or row['pending_count'] not in (1, 2)
                    or not bounds[0] <= row['grpc_return_ns'] <= at
                    or row['width'] == 0 or row['height'] == 0
                    or row['width'] % 2 or row['height'] % 2
                    or row['raw_bytes'] != row['width'] * row['height'] * 4):
                bad['enqueue_identity_or_shape_invalid'] += 1
                break
            last_seq = row['capture_seq']
            expected = queue[0]['capture_seq'] if len(queue) == 2 else 0
            if row['replaced_capture_seq'] != expected:
                bad['replacement_identity_conflict'] += 1
                break
            if expected:
                queue.popleft()
                replaced += 1
            queue.append(row)
            if len(queue) != row['pending_count']:
                bad['pending_depth_conflict'] += 1
                break
            continue
        if not queue:
            bad['dequeue_from_empty_queue'] += 1
            break
        oldest, newest = queue[0], queue[-1]
        if (oldest['capture_seq'], oldest['source_pts_us']) != (
                row['capture_seq'], row['source_pts_us']):
            bad['fifo_selection_identity_conflict'] += 1
            break
        if row['event'] == 'raw_submit' and row['raw_bytes'] != oldest['raw_bytes']:
            bad['dequeue_shape_conflict'] += 1
            break
        if window[0] <= oldest['enqueue_ns'] <= at < window[1]:
            included += 1
            selected_drops += row['event'] == 'raw_drop'
            age = (at - oldest['enqueue_ns']) / 1e6
            newest_age = (at - newest['enqueue_ns']) / 1e6
            depth[len(queue)] += 1
            by_depth[len(queue)].append(age)
            values['observed_age_ms'].append(age)
            values['newest_age_at_same_dequeue_ms'].append(newest_age)
            if oldest['capture_seq'] != newest['capture_seq']:
                values['different_newest_age_ms'].append(newest_age)
                values['different_newest_age_difference_ms'].append(
                    (newest['enqueue_ns'] - oldest['enqueue_ns']) / 1e6)
            loop = decisions[_identity(row)]
            budget = budget_by_id[loop['iteration']]
            if not budget['requested_wait_ns']:
                if budget['wait_begin_ns'] or budget['wait_end_ns']:
                    bad['budget_wait_endpoints_invalid'] += 1
                    break
                values['age_budget_wait_overlap_ms'].append(0.)
                values['age_outside_matched_budget_wait_ms'].append(age)
                budget_matches += 1
            elif (budget['requested_wait_ns'] > 0
                    and bounds[0] <= budget['wait_begin_ns'] <= budget['wait_end_ns'] <= at):
                overlap = max(0, min(at, budget['wait_end_ns'])
                    - max(oldest['enqueue_ns'], budget['wait_begin_ns'])) / 1e6
                values['age_budget_wait_overlap_ms'].append(overlap)
                values['age_outside_matched_budget_wait_ms'].append(age-overlap)
                budget_matches += 1
            else:
                bad['budget_wait_endpoints_invalid'] += 1
                break
        else:
            outside += 1
        queue.popleft()
    if bad:
        result['rejections'] = dict(bad)
        return result  # Fail closed: never return partially reconstructed ages.
    result.update(reconstructed=True, total_enqueues=len(enqueues),
        total_recorded_dequeues=len(removals), total_replaced_enqueues=replaced,
        final_pending_depth=len(queue), window_decisions=included,
        window_dequeues_excluded=outside, window_dropped_before_encode=selected_drops,
        budget_matches=budget_matches,
        pending_at_dequeue={str(k): depth[k] for k in (1, 2)},
        by_pending_age_ms={str(k): distribution(v) for k, v in by_depth.items()},
        distributions_ms={k: distribution(v) for k, v in values.items()},
        decision_local_newest_differs=len(values['different_newest_age_ms']))
    # A changed median of hypothetical ages is not the median improvement.
    # Report per-decision differences (including all unchanged zeros) explicitly.
    diffs = values['different_newest_age_difference_ms'] + [0.] * (
        included-len(values['different_newest_age_difference_ms']))
    result['decision_local_age_difference_ms'] = dict(distribution(diffs),
        mean=round(sum(diffs)/len(diffs), 4) if diffs else None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', required=True)
    parser.add_argument('--window-start-ns', required=True, type=int)
    parser.add_argument('--window-end-ns', required=True, type=int)
    args = parser.parse_args()
    try:
        result = reconstruct(host.read_stream(args.capture, 'capture'),
            (args.window_start_ns, args.window_end_ns))
    except ValueError:
        parser.exit(2, 'invalid_monotonic_window\n')
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['reconstructed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
