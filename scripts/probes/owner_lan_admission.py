#!/usr/bin/env python3
"""Owner-only LAN handover reservation; CLI is descriptive dry-run only.

Trusted callers supply authentication and exact-instance readback. This module
does not read credentials, make HTTP requests, inspect argv, or signal services.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import socket
import sys
import threading


LOOPBACK_ENDPOINT = ('127.0.0.1', 45965)
LOOPBACK_INTERFACE = 'lo0'
DARWIN_IP_BOUND_IF = 25
_IDENTITY_KEYS = frozenset(('gateway_pid', 'gateway_start_id',
                           'gateway_source_sha256', 'runtime_manifest_sha256'))
_PROCESS_KEYS = frozenset(('owned_descendants', 'hardware_process_groups',
                          'packetizer_processes', 'guest_control_processes'))
_READBACK_KEYS = _IDENTITY_KEYS | _PROCESS_KEYS | {'coverage_complete'}
_QUIESCENCE_KEYS = frozenset(('event', 'quiescence_confirmed', 'stop_failures',
                             'gateway_exit_confirmed', 'owned_media_exit_confirmed'))


class AdmissionError(RuntimeError):
    """A fixed code only; never stringify an HTTP response or caller exception."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExpectedGateway:
    gateway_pid: int
    gateway_start_id: str
    gateway_source_sha256: str
    runtime_manifest_sha256: str

    def __post_init__(self):
        if type(self.gateway_pid) is not int or self.gateway_pid < 2:
            raise ValueError('expected_gateway_pid_required')
        if (type(self.gateway_start_id) is not str
                or re.fullmatch(r'[A-Za-z0-9_.:+-]{1,96}', self.gateway_start_id) is None):
            raise ValueError('expected_gateway_start_identity_required')
        for value in (self.gateway_source_sha256, self.runtime_manifest_sha256):
            if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
                raise ValueError('expected_frozen_sha256_required')


def validate_witness(value):
    """Accept only the live registry's exact failed pre-guest factory outcome.

    The trusted HTTPS adapter projects {status, error}; it must validate the
    existing certificate and exact local endpoint, impose a finite timeout,
    and avoid returning a descriptor, credentials, or arbitrary response body.
    """
    if (type(value) is not dict or set(value) != {'status', 'error'}
            or type(value['status']) is not int or type(value['error']) is not str):
        raise AdmissionError('witness_schema_rejected')
    if value == {'status': 503, 'error': 'udp_worker_unavailable'}:
        return
    if value['status'] in (401, 403):
        raise AdmissionError('witness_auth_rejected')
    if value['status'] == 409:
        raise AdmissionError('witness_busy')
    if value == {'status': 503, 'error': 'udp_cleanup_failed'}:
        raise AdmissionError('witness_cleanup_failed')
    raise AdmissionError('witness_not_idle')


def validate_readback(value, expected):
    if (type(value) is not dict or set(value) != _READBACK_KEYS
            or value['coverage_complete'] is not True):
        raise AdmissionError('readback_incomplete')
    for name in _IDENTITY_KEYS:
        if type(value[name]) is not type(getattr(expected, name)):
            raise AdmissionError('readback_identity_schema_rejected')
        if value[name] != getattr(expected, name):
            raise AdmissionError('gateway_identity_or_freeze_changed')
    for name in _PROCESS_KEYS:
        if type(value[name]) is not int or value[name] < 0:
            raise AdmissionError('readback_process_schema_rejected')
        if value[name] != 0:
            raise AdmissionError('owned_processes_still_active')


def validate_quiescence(value):
    if (type(value) is not dict or set(value) != _QUIESCENCE_KEYS
            or type(value['event']) is not str or value['event'] != 'candidate_shutdown'
            or value['quiescence_confirmed'] is not True
            or type(value['stop_failures']) is not int or value['stop_failures'] != 0
            or value['gateway_exit_confirmed'] is not True
            or value['owned_media_exit_confirmed'] is not True):
        raise AdmissionError('owned_lan_quiescence_unconfirmed')


