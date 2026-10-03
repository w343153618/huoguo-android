#!/usr/bin/env python3
"""Add fixed HTTPS signaling tasks for bounded owner UDP trials; no NPS restart.

Only TCP49556/49558 to each existing NPC's loopback45561 is created. Real-time
media uses the independently verified UDP15556/15558 tasks. No account, bridge,
firewall, target proxy or original task is changed. Existing full backup and
preservation readers are reused; unknown outcomes never trigger a blind retry.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import time

_SPEC = importlib.util.spec_from_file_location('_owner_control_existing',
    Path(__file__).with_name('create_dual_udp_nps_tasks.py'))
base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base)
Refuse = base.Refuse
TARGET = '127.0.0.1:45561'
SCOPES = ((1466, 49556, 'HuoguoAndroid-M1-UDP-control-49556'),
          (1468, 49558, 'HuoguoAndroid-M5-UDP-control-49558'))


def listeners():
    result = {}
    for _, port, _ in SCOPES:
        raw = base.command(['ss','-H','-l','-t','-n','-p','( sport = :%d )' % port])
        owners = []
        for line in raw.splitlines():
            matches = base.re.findall(r'\bpid=([1-9][0-9]*)\b', line)
            if not matches:
                raise Refuse('control_listener_ownership_unavailable')
            owners.extend(int(value) for value in matches)
        result[port] = frozenset(owners)
    return result


def capture(admin):
    snapshot = base.capture(admin)
    snapshot['control_listeners'] = listeners()
    return snapshot


def exact(row, scope):
    client, port, remark = scope
    target, flow, auth = row.get('Target'), row.get('Flow'), row.get('UserAuth')
    if not isinstance(target, dict) or (flow is not None and not isinstance(flow, dict)):
        return False
    auth_empty = auth is None or (isinstance(auth, dict) and auth.get('Content','') == ''
        and auth.get('AccountMap') in (None,{}) and not set(auth)-{'Content','AccountMap'})
    return (type(row.get('Id')) is int and row['Id'] > 0 and row.get('Mode') == 'tcp'
        and type(row.get('Port')) is int and row['Port'] == port
        and row.get('Client',{}).get('Id') == client and row.get('Remark') == remark
        and row.get('ServerIp') == '0.0.0.0' and row.get('Status') is True
        and target.get('TargetStr') == TARGET and target.get('LocalProxy') is False
        and type(target.get('ProxyProtocol',0)) is int and target.get('ProxyProtocol',0) == 0
        and row.get('DestAclMode',0) == 0 and row.get('DestAclRules','') == ''
        and row.get('Password','') == '' and row.get('LocalPath','') == ''
        and row.get('StripPre','') == '' and row.get('HttpProxy',False) is False
        and row.get('Socks5Proxy',False) is False and row.get('TargetType','') in ('','all')
        and (flow or {}).get('FlowLimit',0) == 0
        and (flow or {}).get('TimeLimit','0001-01-01T00:00:00Z') == '0001-01-01T00:00:00Z'
        and auth_empty)


def plan(snapshot, starting_ids=()):
    # Both prior UDP tasks and original TCP tasks/QUIC identities must remain valid.
    old = base.plan(snapshot)
    if any(item['task'] is None for item in old):
        raise Refuse('verified_udp_media_task_required')
    result = []
    for scope in SCOPES:
        client, port, remark = scope
        matches = [row for row in snapshot['tasks'].values()
            if (row.get('Mode') == 'tcp' and row.get('Port') == port) or row.get('Remark') == remark]
        live = [row for row in snapshot['runtime_tasks'].values()
            if (row.get('Mode') == 'tcp' and row.get('Port') == port) or row.get('Remark') == remark]
        if len(matches) > 1 or (matches and not exact(matches[0], scope)):
            raise Refuse('control_task_conflict_or_parameters_differ')
        task = matches[0] if matches else None
        if task is None:
            if live or snapshot['control_listeners'][port]:
                raise Refuse('control_port_or_runtime_conflict')
        else:
            if len(live)!=1 or live[0].get('Id') != task['Id'] or not exact(live[0],scope):
                raise Refuse('control_runtime_readback_differs')
            if live[0].get('RunStatus') is not True and task['Id'] not in starting_ids:
                raise Refuse('control_task_not_running')
            permitted = (frozenset(),frozenset((snapshot['process']['pid'],))) if task['Id'] in starting_ids else (frozenset((snapshot['process']['pid'],)),)
            if snapshot['control_listeners'][port] not in permitted:
                raise Refuse('control_listener_not_formal_nps')
        result.append({'scope':scope,'task':task})
    return result


def preserved(before, after, created=(), starting=()):
    base.preserved(before,after,created)
    plan(after,starting)


def fields(scope):
    client,port,remark = scope
    return {'client_id':client,'type':'tcp','server_ip':'0.0.0.0','port':port,
        'target':TARGET,'local_proxy':'false','proxy_protocol':0,'remark':remark,
        'flow_limit':0,'dest_acl_mode':0}


def backup(snapshot):
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    base.private_dir(base.BACKUP_ROOT)
    dest=base.BACKUP_ROOT/('owner-udp-control-'+stamp+'-'+secrets.token_hex(4))
    base.private_dir(dest);files=base.conf_files();hashes={}
    for source in files:
        relative=source.relative_to(base.CONF_DIR);target=dest/'conf'/relative
        base.private_dir(target.parent);data=base.read_regular(source)
        base.private_write(target,data);digest=hashlib.sha256(data).hexdigest()
        if hashlib.sha256(base.read_regular(target)).hexdigest()!=digest:
            raise Refuse('backup_copy_checksum_failed')
        hashes[str(relative)]=digest
    if files!=base.conf_files() or any(hashlib.sha256(base.read_regular(base.CONF_DIR/name)).hexdigest()!=digest for name,digest in hashes.items()):
        raise Refuse('conf_changed_during_backup')
    base.private_write(dest/'manifest.json',json.dumps({'created_utc':stamp,'process':snapshot['process'],
        'online_ids':sorted(snapshot['online_ids']),'geo':snapshot['geo'],'conf_sha256':hashes,
        'verified_regular_files':len(hashes)}).encode()+b'\n')
    return dest,len(hashes)


def summary(snapshot, tasks, created=(), backup_path=None, backup_files=0):
    return {'status':'verified','read_only':backup_path is None,'formal_nps_pid':snapshot['process']['pid'],
        'online_count':len(snapshot['online_ids']),'created_task_ids':list(created),
        'preservation_verified':True,'backup_path':str(backup_path) if backup_path else None,
        'backup_regular_files':backup_files,'backup_checksums_verified':backup_path is not None,
        'tasks':[{'client_id':item['scope'][0],'public_control_tcp':item['scope'][1],
            'task_id':item['task']['Id'] if item['task'] else None,'target':TARGET} for item in tasks]}


def execute(admin, create=False):
    before=capture(admin);initial=plan(before)
    if not create or all(item['task'] is not None for item in initial):
        after=capture(admin);preserved(before,after)
        return summary(after,plan(after))
    with base.creation_lock():
        current=capture(admin);preserved(before,current);dest,count=backup(current)
        current=capture(admin);preserved(before,current);created=[]
        try:
            for item in plan(current):
                if item['task'] is not None:continue
                scope=item['scope'];client=current['clients'][scope[0]];limit=client['MaxTunnelNum']
                if limit and sum(t.get('Client',{}).get('Id')==scope[0] for t in current['tasks'].values())>=limit:
                    raise Refuse('client_max_tunnel_limit_rejected_no_client_change')
                fresh=capture(admin);preserved(before,fresh,created);current=fresh
                if plan(current)[SCOPES.index(scope)]['task'] is not None:
                    raise Refuse('control_task_appeared_concurrently_reinspect_required')
                try:response=admin.json('/index/add',fields(scope))
                except Exception as exc:raise Refuse('control_add_outcome_unknown_reinspect_required') from exc
                if type(response.get('status')) is not int or response['status']!=1:
                    raise Refuse('control_add_rejected_reinspect_required')
                task_id=base.integer(response.get('id'));created.append(task_id)
                deadline=time.monotonic()+base.READINESS_SECONDS
                while True:
                    with base.inspection_budget(deadline):
                        current=capture(admin);preserved(before,current,created,(task_id,))
                        matched=plan(current,(task_id,))[SCOPES.index(scope)]['task']
                        if matched is None or matched['Id']!=task_id:
                            raise Refuse('created_control_identity_not_verified')
                        if current['runtime_tasks'][task_id].get('RunStatus') is True and current['control_listeners'][scope[1]]==frozenset((current['process']['pid'],)):
                            base.remaining_timeout();break
                    if time.monotonic()>=deadline:raise Refuse('control_start_outcome_unknown_reinspect_required')
                    time.sleep(min(base.READINESS_POLL_SECONDS,max(0,deadline-time.monotonic())))
            after=capture(admin);preserved(before,after,created)
            receipt=summary(after,plan(after),created,dest,count)
            base.private_write(dest/'result.json',json.dumps(receipt).encode()+b'\n')
            return receipt
        except Refuse as error:
            error.created_task_ids=tuple(created);error.backup_path=str(dest);raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--create',action='store_true')
    args=parser.parse_args();base.require_host();config,url=base.settings()
    print(json.dumps(execute(base.Admin(config,url),args.create)))


if __name__=='__main__':
    try:main()
    except Refuse as error:
        receipt={'status':'refused','reason':str(error),'credentials_printed':False}
        if hasattr(error,'created_task_ids'):receipt.update(created_task_ids=list(error.created_task_ids),backup_path=error.backup_path)
        print(json.dumps(receipt),file=sys.stderr);raise SystemExit(2)
    except Exception as error:
        print(json.dumps({'status':'failed','reason':type(error).__name__,'credentials_printed':False}),file=sys.stderr);raise SystemExit(3)
