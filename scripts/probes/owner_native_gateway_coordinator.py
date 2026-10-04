"""Default-OFF finite gateway/driver entry binding for one private M1 window.

Only a trusted local caller can construct this object. That caller supplies
reviewed frozen source/runtime/environment and finite fresh phone/artifact/
operator qualification. No CLI, account, HTTP, env or JSON supplies permission.
Unknown remote/local cleanup retains this supervisor and the original reserve;
there is deliberately no automatic driver termination or destructor release.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import time

from scripts.probes.owner_native_gateway_lifecycle import OwnedGateway, LifecycleError, _Drain
from scripts.probes.owner_native_window import Window
from scripts.probes.owner_lan_admission import validate_readback


@dataclass(frozen=True)
class Entries:
    source: Path
    python: Path
    runtime: Path
    evidence: Path
    interface: str = 'en7'

    def __post_init__(self):
        if (any(not isinstance(p,Path) or not p.is_absolute() for p in
                (self.source,self.python,self.runtime,self.evidence))
                or self.interface not in ('en7','en0')):
            raise ValueError('native_coordinator_reviewed_paths_required')

    def argv(self, window, kind):
        """Fixed module and argv: both children receive the same Plan/Window.

        Literal public pins/paths only, never credentials, stdin or UI coords.
        Actual file bytes and path ownership remain the qualification callback's
        responsibility; this builder itself is inert.
        """
        if type(window) is not Window or kind not in ('gateway','driver'):
            raise ValueError('native_coordinator_exact_entry_required')
        window.__post_init__(); self.__post_init__()
        module = 'udp_lan_gateway' if kind == 'gateway' else 'scripts.probes.run_authenticated_lan_ui'
        program = ('import sys;sys.path.insert(0,' + repr(str(self.source)) + ');'
            'from scripts.probes.owner_native_diagnostic_preflight import Plan;'
            'from scripts.probes.owner_native_window import Window;'
            'from ' + module + ' import main;'
            'window=Window(Plan(**' + repr(vars(window.plan)) + '),' + str(window.seconds) + ');'
            'raise SystemExit(main(owner_native_window=window))')
        if kind == 'gateway':
            options = ('--host','192.168.9.128','--interface',self.interface,
                '--network-scope','lan','--https-port','45560','--udp-port','45963',
                '--runtime',str(self.runtime),'--packetizer',str(self.runtime/'h264_udp_packetizer'),
                '--native-encoder',str(self.runtime/'session-pool-encoder'),
                '--evidence-dir',str(self.evidence/'gateway'),'--max-runtime',str(window.plan.process_max_seconds))
        else:
            options = ('--phone','f7fc9469','--guest','emulator-5556',
                '--output',str(self.evidence/'phone'),'--network-scope','lan',
                '--media-only','--credential-source','saved-ui','--credential-save','off',
                '--v50-profile','on','--stage-diagnostics','off','--pcm-queue','off',
                '--codec-startup','off','--surface-submit-lead-ms','0')
        # A fresh isolated interpreter imports only the explicit reviewed root,
        # not an inherited PYTHONPATH or user-site shadow of that source.
        argv = (str(self.python),'-I','-u','-c',program) + options
        if len(argv)>64 or sum(map(len,argv))>8192 or any('\0' in x for x in argv):
            raise ValueError('native_coordinator_entry_bound')
        return argv

    def environment(self, home, state, original):
        """Pure exact child environment; referenced bytes still need review."""
        self.__post_init__()
        from scripts.probes.owner_native_gateway_environment import select
        return select(home, home / 'Library/Android/sdk/platform-tools/adb',
                      state, self.evidence, original)


def _ready(raw, window, interface):
    """Only this gateway pipe prefix; records never establish permission."""
    end = raw.rfind(b'\n')
    values = []
    try:
        def unique(pairs):
            result = {}
            for key,value in pairs:
                if key in result: raise ValueError('duplicate')
                result[key] = value
            return result
        for line in raw[:end+1].splitlines() if end>=0 else ():
            row = json.loads(line,object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            if type(row) is not dict: raise ValueError('object')
            values.append(row)
    except (ValueError,UnicodeError,RecursionError):
        raise LifecycleError('native_coordinator_readiness_unverified') from None
    listen = [row for row in values if row.get('event')=='listening']
    select = [row for row in values if row.get('event')=='owner_native_window_selection']
    if len(listen)>1 or len(select)>1: raise LifecycleError('native_coordinator_readiness_unverified')
    if not listen or not select: return False
    expected = dict(event='listening',host='192.168.9.128',https_port=45560,udp_port=45963,
        scope='isolated-lan',network_scope='lan',inner_interface=interface,capture_trace_enabled=False,
        owner_raw_queue_policy_requested='fifo',owner_raw_submit_fps_requested=None,owner_enobufs_retry_enabled=False)
    # LanScope's actual ping_scope is closed and checked against its source.
    from udp_network_scope import LanScope
    expected['scope'] = LanScope.ping_scope
    selection = dict(event='owner_native_window_selection',diagnostic_events_requested=True,
        descriptor_seconds_ceiling=30,process_seconds_ceiling=window.plan.process_max_seconds,
        operator_or_server_lease_established_by_this_record=False)
    def exact(row,target):
        return set(row)==set(target) and all(type(row[k]) is type(v) and row[k]==v for k,v in target.items())
    if not exact(listen[0],expected) or not exact(select[0],selection):
        raise LifecycleError('native_coordinator_readiness_unverified')
    return True


class Coordinator:
    """Keep the actual gateway and driver Popen objects through their teardown.

    qualify is a fresh reviewed finite callback, not a JSON true field. It must
    check source/dependency/artifact/device/operator/private-scope eligibility,
    physical interface and port conflicts. phone_idle independently checks
    helper/input/current target absence after the driver's own normal cleanup.
    """
    def __init__(self, gateway, entries, qualify, phone_idle):
        if type(gateway) is not OwnedGateway or type(entries) is not Entries or not callable(qualify) or not callable(phone_idle):
            raise ValueError('native_coordinator_trusted_objects_required')
        entries.__post_init__()
        self.gateway,self.entries,self.qualify,self.phone_idle = gateway,entries,qualify,phone_idle
        self._driver = None; self.driver_drains = []; self.state = 'new'
        self.driver_exit = None

    @property
    def driver(self): return self._driver

    def _qualified(self, phase):
        try:
            if self.qualify(phase) is not True: raise ValueError('qualification')
        except Exception:
            raise LifecycleError('native_coordinator_fresh_qualification_unverified') from None

    def start(self, *, env):
        if self.state != 'new': raise LifecycleError('native_coordinator_not_new')
        self._qualified('before_gateway')
        if type(env) is not dict or any(type(k) is not str or type(v) is not str or '\0' in k+v for k,v in env.items()):
            raise ValueError('native_coordinator_explicit_environment_required')
        try:
            self.gateway.start(self.entries.argv(self.gateway.window,'gateway'),cwd=self.entries.source,env=env)
        except BaseException:
            self.state = 'cleanup_required' if self.gateway.admission.state in ('lan_starting','cleanup_required') else 'refused'
            raise
        self.state = 'gateway_started'

    def start_driver(self, *, env):
        if self.state != 'gateway_started' or self.driver is not None:
            raise LifecycleError('native_coordinator_driver_order_unverified')
        if type(env) is not dict or any(type(k) is not str or type(v) is not str or '\0' in k+v for k,v in env.items()):
            raise ValueError('native_coordinator_explicit_environment_required')
        try:
            end = min(time.monotonic()+12,self.gateway.deadline)
            while time.monotonic()<end:
                if self.gateway.process.poll() is not None: raise LifecycleError('native_coordinator_gateway_startup_failed')
                if len(self.gateway.drains)!=2 or any(d.failed or d.overflow for d in self.gateway.drains):
                    raise LifecycleError('native_coordinator_readiness_unverified')
                if _ready(bytes(self.gateway.drains[0].data),self.gateway.window,self.entries.interface): break
                time.sleep(.025)
            else: raise LifecycleError('native_coordinator_readiness_timeout')
            # Listener/selection rows are not a source/device/operator lease.
            self._qualified('before_driver')
            validate_readback(self.gateway.admission.readback(),self.gateway.admission.expected)
            self.gateway._formal()
            self._driver = subprocess.Popen(self.entries.argv(self.gateway.window,'driver'),
                cwd=self.entries.source,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,shell=False,close_fds=True,bufsize=0)
            for pipe in (self.driver.stdout,self.driver.stderr):
                drain = _Drain(pipe); self.driver_drains.append(drain); drain.thread.start()
            self.state = 'driver_started'
        except LifecycleError:
            self.state = 'cleanup_required'; raise
        except BaseException:
            self.state = 'cleanup_required'
            raise LifecycleError('native_coordinator_driver_start_unverified') from None

    def wait_driver(self, seconds=90):
        if self.driver is None or self.state == 'released': raise LifecycleError('native_coordinator_owned_driver_required')
        if type(seconds) not in (int,float) or not 0<=seconds<=90:
            raise ValueError('native_coordinator_driver_wait_bound')
        try:
            code = self.driver.wait(timeout=min(seconds,max(0,self.gateway.deadline-time.monotonic())))
        except subprocess.TimeoutExpired:
            self.state = 'cleanup_required'; return None  # Never kill ADB and pretend the App exited.
        except Exception:
            self.state = 'cleanup_required'; raise LifecycleError('native_coordinator_driver_wait_unverified') from None
        self.driver_exit = code
        return code

    def finish(self):
        # Called only after the actual finite gateway's own natural wait/exit.
        try:
            if self.driver is None or type(self.driver.poll()) is not int:
                raise LifecycleError('native_coordinator_driver_exit_unverified')
            code = self.driver.returncode
            end = time.monotonic()+2
            try:
                for drain in self.driver_drains: drain.thread.join(timeout=max(0,end-time.monotonic()))
            except Exception:
                raise LifecycleError('native_coordinator_driver_EOF_unverified') from None
            if len(self.driver_drains)!=2 or any(d.thread.is_alive() or d.failed or d.overflow for d in self.driver_drains):
                raise LifecycleError('native_coordinator_driver_EOF_unverified')
            try:
                if self.phone_idle() is not True: raise ValueError('phone')
            except Exception:
                raise LifecycleError('native_coordinator_phone_cleanup_unverified') from None
            receipt = self.gateway.finish()
        except BaseException:
            self.state = 'cleanup_required'; raise
        self.state = 'released'
        return dict(receipt,driver_exit_observed=code,driver_stdout_and_stderr_EOF_confirmed=True,
            phone_idle_independently_read_back=True,driver_success=code==0,
            observed_ART_native_report_or_performance_accepted=False)