def _bind_exclusive_loopback(owned, endpoint):
    """Shared socket primitive; tests call it only with an owned ephemeral port.

    Production admission always supplies the immutable LOOPBACK_ENDPOINT.
    This private primitive adds no configurable production endpoint or CLI.
    """
    if (type(endpoint) is not tuple or len(endpoint) != 2
            or endpoint[0] != '127.0.0.1' or type(endpoint[1]) is not int
            or not 0 <= endpoint[1] <= 65535):
        raise AdmissionError('loopback_bind_schema_rejected')
    if owned.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0:
        raise AdmissionError('reservation_reuse_rejected')
    if hasattr(socket, 'SO_REUSEPORT') and owned.getsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0:
        raise AdmissionError('reservation_reuse_rejected')
    index = socket.if_nametoindex(LOOPBACK_INTERFACE)
    if index <= 0:
        raise AdmissionError('loopback_interface_unavailable')
    owned.setsockopt(socket.IPPROTO_IP, DARWIN_IP_BOUND_IF, index)
    if owned.getsockopt(socket.IPPROTO_IP, DARWIN_IP_BOUND_IF) != index:
        raise AdmissionError('loopback_interface_mismatch')
    try:
        owned.bind(endpoint)
    except OSError:
        raise AdmissionError('reservation_bind_unavailable') from None
    return owned.getsockname()[:2]


class OwnerLanAdmission:
    """Hold fixed UDP45965 until the caller's owned LAN work is quiescent.

    witness/readback must be trusted, finite, payload-free callbacks. No socket
    is opened at construction. Mark LAN starting *before* Popen/thread startup.
    On incomplete LAN cleanup the reservation is deliberately retained: keep
    this object and its supervisor alive, finish bounded owned cleanup, then
    confirm and close. A process crash cannot preserve an OS socket lease.
    """

    def __init__(self, expected, witness, readback, *, _socket_factory=socket.socket):
        if type(expected) is not ExpectedGateway:
            raise ValueError('expected_gateway_required')
        if not callable(witness) or not callable(readback):
            raise ValueError('trusted_callbacks_required')
        self.expected, self.witness, self.readback = expected, witness, readback
        self._socket_factory = _socket_factory
        self._socket = None
        self._lock = threading.RLock()
        self._state = 'new'

    @property
    def state(self):
        with self._lock:
            return self._state

    def _release(self):
        owned = self._socket
        if owned is not None:
            try:
                owned.close()
            except Exception:
                raise AdmissionError('reservation_release_unconfirmed') from None
        self._socket = None
        self._state = 'released'

    def __enter__(self):
        with self._lock:
            if self._state != 'new':
                raise AdmissionError('reservation_not_reentrant')
            if sys.platform != 'darwin':
                raise AdmissionError('darwin_physical_binding_required')
            try:
                owned = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
                self._socket = owned
                if _bind_exclusive_loopback(owned, LOOPBACK_ENDPOINT) != LOOPBACK_ENDPOINT:
                    raise AdmissionError('reservation_endpoint_mismatch')
                self._state = 'reserved'
                try:
                    result = self.witness()
                except Exception:
                    raise AdmissionError('witness_unavailable') from None
                validate_witness(result)
                try:
                    result = self.readback()
                except Exception:
                    raise AdmissionError('readback_unavailable') from None
                validate_readback(result, self.expected)
                self._state = 'admitted'
                return self
            except BaseException:
                self._release()
                raise

    def mark_owned_lan_starting(self):
        with self._lock:
            if self._state != 'admitted':
                raise AdmissionError('lan_start_outside_admission')
            self._state = 'lan_starting'

    def confirm_owned_lan_quiescence(self, result):
        with self._lock:
            if self._state not in ('lan_starting', 'cleanup_required'):
                raise AdmissionError('lan_quiescence_outside_owned_attempt')
            validate_quiescence(result)
            self._state = 'quiescent'

    def close(self):
        with self._lock:
            if self._state in ('lan_starting', 'cleanup_required'):
                self._state = 'cleanup_required'
                raise AdmissionError('owned_lan_quiescence_required')
            self._release()

    def __exit__(self, exc_type, exc, traceback):
        with self._lock:
            if self._state in ('lan_starting', 'cleanup_required'):
                self._state = 'cleanup_required'
                if exc_type is None:
                    raise AdmissionError('owned_lan_quiescence_required')
                # Retain the reservation without replacing the primary failure.
                return False
            self._release()
            return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', default=True)
    parser.parse_args(argv)
    print(json.dumps({'mode': 'dry_run', 'binding_performed': False,
                      'signals_sent': 0, 'http_requests_sent': 0,
                      'required_endpoint': '127.0.0.1:45965',
                      'required_interface': 'lo0',
                      'live_execution': 'trusted_library_caller_only'}, sort_keys=True))


if __name__ == '__main__':
    main()
