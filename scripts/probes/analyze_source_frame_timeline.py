#!/usr/bin/env python3
"""Numeric FrameTimeline supplement, keeping SurfaceView coverage explicit."""
import argparse
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.probes.guest_perfetto_probe import private_trace,parse_integer

SQL = """
SELECT f.ts AS ts_ns,f.dur AS dur_ns,
 CASE WHEN f.layer_name IS NULL AND p.name LIKE '%surfaceflinger%' THEN 1
 WHEN f.layer_name LIKE '%app.morphe.android.youtube%' AND f.layer_name LIKE '%SurfaceView%' THEN 2
 WHEN f.layer_name LIKE '%app.morphe.android.youtube%' THEN 3 ELSE 0 END AS scope_id,
 (CASE WHEN f.jank_type LIKE '%SurfaceFlinger CPU Deadline Missed%' THEN 1 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%SurfaceFlinger GPU Deadline Missed%' THEN 2 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%SurfaceFlinger Scheduling%' THEN 4 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%SurfaceFlinger Stuffing%' THEN 8 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%Display HAL%' THEN 16 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%Prediction Error%' THEN 32 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%Non Animating%' THEN 64 ELSE 0 END)+
 (CASE WHEN f.jank_type LIKE '%App Deadline Missed%' THEN 128 ELSE 0 END) AS jank_mask,
 CASE WHEN f.jank_type='None' THEN 1 ELSE 0 END AS none_jank,
 CASE WHEN f.present_type='On-time Present' THEN 1 WHEN f.present_type='Late Present' THEN 2
 WHEN f.present_type='Early Present' THEN 3 ELSE 0 END AS present_kind_id,
 f.on_time_finish,f.gpu_composition,f.display_frame_token
 FROM actual_frame_timeline_slice f JOIN process p USING(upid)
 WHERE (f.layer_name IS NULL AND p.name LIKE '%surfaceflinger%')
 OR f.layer_name LIKE '%app.morphe.android.youtube%'
 ORDER BY f.ts;
"""
LEGEND = {1:'sf_cpu_deadline',2:'sf_gpu_deadline',4:'sf_scheduling',8:'sf_stuffing',
          16:'display_hal',32:'prediction_error',64:'non_animating',128:'app_deadline'}


def analyze(raw):
    rows=list(csv.DictReader(io.StringIO(raw)))
    if len(rows)>20000:
        raise ValueError('bounded_timeline_rows_required')
    rows=[{k:parse_integer(v) for k,v in r.items()} for r in rows]
    summaries={}
    for scope,label in [(1,'sf_display'),(2,'youtube_video_surfaceview'),(3,'youtube_other_surface')]:
        selected=[r for r in rows if r['scope_id']==scope]
        stamps=sorted(set(r['ts_ns'] for r in selected))
        summaries[label]={'frames':len(selected),'unique_start_times':len(stamps),
            'start_gap_max_ms':max([(b-a)/1e6 for a,b in zip(stamps,stamps[1:])],default=None),
            'none_jank':sum(r['none_jank']==1 for r in selected),
            'jank_flags_counts':{name:sum(bool(r['jank_mask']&mask) for r in selected) for mask,name in LEGEND.items()},
            'present_counts':{str(k):sum(r['present_kind_id']==k for r in selected) for k in range(4)}}
    return {'schema':1,'scope':'SF display scheduling and supported app timeline; not optical video FPS',
            'video_surfaceview_covered':summaries['youtube_video_surfaceview']['frames']>0,
            'summary':summaries,'numeric_frame_records':rows,
            'jank_flags':{str(k):v for k,v in LEGEND.items()},
            'present_kind_legend':{'0':'unspecified','1':'on_time','2':'late','3':'early'},
            'limitations':['No video SurfaceView rows means unsupported/unobserved coverage, not zero video jank',
                'Global SF jank classifications cannot uniquely attribute a source video gap',
                'FrameTimeline starts/durations are scheduling records, not panel photons',
                'Prediction errors and non-animating tags are distinct from a GPU deadline miss']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace',type=Path,required=True);p.add_argument('--processor',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():p.error('fresh numeric evidence required')
    trace=private_trace(args.trace)
    result=subprocess.run([str(args.processor),'query',str(trace),SQL],
                          capture_output=True,text=True,timeout=30)
    if result.returncode or len(result.stdout.encode())>8*1024*1024:
        raise SystemExit('bounded_frame_timeline_query_failed')
    report=analyze(result.stdout)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:json.dump(report,out,indent=2);out.write('\n')
    print(json.dumps(report['summary']))


if __name__=='__main__':main()
