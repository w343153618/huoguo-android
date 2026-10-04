"""Explicit owned-Popen lifecycle for a trusted M1 LAN native coordinator.

Construction is inert; there is no CLI, credential reader or production toggle.
The trusted caller still supplies qualified frozen argv, private evidence and
fresh operator/device/artifact permission. JSON cannot supply that provenance.
Unknown cleanup keeps the admission object and reservation alive. The caller
must keep this supervisor alive too; process exit cannot preserve an OS socket.
"""
import json
import math
from pathlib import Path
import subprocess
import threading
import time

from scripts.probes.owner_lan_admission import OwnerLanAdmission, validate_readback
from scripts.probes.owner_native_window import Window


OUTPUT_BYTES = 65536
ROLE_FIELDS = frozenset(('owned_descendants', 'hardware_process_groups',
    'packetizer_processes', 'guest_control_processes'))
SHUTDOWN_FIELDS = frozenset(('event', 'quiescence_confirmed', 'stop_failures',
    'owned_reaper_exit_confirmed'))


class LifecycleError(RuntimeError):
    """Closed local error labels; never include argv, stderr or callback text."""


def native_shutdown(raw):
    """Exactly one closed successful native footer; not ownership by itself."""
    if type(raw) is not bytes or not 0 < len(raw) <= OUTPUT_BYTES:
        raise LifecycleError('native_gateway_output_unverified')
    found = []
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate')
                value[key] = item
            return value
        for line in raw.decode('utf-8', 'strict').splitlines():
            if not line.strip():
                continue
            value = json.loads(line, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            if type(value) is not dict:
                raise ValueError('object')
            if value.get('event') == 'candidate_shutdown':
                found.append(value)
    except (ValueError, UnicodeError, RecursionError):
        raise LifecycleError('native_gateway_output_unverified') from None
    if (len(found) != 1 or set(found[0]) != SHUTDOWN_FIELDS
            or found[0]['quiescence_confirmed'] is not True
            or type(found[0]['stop_failures']) is not int or found[0]['stop_failures'] != 0
            or found[0]['owned_reaper_exit_confirmed'] is not True):
        raise LifecycleError('native_gateway_shutdown_unverified')
    return found[0]


def _owned_roles(value, process):
    if (type(value) is not dict or set(value) != ROLE_FIELDS | {'gateway_pid', 'coverage_complete'}
            or value['coverage_complete'] is not True
            or type(value['gateway_pid']) is not int or value['gateway_pid'] != process.pid
            or any(type(value[k]) is not int or value[k] != 0 for k in ROLE_FIELDS)):
        raise LifecycleError('native_gateway_owned_roles_unverified')


class _Drain:
    """Always drain, retain a bounded prefix, and require actual EOF/join."""
    def __init__(self, stream):
        self.stream, self.data = stream, bytearray()
        self.overflow = self.failed = False
        self.thread = threading.Thread(target=self._read, daemon=False)

    def _read(self):
        try:
            while True:
                part = self.stream.read(4096)
                if not part:
                    return
                space = OUTPUT_BYTES - len(self.data)
                self.data.extend(part[:space])
                if len(part) > space:
                    self.overflow = True
        except Exception:
            self.failed = True
        finally:
            try:
                self.stream.close()
            except Exception:
                self.failed = True


class OwnedGateway:
    """No PID adoption or destructor release; only the Popen made by start().

    Callbacks are trusted fresh probes, not externally supplied true fields.
    The full original guard does reserve/witness/identity/role checks first;
    formal_clear is repeated before mark and again before final release.
    """
    def __init__(self, window, admission, formal_clear, owned_roles):
        if type(window) is not Window or type(admission) is not OwnerLanAdmission:
            raise ValueError('native_gateway_trusted_local_selection_required')
        window.__post_init__()
        if not callable(formal_clear) or not callable(owned_roles):
            raise ValueError('native_gateway_trusted_probes_required')
        self.window, self.admission = window, admission
        self.formal_clear, self.owned_roles = formal_clear, owned_roles
        self._process = None
        self.drains = []
        self.state = 'new'
        self.signals = 0
        self.kill_calls = 0
        self.deadline = None
        self.projection_accepted = False

    @property
    def process(self):
        return self._process  # No public PID/Popen adoption setter.

    def _formal(self):
        try:
            if self.formal_clear() is not True:
                raise ValueError('busy')
        except Exception:
            raise LifecycleError('native_gateway_formal_unverified') from None

    def start(self, argv, *, cwd, env=None):
        # No shell, supplied PID, log path, implicit input or imported Popen.
        if (self.state != 'new' or type(argv) is not tuple or not 1 <= len(argv) <= 64
                or any(type(x) is not str or not x or '\0' in x for x in argv)
                or sum(len(x) for x in argv) > 8192
                or not isinstance(cwd, Path) or not cwd.is_absolute()
                or env is not None and (type(env) is not dict or any(
                    type(k) is not str or type(v) is not str or '\0' in k + v for k,v in env.items()))):
            raise ValueError('native_gateway_qualified_launch_required')
        self.admission.__enter__()
        try:
            self._formal()
        except BaseException:
            self.admission.close()  # No mark/Popen yet; no owned work exists.
            raise
        self.admission.mark_owned_lan_starting()
        self.state = 'starting'
        self.deadline = time.monotonic() + self.window.plan.process_max_seconds + 47
        try:
            self._process = subprocess.Popen(argv, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, close_fds=True, bufsize=0)
            # Plan is finite gateway lifetime plus existing registry45/reaper2.
            for pipe in (self.process.stdout, self.process.stderr):
                drain = _Drain(pipe)
                self.drains.append(drain)
                drain.thread.start()
            self.state = 'running'
        except BaseException:
            self.state = 'cleanup_required'
            raise LifecycleError('native_gateway_start_or_drain_unverified') from None
        return self.process.pid

    def wait(self, timeout=None):
        """Bounded wait only; timeout does not signal or release the guard."""
        if self.process is None or self.state == 'released':
            raise LifecycleError('native_gateway_owned_Popen_required')
        remaining = max(0.0, self.deadline - time.monotonic())
        if timeout is not None:
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= remaining:
                raise ValueError('native_gateway_bounded_wait_required')
            remaining = timeout
        try:
            result = self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            self.state = 'cleanup_required'
            return None
        except Exception:
            self.state = 'cleanup_required'
            raise LifecycleError('native_gateway_wait_unverified') from None
        self.state = 'exited'
        return result

    def terminate_owned(self):
        """Only this unreaped Popen; no ps PID, process group or old service."""
        if self.process is None or self.state == 'released':
            raise LifecycleError('native_gateway_owned_Popen_required')
        if self.process.poll() is not None:
            return False
        try:
            self.process.terminate()
            self.signals += 1
        except Exception:
            self.state = 'cleanup_required'
            raise LifecycleError('native_gateway_owned_terminate_unverified') from None
        return True  # A method return is not child exit or remote quiescence.

    def kill_owned(self):
        """Explicit escalation for this still-owned Popen only.

        Forced exit does not satisfy finish(): a nonzero exit or missing
        native footer retains the reservation. No group/PID adoption API.
        """
        if self.process is None or self.state == 'released':
            raise LifecycleError('native_gateway_owned_Popen_required')
        if self.process.poll() is not None:
            return False
        try:
            self.process.kill()
            self.kill_calls += 1
        except Exception:
            self.state = 'cleanup_required'
            raise LifecycleError('native_gateway_owned_kill_unverified') from None
        self.state = 'cleanup_required'
        return True

    def finish(self):
        """Actual exit+EOF+footer+full roles+fresh original before five fields."""
        if self.process is None or self.state == 'released':
            raise LifecycleError('native_gateway_owned_Popen_required')
        try:
            code = self.process.poll()
            if type(code) is not int or code != 0 or self.kill_calls:
                raise LifecycleError('native_gateway_exit_unverified')
            deadline = time.monotonic() + 2
            try:
                for drain in self.drains:
                    drain.thread.join(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                raise LifecycleError('native_gateway_output_EOF_unverified') from None
            if (len(self.drains) != 2 or any(d.thread.is_alive() or d.failed or d.overflow for d in self.drains)):
                raise LifecycleError('native_gateway_output_EOF_unverified')
            native_shutdown(bytes(self.drains[0].data))
            try:
                _owned_roles(self.owned_roles(self.process), self.process)
                validate_readback(self.admission.readback(), self.admission.expected)
            except Exception:
                raise LifecycleError('native_gateway_postcheck_unverified') from None
            self._formal()
            # Existing guard schema is not relaxed to accept a native footer.
            if self.projection_accepted:
                if self.admission.state != 'quiescent':
                    raise LifecycleError('native_gateway_release_retry_state_unverified')
            else:
                self.admission.confirm_owned_lan_quiescence(dict(event='candidate_shutdown',
                    quiescence_confirmed=True, stop_failures=0, gateway_exit_confirmed=True,
                    owned_media_exit_confirmed=True))
                self.projection_accepted = True
            self.admission.close()
        except BaseException:
            self.state = 'cleanup_required'
            raise
        self.state = 'released'
        return dict(event='native_gateway_parent_receipt', gateway_pid=self.process.pid,
            gateway_exit_observed=code, stdout_and_stderr_EOF_confirmed=True,
            owned_roles_and_original_postcheck_verified=True, reservation_release_confirmed=True,
            parent_gateway_terminate_calls=self.signals, exit_zero_no_parent_terminate_call=self.signals == 0 and self.kill_calls == 0,
            parent_gateway_kill_calls=self.kill_calls,
            phone_ART_native_producer_or_full_pipeline_acceptance=False)
