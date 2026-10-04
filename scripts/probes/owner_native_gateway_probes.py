"""Explicit read-only bounded host probes for the private M1 coordinator.

No CLI, password reader, service signals, PID adoption or permission-by-JSON.
The role scan is conservative across known M1 hardware/control roles. It is
used with the pinned gateway's joined registry/reaper contract, never alone as
proof that arbitrary reparented processes are absent. Construction is inert.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import time

from scripts.probes.owner_lan_admission import ExpectedGateway


RUNTIME_NAMES = ('source-alpha8-dd43a39/udp_nps_gateway.py',
    'source-alpha8-dd43a39/udp_lan_worker.py', 'source-alpha8-dd43a39/hardware_stream.py',
    'source-alpha8-dd43a39/udp_lan_sessions.py', 'session-pool-encoder',
    'h264_udp_packetizer', 'hardware/scrcpy-audio-control', 'hardware/capabilities.json')


class ProbeError(RuntimeError):
    """Fixed labels only; process output and private paths stay in memory."""


class Commands:
    """Drain bounded pipes and reap only the local Popen this object creates.

    Timeout/overflow attempts TERM then KILL of that exact local command. An
    unreaped client stays in pending, and cannot become successful readback.
    These are read-only commands, not gateway/media cleanup authority.
    """
    def __init__(self, *, env=None):
        if env is not None and (type(env) is not dict
                or any(type(k) is not str or type(v) is not str or '\0' in k + v for k, v in env.items())):
            raise ValueError('native_probe_explicit_environment_required')
        # Existing readonly callers retain their behavior. A reviewed private
        # binding can isolate ADB/import/loader/proxy settings for its probes.
        self.env = None if env is None else dict(env)
        self.pending = []
        self.started = self.reaped = 0
        self.terminate_calls = self.kill_calls = 0

    def _stop(self, child):
        try:
            if child.poll() is None:
                child.terminate(); self.terminate_calls += 1
            try:
                child.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                child.kill(); self.kill_calls += 1
                child.wait(timeout=.75)
        except Exception:
            raise ProbeError('native_probe_local_reap_unverified') from None

    def run(self, argv, *, seconds=3, bound=4 * 1024 * 1024, allowed=(0,)):
        if (type(argv) is not tuple or not argv or len(argv) > 32
                or any(type(x) is not str or not x or '\0' in x for x in argv)
                or type(seconds) not in (int, float) or not 0 < seconds <= 5
                or type(bound) is not int or not 1 <= bound <= 4 * 1024 * 1024
                or type(allowed) is not tuple or not allowed or any(type(x) is not int for x in allowed)):
            raise ValueError('native_probe_bounded_command_required')
        child = None; selector = selectors.DefaultSelector(); pipes = []; data = [bytearray(),bytearray()]
        failure = None
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, close_fds=True, bufsize=0, env=self.env)
            self.pending.append(child); self.started += 1
            for index, pipe in enumerate((child.stdout, child.stderr)):
                pipes.append(pipe); os.set_blocking(pipe.fileno(), False); selector.register(pipe, selectors.EVENT_READ, index)
            end = time.monotonic() + seconds
            while selector.get_map():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise ProbeError('native_probe_command_timeout')
                for key, _ in selector.select(min(remaining,.05)):
                    part = os.read(key.fd,4096)
                    if not part:
                        selector.unregister(key.fileobj); continue
                    maximum = bound if key.data == 0 else 4096
                    if len(data[key.data]) + len(part) > maximum:
                        raise ProbeError('native_probe_output_bound')
                    data[key.data].extend(part)
            try:
                code = child.wait(timeout=max(0,end-time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ProbeError('native_probe_command_timeout') from None
            if code not in allowed or data[1]:
                raise ProbeError('native_probe_command_unverified')
            return bytes(data[0]).decode('utf-8','strict')
        except ProbeError as error:
            failure = error; raise
        except Exception:
            failure = ProbeError('native_probe_command_unverified'); raise failure from None
        finally:
            try:
                if child is not None:
                    if failure is not None: self._stop(child)
                    if child.poll() is None: raise ProbeError('native_probe_local_reap_unverified')
                    self.pending.remove(child); self.reaped += 1
            finally:
                selector.close()
                for pipe in pipes: pipe.close()


def inventory(raw):
    """Full ps rows, with argv kept private; malformed/duplicate rows refuse."""
    if type(raw) is not str or not raw.strip(): raise ProbeError('native_probe_inventory_unknown')
    rows = {}
    for line in raw.splitlines():
        match = re.fullmatch(r'\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+(.+)',line)
        if match is None or len(line) > 65536: raise ProbeError('native_probe_inventory_unknown')
        pid,parent,group = map(int,match.group(1,2,3))
        if pid in rows or pid < 1 or parent < 0 or group < 0: raise ProbeError('native_probe_inventory_unknown')
        try: argv = tuple(shlex.split(match[4]))
        except ValueError: raise ProbeError('native_probe_inventory_unknown') from None
        if not argv: raise ProbeError('native_probe_inventory_unknown')
        # macOS ps flattens argv and may omit quotes around paths with spaces.
        # Keep that readonly command witness separately; it is not actual argv.
        rows[pid] = (parent,group,argv,match[4])
    return rows


def roles(rows, pid, guest):
    descendants = set()
    while True:
        fresh = {child for child,(parent,_,_,_) in rows.items() if child != pid and (parent == pid or parent in descendants)} - descendants
        if not fresh: break
        descendants.update(fresh)
    hardware = set(); packetizers = set()
    for child,(_,group,argv,_) in rows.items():
        if child in (pid,os.getpid()): continue
        names = {Path(word).name for word in argv}
        if ('hardware_stream.py' in names and any(argv[i:i+2] == ('--serial','emulator-5556') for i in range(len(argv)-1))) or 'session-pool-encoder' in names:
            hardware.add(group)
        if 'h264_udp_packetizer' in names: packetizers.add(child)
    lines = guest.splitlines()
    if not lines or lines[0].strip() not in ('ARGS','COMMAND') or len(lines) < 2:
        raise ProbeError('native_probe_guest_inventory_unknown')
    controls = sum(bool(re.search(r'(?:^|\s)com\.genymobile\.scrcpy\.Server(?:\s|$)',line)) for line in lines[1:])
    return dict(owned_descendants=len(descendants),hardware_process_groups=len(hardware),
        packetizer_processes=len(packetizers),guest_control_processes=controls)


class MacProbes:
    """Finite original identity/runtime, full known roles and formal TCP reads.

    Expectation/path provenance comes from the independently reviewed private
    launcher. The actual gateway is never discovered or signaled by ps.
    """
    def __init__(self, expected, original, adb, commands=None):
        if (type(expected) is not ExpectedGateway or not isinstance(original,Path) or not original.is_absolute()
                or not isinstance(adb,Path) or not adb.is_absolute()):
            raise ValueError('native_probe_exact_original_paths_required')
        self.expected,self.original,self.adb = expected,original,adb
        self.commands = Commands() if commands is None else commands

    def _start(self):
        raw = self.commands.run(('/bin/ps','-p',str(self.expected.gateway_pid),'-o','lstart='),bound=4096)
        value = re.sub(r'\s+','_',raw.strip())
        if not value or value != self.expected.gateway_start_id:
            raise ProbeError('native_probe_original_identity_changed')
        return value

    def _inventory(self):
        rows = inventory(self.commands.run(('/bin/ps','-axo','pid=,ppid=,pgid=,command=')))
        guest = self.commands.run((str(self.adb),'-s','emulator-5556','shell','ps','-A','-o','ARGS'))
        return rows,guest

    def original_readback(self):
        started = self._start(); hashes = []; cap_raw = None
        for name in RUNTIME_NAMES:
            # Existing symlinked binaries are allowed as readonly dependencies;
            # changing resolved bytes changes the full expected manifest.
            with (self.original/name).open('rb') as stream: raw = stream.read(32*1024*1024+1)
            if not raw or len(raw) > 32*1024*1024: raise ProbeError('native_probe_original_runtime_unknown')
            hashes.append((name,hashlib.sha256(raw).hexdigest()))
            if name == RUNTIME_NAMES[-1]: cap_raw = raw
        if (hashes[0][1] != self.expected.gateway_source_sha256 or
                hashlib.sha256(json.dumps(hashes,separators=(',',':')).encode()).hexdigest() != self.expected.runtime_manifest_sha256):
            raise ProbeError('native_probe_original_runtime_changed')
        try: cap = json.loads(cap_raw)
        except (ValueError,UnicodeError): raise ProbeError('native_probe_original_capabilities_unknown') from None
        if type(cap) is not dict or cap.get('touch_cancel_clears_pointer_state') is not True or cap.get('guest_jar_sha256') != dict(hashes)['hardware/scrcpy-audio-control']:
            raise ProbeError('native_probe_original_capabilities_unknown')
        rows,guest = self._inventory()
        row = rows.get(self.expected.gateway_pid)
        if row is None or re.search(r'(?:^|\s)'+re.escape(str(self.original/RUNTIME_NAMES[0]))+r'(?:\s|$)',row[3]) is None:
            raise ProbeError('native_probe_original_executing_source_unknown')
        self._start()
        if self.commands.pending or self.commands.started != self.commands.reaped:
            raise ProbeError('native_probe_local_reap_unverified')
        return dict(vars(self.expected),coverage_complete=True,**roles(rows,self.expected.gateway_pid,guest))

    def formal_clear(self):
        for port in (15556,15558):
            # Readonly; never lsof -> kill, no claimed permission by a snapshot.
            raw = self.commands.run(('/usr/sbin/lsof','-nP','-iTCP:'+str(port),'-sTCP:ESTABLISHED','-t'),allowed=(0,1),bound=4096)
            if raw.strip(): return False
        return True

    def owned_roles(self, child):
        if type(child) is not subprocess.Popen or type(child.poll()) is not int:
            raise ProbeError('native_probe_same_exited_Popen_required')
        rows,guest = self._inventory()
        if child.pid in rows: raise ProbeError('native_probe_owned_PID_present_or_reused')
        if self.commands.pending or self.commands.started != self.commands.reaped:
            raise ProbeError('native_probe_local_reap_unverified')
        return dict(gateway_pid=child.pid,coverage_complete=True,**roles(rows,child.pid,guest))
