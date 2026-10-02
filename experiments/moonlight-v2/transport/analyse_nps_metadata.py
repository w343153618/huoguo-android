#!/usr/bin/env python3
"""Analyse previously collected, safe socket metadata; no SSH or live effects."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=json.loads(args.input.read_text())
    groups={}
    for sample in source['samples']:
        for sock in sample['tcp_sockets']:
            groups.setdefault((sock['local'],sock['peer']),[]).append((sample['elapsed_seconds'],sock))
    result={'validation_layer':'offline_analysis_of_readonly_TCP_metadata',
            'input_evidence':str(args.input),'created_at_from_input':source.get('created_at'),
            'duration_seconds':source['duration_seconds'],'system_cpu_percent':source['system_cpu_percent'],
            'top_processes':source['top_processes'][:3],'connections':[],
            'limitations':['TLS channel roles inferred from byte volume, not packet payload inspection',
                          'One-second samples cannot prove the absence of short HOL or zero-window events',
                          'cloud8024 retransmissions describe cloud-to-NPC reverse traffic, not M1-to-cloud media loss',
                          'Send-Q includes unacknowledged bytes, not necessarily unsent application data',
                          'No source FPS or phone presentation claim from socket metadata']}
    for (local,peer),samples in groups.items():
        if len(samples)<2: continue
        first,last=samples[0][1],samples[-1][1]
        period=samples[-1][0]-samples[0][0]
        def counter_delta(key):
            return int(last['tcp'].get(key,0))-int(first['tcp'].get(key,0))
        sent=counter_delta('bytes_sent'); retr=counter_delta('bytes_retrans')
        stats={'local_port':int(local.rsplit(':',1)[1]),'peer_port':int(peer.rsplit(':',1)[1]),
               'sample_count':len(samples),'counter_delta_seconds':period,
               'bytes_sent_delta':sent,'bytes_received_delta':counter_delta('bytes_received'),
               'bytes_retrans_delta':retr,'retrans_bytes_over_sent_delta_percent':round(retr/sent*100,5) if sent else None,
               'send_q_bytes_max':max(v['send_q_bytes'] for _,v in samples),
               'recv_q_bytes_max':max(v['recv_q_bytes'] for _,v in samples)}
        for key in ('snd_wnd','rcv_wnd','cwnd','rcv_ooopack'):
            values=[int(v['tcp'][key]) for _,v in samples if key in v['tcp']]
            if values: stats[key+'_range']=[min(values),max(values)]
        for key in ('rtt','rto'):
            values=[float(v['tcp'][key].split('/')[0]) for _,v in samples if key in v['tcp']]
            if values: stats[key+'_ms_range']=[min(values),max(values)]
        totals=[int(v['tcp']['retrans'].split('/')[1]) for _,v in samples if '/' in v['tcp'].get('retrans','')]
        if len(totals)>=2: stats['retrans_segments_delta']=totals[-1]-totals[0]
        result['connections'].append(stats)
    public=[v for v in result['connections'] if v['local_port']==15556]
    bridges=[v for v in result['connections'] if v['local_port']==8024]
    if public: max(public,key=lambda v:v['bytes_sent_delta'])['inferred_role']='public_video_candidate_by_sent_bytes'
    if bridges: max(bridges,key=lambda v:v['bytes_received_delta'])['inferred_role']='media_NPC_mux_candidate_by_received_bytes'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
