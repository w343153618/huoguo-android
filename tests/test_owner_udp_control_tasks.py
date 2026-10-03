"""Offline control-task authorization and preservation fixtures; no network."""
import copy
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.test_dual_udp_nps_tasks import fixture, install_udp, HELPER

SPEC=importlib.util.spec_from_file_location('owner_control',Path(__file__).resolve().parents[1]/'scripts/deployment/create_owner_udp_control_tasks.py')
c=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(c)


def snapshot():
    value=fixture()
    for index,scope in enumerate(HELPER.SCOPES):install_udp(value,scope,1412+index)
    value['control_listeners']={49556:frozenset(),49558:frozenset()}
    return value


def add(value,scope,task_id):
    client,port,remark=scope
    row={'Id':task_id,'Mode':'tcp','Port':port,'Client':copy.deepcopy(value['clients'][client]),
        'Remark':remark,'Status':True,'ServerIp':'0.0.0.0',
        'Target':{'TargetStr':c.TARGET,'LocalProxy':False,'ProxyProtocol':0},'Flow':{'FlowLimit':0}}
    value['tasks'][task_id]=row;value['runtime_tasks'][task_id]=dict(copy.deepcopy(row),RunStatus=True)
    value['control_listeners'][port]=frozenset((value['process']['pid'],));return row


class Admin:
    def __init__(self,value,response=None):self.value=value;self.response=response;self.calls=[]
    def json(self,path,fields):
        self.calls.append((path,fields));assert path=='/index/add'
        if isinstance(self.response,Exception):raise self.response
        if self.response is not None:return self.response
        scope=next(scope for scope in c.SCOPES if c.fields(scope)==fields)
        task_id=max(self.value['tasks'])+1;add(self.value,scope,task_id);return {'status':1,'id':task_id}


