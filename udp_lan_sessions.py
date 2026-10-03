"""Account-bound, bounded lifecycle for isolated authenticated UDP paths.

The HTTPS adapter authenticates the account and validates the selected network peer
before calling ``create``. It must call READY/ALIVE only after authenticating and
replay-checking the session's UDP packet. This module neither accepts passwords
nor opens sockets, files or subprocesses. A worker owns only its own resources;
``start`` launches promptly, ``stop`` revokes touch and closes those resources.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass, field
import ipaddress
import secrets
import threading
import time
from typing import Callable, Protocol

from stream_settings import parse_bitrate_mode, parse_max_fps, parse_settings
from udp_network_scope import TAILNET_HOST, rfc1918_address
from udp_nps_profile import (Endpoint, NPS_OWNER_SCOPE, owner_profile,
                             planned_profile, validate_public_endpoints)


_NPS_OWNER_SETTING_FIELDS = frozenset({
    'network_scope', 'node', 'max_size', 'video_bit_rate', 'bitrate_mode',
    'max_fps', 'buffer_ms', 'seconds', 'surface_submit_lead_ms',
    'audio', 'audio_enabled', 'touch', 'touch_enabled',
})


class SessionError(Exception):
    """Safe error codes: never interpolate a worker error or credential."""

    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


class Worker(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


def trusted_nps_owner_accounts(accounts=None) -> tuple[str, ...]:
    """Closed server configuration, never an HTTP/session setting.

    Default admission remains the original owner account. Explicit ``huoguo``
    admission is an owner credential trial, not friend isolation acceptance.
    Copy and canonicalize the sequence so caller mutation/order cannot change
    either the registry's policy or its agreement with the HTTPS handler.
    """
    if accounts is None:
        return ('wyw',)
    if type(accounts) not in (tuple, list):
        raise ValueError('bounded_trusted_NPS_owner_accounts_required')
    accounts = tuple(accounts)
    if not 1 <= len(accounts) <= 2:
        raise ValueError('bounded_trusted_NPS_owner_accounts_required')
    if any(type(account) is not str or account not in ('wyw', 'huoguo')
           for account in accounts) or len(set(accounts)) != len(accounts):
        raise ValueError('bounded_trusted_NPS_owner_accounts_required')
    return tuple(sorted(accounts))


def parse_udp_settings(settings: dict) -> dict:
    """Use the formal stream parsers, then narrow the isolated UDP candidate."""
    if not isinstance(settings, dict):
        raise ValueError('Invalid settings object')
    normalized = dict(settings)
    scope = normalized.get('network_scope', 'lan')
    if type(scope) is not str or scope not in ('lan', 'tailnet', NPS_OWNER_SCOPE):
        raise ValueError('Explicit supported UDP network scope required')
    node = None
    if scope == NPS_OWNER_SCOPE:
        if set(settings) - _NPS_OWNER_SETTING_FIELDS:
            raise ValueError('Closed NPS owner settings required')
        # This only validates the identity. The trusted registry constructor
        # controls M5 opt-in, guest bindings and account admission separately.
        node = planned_profile(settings.get('node')).node
    normalized.setdefault('max_size', 1280)
    normalized.setdefault('video_bit_rate', 8_000_000)
    normalized.setdefault('bitrate_mode', 'VBR')
    size, bitrate = parse_settings(normalized, 1280)
    mode, android_mode = parse_bitrate_mode(normalized)
    fps = parse_max_fps(normalized)
    if size not in (960, 1280, 1920):
        raise ValueError('LAN UDP supports 540P, 720P and 1080P')
    if fps not in (30, 60, 120):
        raise ValueError('UDP supports 30, 60 and 120 FPS limits')
    buffer_ms = settings.get('buffer_ms', 80)
    seconds = settings.get('seconds', 120)
    if type(buffer_ms) is not int or not 30 <= buffer_ms <= 100:
        raise ValueError('Buffer must be 30 to 100 ms')
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError('Session duration must be 1 to 120 seconds')
    lead_ms = settings.get('surface_submit_lead_ms', 0)
    if type(lead_ms) is not int or lead_ms not in (0, 16):
        raise ValueError('Owner Surface experiment supports only 0 or 16 ms')
    toggles = {}
    for name in ('audio', 'touch'):
        long_name = name + '_enabled'
        short_present, long_present = name in settings, long_name in settings
        value = settings.get(long_name, settings.get(name, True))
        if (type(value) is not bool
                or (short_present and type(settings[name]) is not bool)):
            raise ValueError(name + ' must be a boolean')
        if short_present and long_present and settings[name] != value:
            raise ValueError('Conflicting ' + name + ' options')
        toggles[long_name] = value
    result = {
        'max_size': size, 'video_bit_rate': bitrate,
        'bitrate_mode': mode, 'android_bitrate_mode': android_mode,
        'max_fps': fps, 'fps': fps, 'buffer_ms': buffer_ms, 'seconds': seconds,
        'network_scope': scope,
        'surface_submit_lead_ms': lead_ms,
        **toggles,
    }
    if scope == NPS_OWNER_SCOPE:
        result['node'] = node
    return result


@dataclass(repr=False)
class _Session:
    account: str
    config: dict = field(repr=False)
    created: float
    ready_deadline: float
    phase: str = 'building'
    worker: Worker | None = field(default=None, repr=False)
    ready_at: float | None = None
    alive_at: float | None = None
    hard_deadline: float | None = None
    starting: bool = False
    stop_called: bool = False
    stop_done: bool = False
    factory_finished: bool = False


class UdpLanSessions:
    """One non-displacing reservation, with cleanup outside the registry lock.

    Call ``reap`` from the gateway's existing bounded service loop. All methods
    also expire stale reservations before acting. An expired owner can repeat a
    DELETE briefly; another account or an unknown id always receives ``False``.
    Config dictionaries contain ephemeral keys: do not log or persist them.
    """

    READY_SECONDS = 10.0
    ALIVE_SECONDS = 3.0
    TOMBSTONE_SECONDS = 300.0
    MAX_TOMBSTONES = 256

    def __init__(self, peer_host: str, peer_port: int = 45963,
                 clock: Callable[[], float] = time.monotonic, *,
                 network_scope: str = 'lan', scope_guard: Callable[[], bool] | None = None,
                 allow_owner_surface_submit_lead: bool = False,
                 node: str | None = None, allow_m5_owner_trial: bool = False,
                 guest_serial: str | None = None, guest_avd: str | None = None,
                 owner_accounts: tuple[str, ...] | list[str] | None = None):
        try:
            address = ipaddress.IPv4Address(peer_host)
        except (ipaddress.AddressValueError, TypeError):
            raise ValueError('LAN peer_host must be a literal IPv4 address') from None
        if network_scope not in ('lan', 'tailnet', NPS_OWNER_SCOPE):
            raise ValueError('Invalid UDP network scope')
        if network_scope == 'lan' and not rfc1918_address(str(address)):
            raise ValueError('LAN peer_host must be a private LAN IPv4 address')
        if network_scope == 'tailnet' and str(address) != TAILNET_HOST:
            raise ValueError('Exact M1 Tailnet host required')
        if network_scope == 'tailnet' and not callable(scope_guard):
            raise ValueError('Tailnet requires a current verified scope guard')
        if type(peer_port) is not int or not 1 <= peer_port <= 65535:
            raise ValueError('Invalid UDP port')
        if type(allow_owner_surface_submit_lead) is not bool:
            raise ValueError('Owner Surface experiment opt-in must be a boolean')
        if type(allow_m5_owner_trial) is not bool:
            raise ValueError('Trusted M5 owner trial opt-in must be a boolean')
        self._nps_profile = None
        self._worker_backend = {}
        self._owner_accounts = None
        if network_scope == NPS_OWNER_SCOPE:
            self._owner_accounts = trusted_nps_owner_accounts(owner_accounts)
            profile = owner_profile(node, allow_m5_owner_trial=allow_m5_owner_trial)
            if not callable(scope_guard):
                raise ValueError('NPS owner requires a current verified scope guard')
            if type(peer_host) is not str or peer_host != profile.public_media.host:
                raise ValueError('Exact NPS public IPv4 required')
            validate_public_endpoints(profile, profile.public_control,
                Endpoint(peer_host, peer_port, 'udp'), network_scope=network_scope,
                proxy_protocol=0, allow_m5_owner_trial=allow_m5_owner_trial)
            if (type(guest_serial) is not str or type(guest_avd) is not str
                    or guest_serial != profile.guest_serial or guest_avd != profile.guest_avd):
                raise ValueError('Exact trusted node guest binding required')
            self._nps_profile = profile
            self._worker_backend = dict(local_bind_host=profile.local_udp.host,
                local_bind_port=profile.local_udp.port,
                guest_serial=guest_serial, guest_avd=guest_avd)
        elif (node is not None or allow_m5_owner_trial or guest_serial is not None
              or guest_avd is not None or owner_accounts is not None):
            raise ValueError('NPS owner parameters require explicit NPS owner scope')
        self._peer_host, self._peer_port, self._clock = str(address), peer_port, clock
        self._network_scope, self._scope_guard = network_scope, scope_guard
        self._allow_owner_surface_submit_lead = allow_owner_surface_submit_lead
        self._lock = threading.RLock()
        self._active: _Session | None = None
        self._tombstones: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._pending_cleanup: list[_Session] = []
        self._closed = False
        self._draining = False
        self._stop_failures = 0
        self._cleanup_failed = False
        # Payload-free completion signal, distinct from revoked/closed access.
        # Closing a reservation does not set this until factory/start/stop have
        # returned and its exactly-once owned cleanup has finished.
        self._quiescent = threading.Event()
        self._quiescent.set()

    @property
    def owner_accounts(self) -> tuple[str, ...] | None:
        """Immutable trusted policy; absent in unchanged LAN/Tailnet scopes."""
        return self._owner_accounts

    def _owner_account_allowed(self, account: str) -> bool:
        return self._owner_accounts is None or account in self._owner_accounts

    def begin_idle_drain(self) -> bool:
        """Trusted in-process maintenance gate; never an HTTP operation.

        Reserve idle against ``create`` under the same lock. Do not reap,
        expire, revoke or stop a reservation to make this check succeed. Busy
        or unconfirmed cleanup leaves admission unchanged. Successful drain is
        sticky and idempotent, including a later ordinary idle ``close``.
        This boolean is only this live registry's result, not a process/port
        handover receipt or permission to stop any other service.
        """
        with self._lock:
            if (self._active is not None or self._pending_cleanup
                    or self._cleanup_failed or not self._quiescent.is_set()):
                return False
            if self._draining:
                return True
            if self._closed:
                return False
            self._draining = True
            return True

    def _scope_ok(self) -> bool:
        """Read an in-memory verifier state; never do IPC on the UDP hot path.

        The gateway refreshes that state outside this registry and closes the
        owned registry when verification fails. Until cleanup completes this
        gate prevents READY, ALIVE and new touch writes from extending access.
        """
        try:
            return self._scope_guard is None or self._scope_guard() is True
        except Exception:
            return False

    @staticmethod
    def _account(account: str) -> None:
        if type(account) is not str or not account or len(account) > 128:
            raise SessionError(401, 'account_required')

    def _prune_locked(self, now: float) -> None:
        for sid, (_, deadline) in list(self._tombstones.items()):
            if now >= deadline:
                del self._tombstones[sid]
        while len(self._tombstones) > self.MAX_TOMBSTONES:
            self._tombstones.popitem(last=False)

    def _release_locked(self, record: _Session) -> None:
        if (record.phase == 'closed' and record.factory_finished
                and not record.starting
                and (record.worker is None or record.stop_done)
                and self._active is record):
            self._active = None
            self._quiescent.set()

    def _stop_locked(self, record: _Session) -> _Session | None:
        if (record.phase == 'closed' and record.worker is not None
                and not record.starting and not record.stop_called):
            record.stop_called = True
            return record
        return None

    def _close_locked(self, record: _Session, now: float) -> _Session | None:
        record.phase = 'closed'  # Immediately prevents any touch permission.
        self._tombstones[record.config['session']] = (
            record.account, now + self.TOMBSTONE_SECONDS)
        self._prune_locked(now)
        self._release_locked(record)
        return self._stop_locked(record)

    def _expire_locked(self, now: float) -> tuple[int, _Session | None]:
        self._prune_locked(now)
        record = self._active
        if record is None or record.phase == 'closed':
            return 0, None
        if record.phase in ('building', 'waiting'):
            expired = now >= record.ready_deadline
        else:
            expired = (now >= record.hard_deadline
                       or now - record.alive_at >= self.ALIVE_SECONDS)
        if expired:
            return 1, self._close_locked(record, now)
        return 0, None

    def _stop(self, record: _Session | None) -> None:
        if record is None:
            return
        try:
            record.worker.stop()
        except Exception:
            # A stop error must not expose a worker command, key or account.
            with self._lock:
                self._stop_failures += 1
                self._cleanup_failed = True
        finally:
            with self._lock:
                record.stop_done = True
                self._release_locked(record)

    def reap(self) -> int:
        with self._lock:
            expired, worker = self._expire_locked(self._clock())
            pending, self._pending_cleanup = self._pending_cleanup, []
            if worker is not None:
                pending.append(worker)
        for record in pending:
            self._stop(record)
        return expired

    def create(self, account: str, settings: dict,
               factory: Callable[[dict], Worker]) -> dict:
        self._account(account)
        if not self._owner_account_allowed(account):
            raise SessionError(403, 'nps_owner_account_required')
        try:
            options = parse_udp_settings(settings)
        except ValueError:
            raise SessionError(400, 'invalid_udp_settings') from None
        if options['network_scope'] != self._network_scope:
            raise SessionError(400, 'udp_network_scope_mismatch')
        if self._nps_profile is not None and options.get('node') != self._nps_profile.node:
            raise SessionError(400, 'udp_node_profile_mismatch')
        if options['surface_submit_lead_ms'] != 0 and not self._allow_owner_surface_submit_lead:
            raise SessionError(400, 'owner_surface_submit_experiment_not_enabled')
        if not self._scope_ok():
            raise SessionError(503, 'udp_network_scope_unavailable')
        self.reap()
        with self._lock:
            if self._closed:
                raise SessionError(503, 'registry_closed')
            if self._draining:
                raise SessionError(503, 'udp_registry_draining')
            if self._cleanup_failed:
                raise SessionError(503, 'udp_cleanup_failed')
            if self._active is not None:
                raise SessionError(409, 'udp_session_busy')
            sid = secrets.token_hex(16)
            while sid in self._tombstones:
                sid = secrets.token_hex(16)
            config = {
                'protocol': 'HGUE_UDP_V1', 'session': sid,
                'key_b64': base64.b64encode(secrets.token_bytes(32)).decode('ascii'),
                'session_tag_hex': f'{secrets.randbits(64):016x}',
                'peer_host': self._peer_host, 'peer_port': self._peer_port,
                'bind_port': 0, **options,
                'video_release': 'scheduled', 'async_video': True,
                'decoder_reanchor_enabled': True,
                # A 30 FPS media cap must not require a 120 Hz phone panel.
                # Zero is the candidate client's soft display hint; it is not
                # a request to lower the guest or phone refresh rate to 30 Hz.
                'diagnostic_events': False,
                'display_hz': 0 if options['fps'] == 30 else 120,
                'network_feedback': True,
            }
            now = self._clock()
            record = _Session(account, config, now, now + self.READY_SECONDS)
            self._active = record
            self._quiescent.clear()
        worker = None
        try:
            # Only the owned factory sees server-selected loopback/guest data.
            # The public descriptor and lifecycle record remain projection-only.
            worker = factory({**config, **self._worker_backend})
            if not callable(getattr(worker, 'start', None)) or not callable(getattr(worker, 'stop', None)):
                raise TypeError('Worker lifecycle required')
        except Exception:
            with self._lock:
                record.factory_finished = True
                if callable(getattr(worker, 'stop', None)):
                    record.worker = worker
                cleanup = self._close_locked(record, self._clock())
            self._stop(cleanup)
            raise SessionError(503, 'udp_worker_unavailable') from None
        with self._lock:
            record.worker = worker
            record.factory_finished = True
            now = self._clock()
            scope_ok = self._scope_ok()
            if not scope_ok or record.phase == 'closed' or now >= record.ready_deadline:
                cleanup = self._close_locked(record, now)
                accepted = False
            else:
                record.phase = 'waiting'
                cleanup, accepted = None, True
        self._stop(cleanup)
        if not accepted:
            if not scope_ok:
                raise SessionError(503, 'udp_network_scope_unavailable')
            raise SessionError(409, 'udp_session_revoked')
        return dict(config)

    def authenticated_ready(self, session_id: str) -> bool:
        """Only the already authenticated/replay-checked UDP READY may call this."""
        self.reap()
        with self._lock:
            if not self._scope_ok():
                return False
            record = self._active
            if record is None or record.config['session'] != session_id:
                return False
            if record.phase in ('starting', 'active'):
                # Repeated READY is not a heartbeat and cannot extend either lease.
                return True
            if record.phase != 'waiting':
                return False
            now = self._clock()
            record.ready_at = record.alive_at = now
            record.hard_deadline = now + record.config['seconds']
            record.phase, record.starting = 'starting', True
            worker = record.worker
        start_ok = True
        try:
            worker.start()
        except Exception:
            start_ok = False
        with self._lock:
            record.starting = False
            now = self._clock()
            if not start_ok or not self._scope_ok() or record.phase == 'closed':
                cleanup = self._close_locked(record, now)
                accepted = False
            elif (now >= record.hard_deadline
                  or now - record.alive_at >= self.ALIVE_SECONDS):
                cleanup = self._close_locked(record, now)
                accepted = False
            else:
                record.phase = 'active'
                cleanup, accepted = None, True
        self._stop(cleanup)
        return accepted

    def authenticated_alive(self, session_id: str) -> bool:
        """A authenticated ALIVE may renew silence only, never the hard deadline."""
        self.reap()
        with self._lock:
            if not self._scope_ok():
                return False
            record = self._active
            if (record is None or record.config['session'] != session_id
                    or record.phase not in ('starting', 'active')):
                return False
            record.alive_at = self._clock()
            return True

    def touch_allowed(self, session_id: str) -> bool:
        self.reap()
        with self._lock:
            record = self._active
            return bool(self._scope_ok() and record is not None and record.config['session'] == session_id
                        and record.phase == 'active' and record.config['touch_enabled'])

    def dispatch_touch(self, session_id: str, write: Callable[[], None]) -> bool:
        """Linearize a bounded owned control write against revocation.

        ``write`` is trusted local code with a short I/O timeout, never an HTTP
        supplied callable. It may raise; the caller handles failure after this
        method releases its lock. Do not revoke or stop resources inside write.
        A previously admitted write completes before revoke, and no later write
        can enter after revoke. True cleanup ACTION_CANCEL uses the separate
        trusted worker cleanup path, because it must clear an abandoned gesture.
        Expiry discovered on an input-writer thread is revoked immediately but
        owned cleanup is handed to the existing service reaper, so that cleanup
        cannot join that writer from within its own dispatch call. The closed
        reservation stays busy until that exactly-once cleanup has completed.
        """
        if not callable(write):
            raise TypeError('Owned touch write required')
        with self._lock:
            _, cleanup = self._expire_locked(self._clock())
            if cleanup is not None:
                self._pending_cleanup.append(cleanup)
            record = self._active
            accepted = bool(self._scope_ok() and record is not None and record.config['session'] == session_id
                            and record.phase == 'active' and record.config['touch_enabled'])
            if accepted:
                write()
        return accepted

    def cancel(self, account: str, session_id: str) -> bool:
        self._account(account)
        if not self._owner_account_allowed(account):
            return False
        self.reap()
        with self._lock:
            record = self._active
            if record is not None and record.config['session'] == session_id:
                if record.account != account:
                    return False
                worker = self._close_locked(record, self._clock())
                result = True
            else:
                worker = None
                result = (session_id in self._tombstones
                          and self._tombstones[session_id][0] == account)
        self._stop(worker)
        return result

    def revoke(self, session_id: str) -> bool:
        """Trusted worker callback for authenticated STOP or owned pipeline failure.

        Never expose this account-free method as an HTTP route. Public DELETE
        uses ``cancel`` with the authenticated account. A known expired session
        is harmlessly idempotent, whereas an unknown id returns ``False``.
        """
        self.reap()
        with self._lock:
            record = self._active
            if record is not None and record.config['session'] == session_id:
                worker = self._close_locked(record, self._clock())
                result = True
            else:
                worker = None
                result = session_id in self._tombstones
        self._stop(worker)
        return result

    def status(self, account: str, session_id: str) -> dict | None:
        self._account(account)
        if not self._owner_account_allowed(account):
            return None
        self.reap()
        with self._lock:
            if not self._scope_ok():
                return None
            record = self._active
            if (record is None or record.config['session'] != session_id
                    or record.account != account or record.phase == 'closed'):
                return None
            now = self._clock()
            deadline = (record.ready_deadline if record.phase in ('building', 'waiting')
                        else min(record.hard_deadline,
                                 record.alive_at + self.ALIVE_SECONDS))
            return {'session': session_id, 'phase': record.phase,
                    'expires_in_ms': max(0, round((deadline - now) * 1000))}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            worker = (self._close_locked(self._active, self._clock())
                      if self._active is not None else None)
            pending, self._pending_cleanup = self._pending_cleanup, []
            if worker is not None:
                pending.append(worker)
        for record in pending:
            self._stop(record)

    def close_and_wait(self, timeout: float = 45.0) -> dict:
        """Gateway-exit barrier; wait without holding the registry lock.

        Ordinary revoke/DELETE remain promptly idempotent. At process shutdown
        an already-running stop cannot be abandoned merely because another
        caller has claimed stop_called. If this caller claims cleanup, run that
        exactly-once operation on one owned daemon while this caller observes
        a bounded completion signal. Timeout is failure, never successful exit.
        A pending factory/start keeps the reservation until its existing caller
        completes cleanup; no duplicate worker.stop or cleanup thread is made.
        """
        if (type(timeout) not in (int, float) or not 0 < timeout <= 60):
            raise ValueError('Bounded shutdown timeout required')
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
            worker = (self._close_locked(self._active, self._clock())
                      if self._active is not None else None)
            pending, self._pending_cleanup = self._pending_cleanup, []
            if worker is not None:
                pending.append(worker)
        if pending:
            def finish_owned():
                for record in pending:
                    self._stop(record)
            cleanup = threading.Thread(target=finish_owned,
                                       name='owned_udp_registry_shutdown', daemon=True)
            try:
                cleanup.start()
            except Exception:
                with self._lock:
                    self._stop_failures += 1
                    self._cleanup_failed = True
                raise SessionError(503, 'udp_shutdown_worker_unavailable') from None
        # Event.wait releases no registry-owned lock because none is held here.
        if not self._quiescent.wait(max(0.0, deadline-time.monotonic())):
            raise SessionError(503, 'udp_shutdown_quiescence_timeout')
        with self._lock:
            if self._cleanup_failed:
                raise SessionError(503, 'udp_cleanup_failed')
            return {'quiescence_confirmed': True, 'stop_failures': self._stop_failures}
