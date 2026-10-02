#!/usr/bin/env python3
"""Offline numeric-only SF fence/capture comparison. Never accesses a device.

An inferred constant guest-to-host offset is explicitly a model, not an exact
clock conversion. Same-guest intervals need no such conversion. Source windows
start at each fence probe, while capture windows start at first gRPC return.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from scripts.probes.analyze_capture_steady_window import PHASES, distribution


def read(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Input exceeds metadata bound')
    return json.loads(path.read_text())


def evidence(path):
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def clock_model(data):
    samples = [q for q in data.get('guest_clock_queries', []) if q.get('available')
               and len(q.get('guest_to_host_monotonic_offset_bounds_ns', [])) == 2]
    if not samples:
        raise ValueError('No guest/host clock exchange bounds')
    lower = max(q['guest_to_host_monotonic_offset_bounds_ns'][0] for q in samples)
    upper = min(q['guest_to_host_monotonic_offset_bounds_ns'][1] for q in samples)
    if lower > upper:
        raise ValueError('No common constant-offset model fits the observed samples')
    return {'sample_count': len(samples), 'constant_offset_intersection_ns': [lower, upper],
            'intersection_width_ms': (upper-lower)/1e6, 'exact': False,
            'assumption': 'Guest/host MONOTONIC offset is constant across this unsuspended probe; exchanges alone do not prove absence of drift.',
            'bounds_include': 'Entire host query round trip and guest sample precision; no path-symmetry assumption.'}


def local_offset_hull(data, guest_ns):
    queries = sorted((q for q in data['guest_clock_queries'] if q.get('available')),
                     key=lambda q: q['guest_monotonic_us'])
    before = [q for q in queries if q['guest_monotonic_us']*1000 <= guest_ns]
    after = [q for q in queries if q['guest_monotonic_us']*1000 >= guest_ns]
    selected = [before[-1] if before else queries[0], after[0] if after else queries[-1]]
    return [min(q['guest_to_host_monotonic_offset_bounds_ns'][0] for q in selected),
            max(q['guest_to_host_monotonic_offset_bounds_ns'][1] for q in selected)]


def conflict(row):
    return bool(row.get('desired_conflict') or row.get('ready_conflict'))


def same_guest_pair(left, right):
    def valid(row,name):
        return row.get(name+'_valid') is True
    triplets_valid = all(valid(r,'desired_present') and valid(r,'frame_ready') for r in (left,right))
    comparable = triplets_valid and not conflict(left) and not conflict(right)
    result = {'left_actual_guest_monotonic_ns': left['actual_present_ns'],
              'right_actual_guest_monotonic_ns': right['actual_present_ns'],
              'actual_gap_ms': (right['actual_present_ns']-left['actual_present_ns'])/1e6,
              'left_identity_conflict': conflict(left), 'right_identity_conflict': conflict(right),
              'both_triplets_valid':triplets_valid,
              'per_frame_identity_comparable': comparable,
              'desired_gap_ms': (right['desired_present_ns']-left['desired_present_ns'])/1e6 if valid(left,'desired_present') and valid(right,'desired_present') else None,
              'right_actual_minus_desired_ms': (right['actual_present_ns']-right['desired_present_ns'])/1e6 if valid(right,'desired_present') else None,
              'right_desired_minus_ready_ms': (right['desired_present_ns']-right['frame_ready_ns'])/1e6 if valid(right,'desired_present') and valid(right,'frame_ready') else None,
              'right_actual_minus_ready_ms': (right['actual_present_ns']-right['frame_ready_ns'])/1e6 if valid(right,'frame_ready') else None,
              'ready_gap_ms': (right['frame_ready_ns']-left['frame_ready_ns'])/1e6 if valid(left,'frame_ready') and valid(right,'frame_ready') else None}
    # Retain conflicting numeric values for auditing, but disallow treating them
    # as a trustworthy latency or decoded-buffer identity.
    if not comparable:
        result['triplet_latency_interpretation_allowed'] = False
    else:
        result['triplet_latency_interpretation_allowed'] = True
    return result


def summarize_fences(path):
    data = read(path)
    rows = sorted(data['frame_records'], key=lambda r: r['actual_present_ns'])
    if len(rows) > 15000 or len(data.get('polls', [])) > 1000:
        raise ValueError('Input record limit exceeded')
    model = clock_model(data)
    lo, hi = model['constant_offset_intersection_ns']
    midpoint = (lo+hi)//2
    origin = data['measurement_host_before_ns']
    start, end = origin + 5_000_000_000, origin + 30_000_000_000
    selected = [r for r in rows if start <= r['actual_present_ns']+midpoint < end]
    definitely = [r for r in rows if start <= r['actual_present_ns']+lo and r['actual_present_ns']+hi < end]
    possibly = [r for r in rows if start <= r['actual_present_ns']+hi and r['actual_present_ns']+lo < end]
    pairs = list(zip(rows, rows[1:]))
    gaps = [same_guest_pair(a, b) for a, b in pairs]
    for gap in gaps:
        a, b = gap['left_actual_guest_monotonic_ns'], gap['right_actual_guest_monotonic_ns']
        gap['left_host_s_from_fence_probe_start_model'] = (a+midpoint-origin)/1e9
        gap['right_host_s_from_fence_probe_start_model'] = (b+midpoint-origin)/1e9
        gap['right_local_query_offset_hull_ns'] = local_offset_hull(data, b)
        gap['both_endpoints_in_fixed_window_model'] = start <= a+midpoint and b+midpoint < end
    comparable = [r for r in selected if not conflict(r) and r.get('frame_ready_valid') and r.get('desired_present_valid')]
    steady_pairs = list(zip(selected, selected[1:]))
    return {'input': evidence(path), 'status': data['status'],
            'measurement_seconds': data['measurement_seconds'],
            'fixed_layer_generation_verified': data.get('complete_fixed_generation_verified'),
            'layer_identity_sha256': data['layer_identity']['sha256'],
            'display_vsync_ns_values': sorted({p['display_vsync_ns'] for p in data['polls']}),
            'clock_model': model,
            'fixed_window': {'origin': 'fence probe measurement_host_before_ns', 'start_s': 5, 'end_s': 30,
                             'selected_actual_frames_model': len(selected), 'actual_fps_model': len(selected)/25,
                             'frames_definitely_inside_given_constant_model': len(definitely),
                             'frames_possibly_inside_given_constant_model': len(possibly),
                             'identity_conflicts': sum(conflict(r) for r in selected),
                             'actual_gap_ms': distribution([(b['actual_present_ns']-a['actual_present_ns'])/1e6 for a,b in steady_pairs]),
                             'ready_to_actual_comparable_ms': distribution([(r['actual_present_ns']-r['frame_ready_ns'])/1e6 for r in comparable]),
                             'desired_to_actual_comparable_ms': distribution([(r['actual_present_ns']-r['desired_present_ns'])/1e6 for r in comparable])},
            'whole_probe': {'actual_unique': len(rows), 'actual_gap_ms': distribution([g['actual_gap_ms'] for g in gaps]),
                            'identity_conflicts': sum(conflict(r) for r in rows)},
            'largest_actual_gaps': sorted(gaps,key=lambda g:g['actual_gap_ms'],reverse=True)[:16]}, data


def summarize_capture(path, fence_data):
    trace = read(path)
    records = trace['records']
    clocks = {r.get('process') for r in records if r.get('event')=='trace_clock'
              and r.get('clock_domain')=='host_clock_gettime_CLOCK_MONOTONIC_ns'}
    if not {'python','swift'} <= clocks:
        raise ValueError('Both capture processes must explicitly use the same host clock')
    captures = sorted((r for r in records if r['event']=='capture_enqueue'), key=lambda r:r['grpc_return_ns'])
    origin = captures[0]['grpc_return_ns']
    events = defaultdict(lambda: defaultdict(list))
    for row in records:
        if type(row.get('source_pts_us')) is int and row['event']!='capture_enqueue':
            events[row['source_pts_us']][row['event']].append(row)
    frames = []
    for capture in captures:
        group = events[capture['source_pts_us']]
        submissions = [r for r in group['raw_submit'] if r.get('capture_seq')==capture['capture_seq'] and not r.get('idle_repeat')]
        if len(submissions)!=1:
            continue
        merged = dict(capture)
        for name in ('raw_submit','raw_read','vt_frame','vt_submit_return','encoded_egress'):
            rows = submissions if name=='raw_submit' else group[name]
            if len(rows)==1:
                merged.update(rows[0])
        phases = {name:(merged[b]-merged[a])/1e6 for name,(a,b) in PHASES.items()
                  if type(merged.get(a)) is int and type(merged.get(b)) is int and merged[b]>=merged[a]}
        frames.append({'capture_seq':capture['capture_seq'], 'screenshot_seq':capture.get('screenshot_seq'),
                       'source_pts_us':capture['source_pts_us'],
                       'seconds_from_first_grpc_return':(capture['grpc_return_ns']-origin)/1e9,'stage_ms':phases})
    stage_windows = {}
    for label,lower,upper in [('startup_0_5s',0,5),('steady_5_30s',5,30),('later_30s_end',30,float('inf'))]:
        selected = [f for f in frames if lower<=f['seconds_from_first_grpc_return']<upper]
        stage_windows[label] = {'joined_frames':len(selected), 'stage_ms':{name:distribution([f['stage_ms'][name] for f in selected if name in f['stage_ms']]) for name in PHASES},
                                'slowest_grpc_to_socket_frames':sorted(selected,key=lambda f:f['stage_ms'].get('grpc_return_to_encoded_socket_end_ms',0),reverse=True)[:3]}
    model = clock_model(fence_data)
    lo,hi = model['constant_offset_intersection_ns']; mid=(lo+hi)//2
    sfrows = sorted(fence_data['frame_records'],key=lambda r:r['actual_present_ns'])
    sfgaps = [(a,b) for a,b in zip(sfrows,sfrows[1:]) if b['actual_present_ns']-a['actual_present_ns']>50_000_000]
    gaps = []
    for left,right in zip(captures,captures[1:]):
        span = right['grpc_return_ns']-left['grpc_return_ns']
        if span<=50_000_000:
            continue
        seq_delta = ((right['screenshot_seq']-left['screenshot_seq']) & 0xffffffff) if type(left.get('screenshot_seq')) is int and type(right.get('screenshot_seq')) is int else None
        overlaps = []
        for a,b in sfgaps:
            if a['actual_present_ns']+lo<=right['grpc_return_ns'] and b['actual_present_ns']+hi>=left['grpc_return_ns']:
                pair = same_guest_pair(a,b)
                pair['sf_left_minus_grpc_left_ms_model'] = (a['actual_present_ns']+mid-left['grpc_return_ns'])/1e6
                pair['sf_right_minus_grpc_right_ms_model'] = (b['actual_present_ns']+mid-right['grpc_return_ns'])/1e6
                pair['right_local_query_offset_hull_ns'] = local_offset_hull(fence_data,b['actual_present_ns'])
                overlaps.append(pair)
        gaps.append({'left_capture_seq':left['capture_seq'],'right_capture_seq':right['capture_seq'],
                     'screenshot_sequence_delta':seq_delta,'grpc_gap_ms':span/1e6,
                     'screenshot_pts_gap_ms':(right['source_pts_us']-left['source_pts_us'])/1000,
                     'left_s_from_first_grpc_return':(left['grpc_return_ns']-origin)/1e9,
                     'right_s_from_first_grpc_return':(right['grpc_return_ns']-origin)/1e9,
                     'sf_actual_gap_time_candidates_not_frame_identity':overlaps})
    return {'input':evidence(path),'origin':'first gRPC return; distinct from fence probe start',
            'common_host_clock_verified':True,
            'trace_status':trace['status'],'stage_windows':stage_windows,
            'largest_grpc_gaps':sorted(gaps,key=lambda g:g['grpc_gap_ms'],reverse=True)[:16]}


def summarize_perfetto(path, fences, data):
    analysis=read(path)['analysis']
    snapshots=analysis.get('clock_snapshots',[])
    offsets=[s['clock_value_ns']-s['clock_ts_ns'] for s in snapshots if s.get('clock_id')==3]
    result={'input':evidence(path),'guest_monotonic_clock_id':3,
            'trace_clock_id_6_matches_ts':all(s['clock_value_ns']==s['clock_ts_ns'] for s in snapshots if s.get('clock_id')==6),
            'guest_monotonic_minus_trace_ts_ns_range':[min(offsets),max(offsets)] if offsets else None,
            'codec_stage_rows':len(analysis.get('codec_stages',[])),
            'decoder_starvation_proven':False,
            'event_counts_are_not_decoded_or_displayed_frame_counts':True,
            'render_to_sf_media_pts_identity_available':False,
            'codec_stage_legend':analysis.get('codec_stage_legend'),
            'codec_component_legend':analysis.get('codec_component_legend')}
    if not offsets:
        return result
    delta_lo,delta_hi=min(offsets),max(offsets)
    delta_mid=(delta_lo+delta_hi)//2
    codec=analysis.get('codec_stages',[])
    counts=[]
    for gap in fences['largest_actual_gaps']:
        if gap['actual_gap_ms']<=100:
            continue
        lo,hi=gap['left_actual_guest_monotonic_ns'],gap['right_actual_guest_monotonic_ns']
        groups=[]
        for component,instance in sorted({(r['component_id'],r['instance_id']) for r in codec}):
            rows=[r for r in codec if r['component_id']==component and r['instance_id']==instance]
            stage_counts=[]
            for stage in (1,2,3):
                rr=[r for r in rows if r['stage_id']==stage]
                starts=[r for r in rr if lo<=r['ts_ns']+delta_mid<hi]
                ends=[r for r in rr if r['dur_ns']>=0 and lo<=r['ts_ns']+r['dur_ns']+delta_mid<hi]
                definite=sum(lo<=r['ts_ns']+delta_lo and r['ts_ns']+delta_hi<hi for r in rr)
                possible=sum(lo<=r['ts_ns']+delta_hi and r['ts_ns']+delta_lo<hi for r in rr)
                stage_counts.append({'stage_id':stage,'entry_count_model':len(starts),
                                     'entry_count_min_given_snapshot_delta_range':definite,
                                     'entry_count_max_given_snapshot_delta_range':possible,
                                     'exit_count_model':len(ends),
                                     'max_call_duration_ms':max([r['dur_ns']/1e6 for r in starts if r['dur_ns']>=0],default=None),
                                     'entry_ms_from_sf_gap_left':[round((r['ts_ns']+delta_mid-lo)/1e6,6) for r in starts][:32]})
            if any(item['entry_count_model'] for item in stage_counts):
                groups.append({'component_id':component,'instance_id':instance,'stages':stage_counts})
        states=[r for r in analysis.get('thread_states',[]) if r['state_kind_id'] in (1,2,4)
                and r['dur_ns']>=0 and r['ts_ns']+delta_mid<hi and r['ts_ns']+r['dur_ns']+delta_mid>lo]
        state_records=[]
        for state in sorted(states,key=lambda r:r['dur_ns'],reverse=True)[:6]:
            state_records.append({k:state[k] for k in ('role_id','state_kind_id','pid','tid','dur_ns')})
            state_records[-1]['overlap_ms']=max(0,min(hi,state['ts_ns']+state['dur_ns']+delta_mid)-max(lo,state['ts_ns']+delta_mid))/1e6
        counts.append({'sf_gap':gap,'trace_window_ns_model':[lo-delta_mid,hi-delta_mid],
                       'codec_call_events_during_sf_gap':groups,
                       'largest_exported_running_runnable_uninterruptible_intervals':state_records})
    result['sf_gaps_over_100_ms_codec_time_association']=counts
    result['thread_state_role_legend']=analysis.get('thread_state_role_legend')
    result['state_kind_legend']=analysis.get('state_kind_legend')
    return result


def summarize_sample(directory):
    fences,data = summarize_fences(directory/'source-fences.json')
    result = {'sample':directory.name, 'fences':fences}
    manifest = directory/'manifest.json'
    if manifest.is_file():
        m = read(manifest)
        result['manifest'] = {'input':evidence(manifest),'condition':m.get('condition'),
                              'requested_fps':m.get('comparison_requested_fps'),
                              'source_media_fps_known':m.get('source_media_fps_known'),
                              'probe_source_sha256':m.get('source_sha256'),'process_exit':m.get('results')}
    captures = list(directory.glob('*-capture-trace.json'))
    if captures:
        result['capture'] = summarize_capture(captures[0],data)
    perfetto = directory/'perfetto-analysis-complete.json'
    if not perfetto.is_file():
        perfetto = directory/'perfetto-analysis.json'
    if perfetto.is_file():
        result['perfetto_clock_bridge']=summarize_perfetto(perfetto,fences,data)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    samples=[summarize_sample(p.parent) for p in sorted(args.input_dir.glob('*/source-fences.json'))]
    report={'schema':1,'scope':'Offline numeric SF/capture clock-aware diagnostic; no device or service operations',
            'samples':samples,'limitations':[
                'Clock conversions are conditional constant-offset models; exchange bounds do not prove no drift.',
                'SF desired/actual/ready are scheduling/fence times, not guest decoded media PTS.',
                'Conflicting triplets cannot identify one buffer and are excluded from comparable per-frame timing.',
                'SF/capture time overlap does not prove the same frame identity.',
                'Whole-probe and [5,30) windows are separate; capture and fence windows have different origins.',
                'Unknown source media FPS, sequential video segments and trace observer overhead preclude a controlled performance improvement claim.',
                'No codec slice rows is missing instrumentation evidence, not proof that no decoder ran.',
                'Missing native final summary is an incomplete metadata prefix, never proof of zero dropped trace records.',
                'Pipe writes/native reads overlap and VT timing includes scheduling; do not add them as pure hardware costs.'
            ]}
    with args.output.open('x') as handle:
        json.dump(report,handle,indent=2,allow_nan=False);handle.write('\n')
    print(json.dumps({'output':str(args.output),'samples':len(samples)}))


if __name__=='__main__':
    main()
