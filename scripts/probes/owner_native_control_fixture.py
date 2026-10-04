"""Retained host controller for the new native CONTROL HOST FIXTURE only.

No ADB/device or production activation method. A fresh Popen/control and both
drains are held here, never adopted from a PID/event/checkpoint. Host callbacks
run on this object; native callbacks run separately inside its actual child.
Closed events schedule requests, never permission, cleanup or lease release.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import time

from scripts.probes.owner_native_gateway_environment import select
from scripts.probes.owner_native_gateway_lifecycle import _Drain


class ControlUnknown(RuntimeError):
    """Fixed labels only; no private child output or callback text."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate')
        result[key] = value
    return result


def _environment(env):
    if type(env) is not dict: raise ValueError('control_exact_environment_required')
    try:
        expected = select(Path(env['HOME']),Path(env['HOME'])/'Library/Android/sdk/platform-tools/adb',
            Path(env['DIRECT_STATE_DIR']),Path(env['DIRECT_DIAGNOSTICS_DIR']).parent.parent,
            {k:env[k] for k in ('DIRECT_AUTH_FILE','DIRECT_CERT','DIRECT_KEY','DIRECT_VIDEO_BACKEND')})
    except Exception: raise ValueError('control_exact_environment_required') from None
    if env != expected: raise ValueError('control_exact_environment_required')


MODES = frozenset(('natural','five_seconds','native_gate_missing','later_Attempt',
    'duplicate_event','extra_event','stderr','overflow','no_response','late_response'))
EVENTS = {'upload':('uploaded',),'install':('installed',),'start':('started',),
    'poll':('pending','local_closed'),'done':('done',)}


