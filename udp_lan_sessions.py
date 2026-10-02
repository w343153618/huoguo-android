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


class SessionError(Exception):
    """Safe error codes: never interpolate a worker error or credential."""

    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


class Worker(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


def parse_udp_settings(settings: dict) -> dict:
    """Use the formal stream parsers, then narrow the isolated UDP candidate."""
    if not isinstance(settings, dict):
        raise ValueError('Invalid settings object')
    normalized = dict(settings)
    scope = normalized.get('network_scope', 'lan')
    if type(scope) is not str or scope not in ('lan', 'tailnet'):
        raise ValueError('Explicit supported UDP network scope required')
    normalized.setdefault('max_size', 1280)
    normalized.setdefault('video_bit_rate', 8_000_000)
    normalized.setdefault('bitrate_mode', 'VBR')
    size, bitrate = parse_settings(normalized, 1280)
    mode, android_mode = parse_bitrate_mode(normalized)
    fps = parse_max_fps(normalized)
    if size not in (960, 1280, 1920):
        raise ValueError('LAN UDP supports 540P, 720P and 1080P')
    if fps not in (60, 120):
        raise ValueError('LAN UDP supports 60 and 120 FPS limits')
    buffer_ms = settings.get('buffer_ms', 80)
    seconds = settings.get('seconds', 120)
    if type(buffer_ms) is not int or not 30 <= buffer_ms <= 100:
        raise ValueError('Buffer must be 30 to 100 ms')
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError('Session duration must be 1 to 120 seconds')
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
    return {
        'max_size': size, 'video_bit_rate': bitrate,
        'bitrate_mode': mode, 'android_bitrate_mode': android_mode,
        'max_fps': fps, 'fps': fps, 'buffer_ms': buffer_ms, 'seconds': seconds,
        'network_scope': scope,
        **toggles,
    }


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

    def __init__(self, peer_host: str, peer_port: int = 15963,
                 clock: Callable[[], float] = time.monotonic, *,
                 network_scope: str = 'lan', scope_guard: Callable[[], bool] | None = None):
        try:
            address = ipaddress.IPv4Address(peer_host)
        except (ipaddress.AddressValueError, TypeError):
            raise ValueError('LAN peer_host must be a literal IPv4 address') from None
        if network_scope not in ('lan', 'tailnet'):
            raise ValueError('Invalid UDP network scope')
        if network_scope == 'lan' and not rfc1918_address(str(address)):
            raise ValueError('LAN peer_host must be a private LAN IPv4 address')
        if network_scope == 'tailnet' and str(address) != TAILNET_HOST:
            raise ValueError('Exact M1 Tailnet host required')
        if network_scope == 'tailnet' and not callable(scope_guard):
            raise ValueError('Tailnet requires a current verified scope guard')
        if type(peer_port) is not int or not 1 <= peer_port <= 65535:
            raise ValueError('Invalid UDP port')
        self._peer_host, self._peer_port, self._clock = str(address), peer_port, clock
        self._network_scope, self._scope_guard = network_scope, scope_guard
        self._lock = threading.RLock()
        self._active: _Session | None = None
        self._tombstones: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._pending_cleanup: list[_Session] = []
        self._closed = False
        self._stop_failures = 0
        self._cleanup_failed = False

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
        try:
            options = parse_udp_settings(settings)
        except ValueError:
            raise SessionError(400, 'invalid_udp_settings') from None
        if options['network_scope'] != self._network_scope:
            raise SessionError(400, 'udp_network_scope_mismatch')
        if not self._scope_ok():
            raise SessionError(503, 'udp_network_scope_unavailable')
        self.reap()
        with self._lock:
            if self._closed:
                raise SessionError(503, 'registry_closed')
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
                'decoder_reanchor_enabled': True, 'surface_submit_lead_ms': 0,
                'diagnostic_events': False, 'display_hz': 120,
                'network_feedback': True,
            }
            now = self._clock()
            record = _Session(account, config, now, now + self.READY_SECONDS)
            self._active = record
        worker = None
        try:
            worker = factory(dict(config))
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