class ControlChecks(unittest.TestCase):
    def test_missing_media_task_prevents_control_creation(self):
        value=snapshot();del value['tasks'][1412];del value['runtime_tasks'][1412];value['listeners'][15556]=frozenset()
        with self.assertRaisesRegex(c.Refuse,'verified_udp_media'):c.plan(value)
    def test_media_ports_and_old_tasks_remain_separate(self):
        value=snapshot();self.assertEqual([r['task'] for r in c.plan(value)],[None,None])
        self.assertEqual([c.fields(s)['port'] for s in c.SCOPES],[49556,49558])
        self.assertTrue(all(c.fields(s)['target']=='127.0.0.1:45561' and c.fields(s)['type']=='tcp' for s in c.SCOPES))
    def test_conflict_wrong_owner_target_type_or_public_port_rejected(self):
        for mutation in ('owner','target','type','port','proxy','auth','local','disabled'):
            with self.subTest(mutation=mutation):
                value=snapshot();row=add(value,c.SCOPES[0],1414)
                if mutation=='owner':row['Client']={'Id':999}
                elif mutation=='target':row['Target']['TargetStr']='127.0.0.1:5555'
                elif mutation=='type':row['Mode']='udp'
                elif mutation=='port':row['Port']=49557
                elif mutation=='proxy':row['Target']['ProxyProtocol']=True
                elif mutation=='auth':row['UserAuth']={'Content':'offline-unwanted-auth'}
                elif mutation=='local':row['Target']['LocalProxy']=True
                else:row['Status']=False
                with self.assertRaisesRegex(c.Refuse,'control_task_conflict'):c.plan(value)
    def test_runtime_readback_required(self):
        for change in ({'RunStatus':False},{'Port':49557},{'Client':{'Id':999}}):
            value=snapshot();add(value,c.SCOPES[0],1414);value['runtime_tasks'][1414].update(change)
            with self.assertRaises(c.Refuse):c.plan(value)
    def test_listener_owner_cannot_be_another_pid(self):
        value=snapshot();add(value,c.SCOPES[0],1414);value['control_listeners'][49556]=frozenset((111,))
        with self.assertRaisesRegex(c.Refuse,'listener_not_formal'):c.plan(value)
    def test_unowned_listener_blocks_new_task(self):
        value=snapshot();value['control_listeners'][49556]=frozenset((111,))
        with self.assertRaisesRegex(c.Refuse,'control_port_or_runtime_conflict'):c.plan(value)
    def test_readonly_never_backs_up_or_posts(self):
        value=snapshot();a=Admin(value)
        with patch.object(c,'capture',side_effect=lambda _:copy.deepcopy(value)),patch.object(c,'backup') as backup:
            receipt=c.execute(a)
        self.assertTrue(receipt['read_only']);self.assertEqual(a.calls,[]);backup.assert_not_called()
    def test_create_exact_bounded_tasks_without_other_changes(self):
        value=snapshot();before=copy.deepcopy(value);a=Admin(value)
        with patch.object(c,'capture',side_effect=lambda _:copy.deepcopy(value)),patch.object(c,'backup',return_value=(Path('/offline/backup'),9)),patch.object(c.base,'creation_lock',return_value=nullcontext()),patch.object(c.base,'private_write'):
            receipt=c.execute(a,True)
        self.assertEqual([r[1]['port'] for r in a.calls],[49556,49558]);self.assertEqual(receipt['created_task_ids'],[1414,1415])
        self.assertEqual(value['process'],before['process']);self.assertEqual(value['geo'],before['geo']);self.assertEqual(value['clients'],before['clients'])
        for key,row in before['tasks'].items():self.assertEqual(value['tasks'][key],row)
    def test_idempotence_does_not_post_or_backup(self):
        value=snapshot()
        for i,s in enumerate(c.SCOPES):add(value,s,1414+i)
        a=Admin(value)
        with patch.object(c,'capture',side_effect=lambda _:copy.deepcopy(value)),patch.object(c,'backup') as backup:
            receipt=c.execute(a,True)
        self.assertEqual(a.calls,[]);backup.assert_not_called();self.assertTrue(receipt['read_only'])
    def test_lost_reply_is_never_blindly_retried(self):
        value=snapshot();a=Admin(value,OSError())
        with patch.object(c,'capture',side_effect=lambda _:copy.deepcopy(value)),patch.object(c,'backup',return_value=(Path('/offline/backup'),9)),patch.object(c.base,'creation_lock',return_value=nullcontext()):
            with self.assertRaisesRegex(c.Refuse,'outcome_unknown'):c.execute(a,True)
        self.assertEqual(len(a.calls),1)
    def test_process_geo_original_client_or_task_changes_rejected(self):
        for case in ('process','geo','online','client','task'):
            before=snapshot();after=copy.deepcopy(before)
            if case=='process':after['process']['pid']=1
            elif case=='geo':after['geo']['geo_in']={}
            elif case=='online':after['online_ids']=frozenset((1466,1468))
            elif case=='client':after['clients'][999]['Remark']='changed'
            else:after['tasks'][1409]['Target']['TargetStr']='127.0.0.1:5555'
            with self.assertRaises(c.Refuse):c.preserved(before,after)
    def test_limit_refuses_without_changing_client(self):
        value=snapshot();value['clients'][1466]['MaxTunnelNum']=2;value['runtime_clients'][1466]['MaxTunnelNum']=2
        for row in value['runtime_tasks'].values():
            if row['Client']['Id']==1466:row['Client']['MaxTunnelNum']=2
        for row in value['tasks'].values():
            if row['Client']['Id']==1466:row['Client']['MaxTunnelNum']=2
        a=Admin(value)
        with patch.object(c,'capture',side_effect=lambda _:copy.deepcopy(value)),patch.object(c,'backup',return_value=(Path('/offline/backup'),9)),patch.object(c.base,'creation_lock',return_value=nullcontext()):
            with self.assertRaisesRegex(c.Refuse,'max_tunnel_limit'):c.execute(a,True)
        self.assertEqual(a.calls,[])


if __name__=='__main__':unittest.main()