class FixtureControl:
    """Construction inert; fixed binary+fixture grammar only, no arbitrary argv.

    The trusted caller still independently reviews actual dependencies and
    callback provenance. Even accepted host closure always has release=False.
    Timeout/interrupt/checkpoint failure holds actual Popen/control/drains;
    there is no automatic signal, close, uninstall, scope deletion or release.
    """
    def __init__(self, binary, digest, environment, checkpoint, qualify):
        if (not isinstance(binary,Path) or not binary.is_absolute()
                or type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None
                or not callable(checkpoint) or not callable(qualify)):
            raise ValueError('control_reviewed_fixture_binding_required')
        _environment(environment)
        self.binary,self.digest,self.environment = binary,digest,dict(environment)
        self.checkpoint,self.qualify = checkpoint,qualify
        self._process = None;self.drains=[];self.phase='new';self.pending=None
        self.sequence=0;self.offset=0;self.partial=b'';self.ceiling=None;self.request_end=None
        self.nonce=None;self.payload=None;self.footer=None;self.collected=False

    @property
    def process(self): return self._process

    def _record(self, label):
        try:
            result=self.checkpoint(dict(phase=self.phase,possible_operation=label,
                actual_host_owned_pid=None if self.process is None else self.process.pid,
                checkpoint_is_ownership=False,Android_operations=False,release_authorized=False))
            if result is not True: raise ValueError('checkpoint')
        except BaseException:
            self.phase='unknown';raise ControlUnknown('control_checkpoint_unverified') from None

    def _fail(self, label):
        self.phase='unknown'
        try: self._record('unknown')
        except BaseException: pass
        raise ControlUnknown(label)

    def _qualified(self, label, end):
        try: good=self.qualify(label,self,end)
        except BaseException: self._fail('control_host_qualification_unknown')
        if good is not True or time.monotonic()>=end: self._fail('control_host_qualification_unknown')

    def start_fixture(self, base, package, nonce, payload, mode='natural'):
        if (self.phase!='new' or self.process is not None or mode not in MODES
                or any(not isinstance(p,Path) or not p.is_absolute() for p in (base,package))
                or type(nonce) is not str or re.fullmatch('[0-9a-f]{24}',nonce) is None
                or type(payload) is not bytes or payload!=b'public host APK standin\n'):
            raise ValueError('control_fixed_new_host_fixture_required')
        _environment(self.environment)
        self.ceiling=time.monotonic()+30
        end=time.monotonic()+3
        self._qualified('before_transport',end)
        fd=os.open(self.binary,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            before=os.fstat(fd);raw=os.read(fd,1024*1024+1);after=os.fstat(fd)
            if (before!=after or not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                    or before.st_nlink!=1 or before.st_mode&0o7777!=0o700
                    or len(raw)!=before.st_size or hashlib.sha256(raw).hexdigest()!=self.digest
                    or b'host_control_fixture_footer' not in raw or b'--control-fixture' not in raw):
                self._fail('control_exact_host_fixture_binary_required')
        finally: os.close(fd)
        if time.monotonic()>=end: self._fail('control_preparation_timeout')
        self.nonce=nonce.encode();self.payload=payload
        self.phase='possible_start';self._record('transport_start')
        self._qualified('before_transport',end)
        try:
            self._process=subprocess.Popen((str(self.binary),'--control-fixture',str(base),str(package),
                nonce,hashlib.sha256(payload).hexdigest(),mode),stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,shell=False,
                close_fds=True,env=self.environment)
            for stream in (self.process.stdout,self.process.stderr):
                d=_Drain(stream);self.drains.append(d);d.thread.start()
            os.set_blocking(self.process.stdin.fileno(),False)
            self.phase='ready';self._record('transport_started')
            if time.monotonic()>=end: self._fail('control_transport_creation_timeout')
        except BaseException:
            self._fail('control_possible_transport_retained')

    def _header(self, kind, sequence, size=0, digest=b'0'*64):
        return b'HGHC0001'+bytes((kind,0,0,0))+struct.pack('!I',size)+self.nonce+struct.pack('!Q',sequence)+digest

    def _schedule(self, kind, wire, sequence, required):
        if self.phase=='unknown': raise ControlUnknown('control_sticky_unknown')
        if self.phase!=required or self.pending is not None or self.process is None:
            self._fail('control_one_outstanding_phase_required')
        self._consume()
        end=min(time.monotonic()+3,self.ceiling)
        self._qualified('before_'+kind,end)
        self.pending=(kind,sequence);self.request_end=end
        self._record('possible_'+kind)
        # Checkpoint/probes count in the SAME request budget.
        if time.monotonic()>=end: self._fail('control_request_timeout')
        at=0;selector=selectors.DefaultSelector()
        try:
            selector.register(self.process.stdin,selectors.EVENT_WRITE)
            while at<len(wire):
                if time.monotonic()>=end: self._fail('control_request_timeout')
                self._check_drains()
                try: n=os.write(self.process.stdin.fileno(),wire[at:])
                except BlockingIOError:
                    selector.select(min(.02,max(0,end-time.monotonic())));continue
                if n<=0: self._fail('control_request_write_unverified')
                at+=n
        except BaseException:
            self._fail('control_possible_request_retained')
        finally: selector.close()

    def upload(self):
        sha=hashlib.sha256(self.payload).hexdigest().encode()
        self._schedule('upload',self._header(1,0,len(self.payload),sha)+self.payload+self._header(2,1),1,'ready')

    def install(self): self._schedule('install',self._header(3,2),2,'uploaded')

    def start_driver(self):
        seq=self.sequence
        self._schedule('start',b'HGDS0001'+bytes((1,))+b'\0'*7+self.nonce+struct.pack('!Q',seq),seq,'installed')
        self.sequence+=1

    def poll_driver(self):
        seq=self.sequence
        self._schedule('poll',b'HGDS0001'+bytes((2,))+b'\0'*7+self.nonce+struct.pack('!Q',seq),seq,'running')
        self.sequence+=1

    def driver_done(self): self._schedule('done',self._header(4,3),3,'local_closed')

    def _check_drains(self):
        if len(self.drains)!=2 or any(d.failed or d.overflow for d in self.drains) or self.drains[1].data:
            self._fail('control_output_unverified')

    def _consume(self):
        self._check_drains()
        data=bytes(self.drains[0].data)
        self.partial+=data[self.offset:];self.offset=len(data)
        while b'\n' in self.partial:
            line,self.partial=self.partial.split(b'\n',1)
            try:
                row=json.loads(line.decode('utf8'),object_pairs_hook=_unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
                if type(row) is not dict: raise ValueError('object')
            except Exception: self._fail('control_closed_event_required')
            if row.get('event')=='host_control_fixture_footer':
                expected={'event':'host_control_fixture_footer','host_fixture':True,
                    'native_callbacks_synthetic':True,'driver_done':True,
                    'scope_retained':True,'release_authorized':False}
                if (row!=expected or any(type(row[k]) is not type(v) for k,v in expected.items())
                        or self.phase!='done' or self.footer is not None):
                    self._fail('control_fixture_footer_unverified')
                self.footer=row;continue
            if (set(row)!={'event','phase','sequence','status','permission_ACK'}
                    or type(row['phase']) is not str or type(row['status']) is not str
                    or row['event']!='native_control_schedule' or row['permission_ACK'] is not False
                    or type(row['sequence']) is not int or self.pending!=(row['phase'],row['sequence'])
                    or row['status'] not in EVENTS.get(row['phase'],())):
                self._fail('control_closed_order_required')
            if time.monotonic()>=self.request_end or time.monotonic()>=self.ceiling:
                self._fail('control_late_response_refused')
            self._qualified('after_'+row['phase'],self.request_end)
            self.phase={'uploaded':'uploaded','installed':'installed','started':'running',
                'pending':'running','local_closed':'local_closed','done':'done'}[row['status']]
            self.pending=None;self._record('scheduled_response')

    def poll_response(self):
        if self.phase=='unknown': raise ControlUnknown('control_sticky_unknown')
        if self.process is None: raise ControlUnknown('control_actual_transport_required')
        outstanding=self.pending
        self._consume()
        if self.pending is not None and time.monotonic()>=self.request_end:
            self._fail('control_response_timeout')
        if time.monotonic()>=self.ceiling: self._fail('control_original_ceiling')
        # Idle is not a completed response. Only a request this very object
        # held before consuming an exact ordered event can return True.
        return outstanding is not None and self.pending is None

    def collect_fixture_closure(self):
        if self.phase=='unknown': raise ControlUnknown('control_sticky_unknown')
        if self.collected: raise ControlUnknown('control_collect_once')
        if self.phase!='done' or self.pending is not None: self._fail('control_order_incomplete')
        self._consume()
        if self.process.poll()!=0 or self.footer is None: self._fail('control_same_parent_exit_unknown')
        end=min(time.monotonic()+3,self.ceiling)
        for d in self.drains: d.thread.join(timeout=max(0,end-time.monotonic()))
        self._check_drains();self._consume()
        if any(d.thread.is_alive() for d in self.drains) or self.partial:
            self._fail('control_dual_EOF_unknown')
        self._qualified('after_transport',end)
        self.collected=True;self.phase='fixture_closed';self._record('local_transport_closed')
        return dict(actual_same_host_Popen_exit=0,actual_dual_EOF=True,
            Android_qualifiers=False,remote_scope_cleanup=False,reservation_release=False)
