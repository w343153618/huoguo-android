#!/usr/bin/env python3
"""Read-only NPS mapping, filtered TCP, CPU and NIC metadata; never print keys."""
import argparse
import csv
import datetime
import io
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent


def local_process_counters():
    result={}
    # ucomm is a basename, rather than a truncated full path; no argv is read.
    completed=subprocess.run(['ps','-axo','pid=,ucomm=,time='],capture_output=True,text=True,check=True)
    for row in completed.stdout.splitlines():
        fields=row.split()
        if len(fields)<3 or not fields[0].isdigit():
            continue
        seconds=0.0
        try:
            for component in fields[-1].split(':'):
                seconds=seconds*60+float(component)
        except ValueError:
            continue
        result[int(fields[0])]={'comm':' '.join(fields[1:-1]),'cpu_seconds':seconds}
    return result

# Executed through stdin. No remote files, services, limits or routes are changed.
REMOTE = r'''
import ipaddress,json,os,pathlib,re,subprocess,time
conf=pathlib.Path('/etc/nps/conf')
def safe_fields(obj):
    allowed={'Id','Port','RateLimit','MaxConn','NowConn','MaxTunnelNum','IsConnect','Status','RunStatus','Crypt','Compress','ConfigConnAllow','NoStore'}
    result={k:v for k,v in obj.items() if k in allowed and isinstance(v,(int,float,bool))}
    for key in ('Mode','TargetType','Version'):
        value=obj.get(key)
        if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_.+-]{1,40}',value): result[key]=value
    return result
def endpoint(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_.:/,\[\]-]{1,256}',value) else 'not_plain_endpoint'
def mapping():
    tasks=json.loads((conf/'tasks.json').read_text())
    clients=json.loads((conf/'clients.json').read_text())
    selected=[]
    for task in tasks:
        if str(task.get('Port'))!='15556': continue
        row={'task':safe_fields(task),'source':'persisted_config_not_runtime_connection_state'}
        target=task.get('Target',{})
        if isinstance(target,dict): row['target']={k:endpoint(v) for k,v in target.items() if k=='TargetStr'}
        client_ref=task.get('Client',{})
        cid=client_ref.get('Id') if isinstance(client_ref,dict) else client_ref
        for client in clients:
            if client.get('Id')!=cid: continue
            row['client']=safe_fields(client)
            if isinstance(client.get('Cnf'),dict): row['client_cnf']=safe_fields(client['Cnf'])
            rate=client.get('Rate',{})
            if isinstance(rate,dict): row['client_rate']={k:v for k,v in rate.items() if k in ('NowRate','Limit') and isinstance(v,(int,float,bool))}
        selected.append(row)
    return selected
clock=os.sysconf('SC_CLK_TCK')
def processes():
    result={}
    for path in pathlib.Path('/proc').iterdir():
        if not path.name.isdecimal(): continue
        try:
            fields=(path/'stat').read_text().split(')',1)[1].split()
            result[int(path.name)]={'cpu_seconds':(int(fields[11])+int(fields[12]))/clock,'comm':(path/'comm').read_text().strip()}
        except (OSError,ValueError): continue
    return result
def cpu(): return list(map(int,pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
def net():
    result={}
    for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]:
        name,values=line.split(':',1); parts=values.split(); name=name.strip()
        if name=='lo': continue
        result[name]={'rx_bytes':int(parts[0]),'tx_bytes':int(parts[8]),'rx_drop':int(parts[3]),'tx_drop':int(parts[11])}
    return result
source=str(ipaddress.ip_address(os.environ['SSH_CONNECTION'].split()[0]))
socket_filter='( sport = :15556 or ( sport = :8024 and dst '+source+' ) )'
def sockets():
    completed=subprocess.run(['ss','-tinp',socket_filter],capture_output=True,text=True,check=True)
    result=[]; current=None
    allowed={'rto','rtt','mss','cwnd','ssthresh','bytes_sent','bytes_retrans','bytes_acked','bytes_received','unacked','notsent','retrans','reordering','reord_seen','rcv_rtt','rcv_space','rcv_ssthresh','minrtt','rcv_ooopack','snd_wnd','rcv_wnd','pacing_rate','delivery_rate'}
    for line in completed.stdout.splitlines()[1:]:
        parts=line.split()
        if not parts: continue
        if not line[0].isspace():
            if len(parts)<5: continue
            current={'state':parts[0],'recv_q_bytes':int(parts[1]),'send_q_bytes':int(parts[2]),'local':parts[3],'peer':parts[4],'tcp':{}}
            result.append(current)
        elif current is not None:
            if parts[0] in ('bbr','cubic','reno'): current['tcp']['congestion_control']=parts[0]
            for token in parts:
                if ':' not in token: continue
                key,value=token.split(':',1)
                if key in allowed and re.fullmatch(r'[a-zA-Z0-9./+-]{1,50}',value): current['tcp'][key]=value
                if key=='delivery_rate' and not value: pass
            for key in ('pacing_rate','delivery_rate'):
                if key in parts:
                    idx=parts.index(key)
                    if idx+1<len(parts) and re.fullmatch(r'[a-zA-Z0-9./+-]{1,50}',parts[idx+1]): current['tcp'][key]=parts[idx+1]
    return result
duration=DURATION
initial_processes=processes(); initial_cpu=cpu(); prior_net=net(); start=time.monotonic(); prior_time=start; start_unix_ms=int(time.time()*1000)
samples=[]
for index in range(duration):
    time.sleep(max(0,start+index+1-time.monotonic()))
    now=time.monotonic(); current_net=net(); elapsed=now-prior_time
    rates={}
    for name,item in current_net.items():
        if name not in prior_net: continue
        before=prior_net[name]
        rates[name]={'rx_mbps':round((item['rx_bytes']-before['rx_bytes'])*8/elapsed/1e6,3),'tx_mbps':round((item['tx_bytes']-before['tx_bytes'])*8/elapsed/1e6,3),'rx_drop_delta':item['rx_drop']-before['rx_drop'],'tx_drop_delta':item['tx_drop']-before['tx_drop']}
    samples.append({'elapsed_seconds':round(now-start,3),'observed_unix_ms':int(time.time()*1000),'interface_rates':rates,'tcp_sockets':sockets()})
    prior_net=current_net; prior_time=now
finish=time.monotonic(); ending_processes=processes(); ending_cpu=cpu(); cpu_delta=[v-u for u,v in zip(initial_cpu,ending_cpu)]; total=sum(cpu_delta)
top=[]
for pid,item in ending_processes.items():
    if pid in initial_processes: top.append({'pid':pid,'comm':item['comm'],'cpu_percent_one_core':round((item['cpu_seconds']-initial_processes[pid]['cpu_seconds'])/(finish-start)*100,2)})
result={'validation_layer':'read_only_cloud_runtime_metadata_no_stream_initiation','server':'146.56.249.175','m1_ssh_source_ip_for_8024_filter':source,'duration_seconds':round(finish-start,3),'cloud_clock_start_unix_ms':start_unix_ms,'cloud_clock_end_unix_ms':int(time.time()*1000),'mapping':mapping(),'cpu_count':os.cpu_count(),'system_cpu_percent':{k:round(v/total*100,3) for k,v in zip(('user','nice','system','idle','iowait','hardirq','softirq','steal'),cpu_delta)},'top_processes':sorted(top,key=lambda v:v['cpu_percent_one_core'],reverse=True)[:10],'samples':samples,'not_measured':['Host raw/encoded pipeline','phone FPS','unselected clients','runtime application-level NPS queues'],'safety':'no_credentials_or_full_config_emitted; no_mutations'}
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=8, choices=range(1, 31))
    parser.add_argument('--m1-relay', action='store_true', help='also sample the local 18024 physical relay through read-only nettop')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/nps-readonly-metadata-20261001.json')
    args = parser.parse_args()
    local_sampler = None
    local_cpu_before = None
    if args.m1_relay:
        listener = subprocess.run(['lsof','-t','-iTCP:18024','-sTCP:LISTEN'],
                                  capture_output=True,text=True,check=True).stdout.split()
        if len(listener)!=1 or not listener[0].isdigit():
            raise SystemExit('Expected exactly one physical relay listener; no guessing process IDs')
        local_start_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
        local_start=time.monotonic(); local_cpu_before=local_process_counters()
        local_sampler = subprocess.Popen(['nettop','-n','-x','-d','-L',str(args.duration+1),'-p',listener[0]],
                                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    program = REMOTE.replace('duration=DURATION', 'duration='+str(args.duration))
    completed = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                                '-o', 'ConnectionAttempts=1', 'root@146.56.249.175', 'python3 -'],
                               input=program, capture_output=True, text=True, timeout=args.duration+20)
    if completed.returncode:
        if local_sampler is not None:
            local_sampler.terminate(); local_sampler.communicate(timeout=5)
        raise SystemExit('Read-only audit failed; SSH exit='+str(completed.returncode)+'; diagnostic contents suppressed')
    result = json.loads(completed.stdout)
    if local_sampler is not None:
        local_cpu_after=local_process_counters()
        local_elapsed=time.monotonic()-local_start
        result['m1_process_cpu_interval']={
            'start_utc':local_start_utc,'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'elapsed_seconds':local_elapsed,'names_may_be_kernel_truncated_basenames':True,
            'top_processes':sorted([{'pid':pid,'comm':value['comm'],
                'cpu_percent_one_core':round((value['cpu_seconds']-local_cpu_before[pid]['cpu_seconds'])/local_elapsed*100,2)}
                for pid,value in local_cpu_after.items() if pid in local_cpu_before],
                key=lambda value:value['cpu_percent_one_core'],reverse=True)[:15]}
        local_stdout,_ = local_sampler.communicate(timeout=10)
        if local_sampler.returncode:
            raise SystemExit('Read-only local relay nettop failed; diagnostics suppressed')
        rows = list(csv.reader(io.StringIO(local_stdout)))
        headers = rows[0] if rows else []
        records=[]
        for row in rows[1:]:
            if len(row)<2 or '146.56.249.175:8024' not in row[1]:
                continue
            record={k:v for k,v in zip(headers,row) if k}
            record['endpoint']=row[1]
            record['counter_scope']='cumulative_baseline' if not records else 'delta_from_previous_sample'
            records.append(record)
        result['m1_physical_relay_tcp']=records
        result['nettop_selected_pid']=int(listener[0])
        result['nettop_output_row_count']=len(rows)-1
    result['created_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result['observed_active_15556_socket_samples']=sum(any(v['local'].endswith(':15556') for v in sample['tcp_sockets']) for sample in result['samples'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'cpu':result['system_cpu_percent'],
                      'top_processes':result['top_processes'][:3],
                      'per_second_eth0':[(v['elapsed_seconds'],v['interface_rates'].get('eth0')) for v in result['samples']],
                      'per_second_selected_sockets':[len(v['tcp_sockets']) for v in result['samples']],
                      'observed_active_15556_socket_samples':result['observed_active_15556_socket_samples'],
                      'local_physical_relay_samples':len(result.get('m1_physical_relay_tcp',[]))},ensure_ascii=False))


if __name__=='__main__':
    main()
