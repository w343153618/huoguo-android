"""Bounded offline correlation of RX samples and later worker/callback stalls.

No device, credential, network, clock subtraction across machines or live observer.
The caller must separately verify that both inputs belong to the same App attempt
and phone System.nanoTime domain; valid JSON does not establish that ownership.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


COLUMNS = ('udp_packets', 'authenticated_packets', 'authenticated_video_packets',
           'received_media_frames', 'codec_callback_count', 'FEC_packets',
           'FEC_frames_expired', 'FEC_reference_lost', 'FEC_dependency_dropped',
           'FEC_keyframe_requests')
MAX_JSON_BYTES = 65536
MAX_LONG = (1 << 63) - 1


def _integer(value, low=0, high=MAX_LONG):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('integral_bound')
    return value


def _rx(value):
    if type(value) is not dict or _integer(value.get('schema_version'), 1, 1) != 1:
        raise ValueError('transport_schema')
    available = _integer(value.get('available'), 0, 1)
    if available == 0:
        return None
    n = _integer(value.get('exported_count'), 0, 64)
    recorded = _integer(value.get('recorded_count'), n, 3600)
    if (_integer(value.get('capacity'), 64, 64) != 64
            or _integer(value.get('omitted_prefix_following_count')) != recorded - n
            or _integer(value.get('all_recorded_samples_exported'), 0, 1) != int(n == recorded)
            or _integer(value.get('all_session_observations_covered'), 0, 0) != 0
            or _integer(value.get('timestamp_is_exact_packet_arrival'), 0, 0) != 0):
        raise ValueError('transport_coverage')
    rows = []
    for name in ('t_ns', *COLUMNS):
        column = value.get(name)
        if type(column) is not list or len(column) != n:
            raise ValueError('transport_column_length')
        for i, x in enumerate(column):
            _integer(x, 1 if name == 't_ns' else 0)
            if i and (x <= column[i - 1] if name == 't_ns' else x < column[i - 1]):
                raise ValueError('transport_column_order')
    for i in range(n):
        row = {name: value[name][i] for name in ('t_ns', *COLUMNS)}
        if not row['authenticated_video_packets'] <= row['authenticated_packets'] <= row['udp_packets']:
            raise ValueError('transport_auth_subset')
        rows.append(row)
    return rows


def _worker(value):
    if type(value) is not list or len(value) > 48:
        raise ValueError('worker_rows_bound')
    rows = []
    for row in value:
        if type(row) is not dict:
            raise ValueError('worker_row_schema')
        r = {key: _integer(row.get(key), 1 if key == 'phone_ns' else 0)
             for key in ('phone_ns', 'worker_received_frames', 'codec_callback_count')}
        if rows and (r['phone_ns'] <= rows[-1]['phone_ns']
                     or r['worker_received_frames'] < rows[-1]['worker_received_frames']
                     or r['codec_callback_count'] < rows[-1]['codec_callback_count']):
            raise ValueError('worker_row_order')
        rows.append(r)
    return rows


def analyze(transport, worker_rows, *, same_attempt_phone_clock_verified=False):
    """Interior sample deltas only; no interpolation into missing/tail observations.

    t_ns is the RX clock after socket/primary body processing, before expire,
    audio/statistics and the final sample counters. It is not an atomic counter
    timestamp or packet arrival. Classifications are observations, not root causes.
    """
    if type(same_attempt_phone_clock_verified) is not bool:
        raise ValueError('clock_provenance_flag')
    rx, worker = _rx(transport), _worker(worker_rows)
    result = {'schema': 'phone-transport-progress-analysis-v1',
              'same_attempt_phone_clock_verified_by_caller': same_attempt_phone_clock_verified,
              'RX_is_before_Inbox': True, 'worker_is_after_Inbox_before_codec_input': True,
              'timestamp_is_exact_packet_arrival': False,
              'timestamp_counter_snapshot_delay_bound_known': False,
              'whole_session_or_physical_network_loss_verified': False,
              'codec_or_Surface_root_cause_proven': False,
              'retained_RX_rows': 0 if rx is None else len(rx),
              'retained_worker_rows': len(worker), 'plateaus': []}
    if not same_attempt_phone_clock_verified:
        result['correlation_status'] = 'provenance_unverified'
        return result
    if rx is None:
        result['correlation_status'] = 'legacy_RX_unavailable'
        return result
    result['correlation_status'] = 'retained_prefix_only'
    i = 0
    while i < len(worker):
        j = i
        while (j + 1 < len(worker)
               and worker[j + 1]['worker_received_frames'] == worker[i]['worker_received_frames']
               and worker[j + 1]['codec_callback_count'] == worker[i]['codec_callback_count']):
            j += 1
        start, end = worker[i]['phone_ns'], worker[j]['phone_ns']
        if end - start >= 3_000_000_000:
            interior = [r for r in rx if start < r['t_ns'] < end]
            out = {'worker_start_ns': start, 'worker_end_ns': end,
                   'worker_observed_plateau_ns': end - start,
                   'interior_RX_rows': len(interior), 'classification': 'RX_coverage_insufficient'}
            if len(interior) >= 2:
                first, last = interior[0], interior[-1]
                delta = {name: last[name] - first[name] for name in COLUMNS}
                gap = max(b['t_ns'] - a['t_ns'] for a, b in zip(interior, interior[1:]))
                out.update(RX_interior_start_ns=first['t_ns'], RX_interior_end_ns=last['t_ns'],
                           RX_interior_span_ns=last['t_ns'] - first['t_ns'],
                           RX_max_observed_sample_gap_ns=gap, deltas=delta)
                if gap > 2_000_000_000:
                    out['classification'] = 'RX_sample_timeline_gap_unknown'
                elif delta['received_media_frames'] > 0:
                    out['classification'] = 'RX_completion_continues_while_later_worker_is_flat'
                elif delta['authenticated_video_packets'] > 0:
                    out['classification'] = 'authenticated_video_continues_RX_completion_flat'
                elif delta['udp_packets'] > 0:
                    out['classification'] = 'nonvideo_or_unqualified_datagrams_continue'
                else:
                    out['classification'] = 'RX_sampled_datagram_count_flat_not_physical_loss_proof'
            result['plateaus'].append(out)
        i = j + 1
    return result


def _load(path):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate_JSON_key')
            out[key] = value
        return out
    with path.open('rb') as f:
        raw = f.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('JSON_bytes_bound')
    def constant(_):
        raise ValueError('nonfinite_JSON')
    return json.loads(raw.decode('utf8'), object_pairs_hook=pairs, parse_constant=constant)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app-report', required=True, type=Path)
    p.add_argument('--ui-report', required=True, type=Path)
    p.add_argument('--same-attempt-phone-clock-verified', action='store_true',
                   help='Explicit caller provenance, never inferred from valid JSON')
    args = p.parse_args(argv)
    app, ui = _load(args.app_report), _load(args.ui_report)
    helper = ui.get('ui_result', ui)
    print(json.dumps(analyze(app['transport_samples_numeric'], helper['steady_progress_samples'],
                            same_attempt_phone_clock_verified=args.same_attempt_phone_clock_verified)))


if __name__ == '__main__':
    main()
