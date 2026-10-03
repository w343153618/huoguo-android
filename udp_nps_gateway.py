#!/usr/bin/env python3
"""Bounded owner-only NPS UDP candidate; HTTPS is signaling, never media.

Fixed public tuples are advertised to the phone. Both local backends bind only
127.0.0.1/lo0; the existing NPC owns the domestic outer connection. An exact
loopback proxy address is placement, not authentication or phone identity.
Existing account authentication and each session's GCM/replay-checked READY
remain mandatory. No production gateway, NPC, VM or friend gate is changed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import subprocess
import threading
import time

from gateway import Handler as ExistingHandler, CERT, KEY
from udp_lan_gateway import BoundedTlsServer, formal_busy, shutdown_registry
from udp_lan_sessions import UdpLanSessions, SessionError
from udp_lan_worker import LanMediaWorker
from udp_network_scope import ScopeUnavailable
from udp_nps_profile import (ProfileError, owner_profile, validate_owner_request,
                             validate_proxy_peer)


OWNER_ACCOUNT = 'wyw'
GUESTS = {'m1': ('emulator-5556', 'RemoteAndroid17Compare'),
          'm5': ('emulator-5554', 'phone17-root')}
SETTING_FIELDS = frozenset(('node', 'network_scope', 'max_size', 'video_bit_rate',
    'bitrate_mode', 'max_fps', 'buffer_ms', 'seconds', 'surface_submit_lead_ms',
    'audio', 'audio_enabled', 'touch', 'touch_enabled'))


def competing_candidate_busy():
    """Never start over a LAN/Tailnet candidate, even if it is currently idle.

    This is a conservative admission/monitor check, not a cross-process lock.
    The experiment supervisor must still serialize scope changes. An unrelated
    process on either dedicated port is also a reason to skip, never to kill it.
    """
    for selector in ('-iTCP:45560', '-iUDP:45963'):
        arguments = ['lsof', '-nP', selector]
        if selector.startswith('-iTCP:'):
            arguments.append('-sTCP:LISTEN')
        arguments.append('-t')
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=2)
        if result.returncode not in (0, 1) or result.stdout.strip():
            return True
    return False


def trial_busy():
    # The new signaling port45561 is intentionally excluded. A formal socket
    # on local15556 or another candidate wins; only this candidate is revoked.
    return formal_busy() or competing_candidate_busy()


def guest_for(profile, environment):
    """Read explicit server runtime selection; never fall back to a M1 guest."""
    serial, avd = GUESTS[profile.node]
    if (environment.get('DIRECT_SERIAL') != serial
            or environment.get('DIRECT_AVD') != avd
            or environment.get('DIRECT_EXTERNAL_VM') != '1'):
        raise ProfileError('explicit_profile_guest_runtime_required')
    return serial, avd


def validate_settings(settings, profile, *, allow_m5_owner_trial=False):
    """Closed HTTP body: the caller cannot replace a public or local tuple."""
    if type(settings) is not dict or not set(settings).issubset(SETTING_FIELDS):
        raise ProfileError('closed_nps_stream_settings_required')
    identity = {'node': settings.get('node'), 'network_scope': settings.get('network_scope')}
    selected = validate_owner_request(identity, allow_m5_owner_trial=allow_m5_owner_trial)
    if selected != profile:
        raise ProfileError('nps_node_profile_mismatch')
    return settings


class OwnerNpsScope:
    """Sticky local placement guard; it makes no WAN/domestic-route claim."""
    name = 'nps_owner'
    ping_scope = 'bounded_owner_NPS_public_UDP_not_friend_deployment'

    def __init__(self, profile, *, allow_m5_owner_trial=False):
        self.profile, self.allow_m5_owner_trial = profile, allow_m5_owner_trial
        self._healthy = False
        self._failed = False
        self._lock = threading.Lock()
        self.verify()

    def healthy(self):
        return self._healthy

    def permits_peer(self, peer):
        try:
            validate_proxy_peer(self.profile, peer,
                                allow_m5_owner_trial=self.allow_m5_owner_trial)
            return True
        except ProfileError:
            return False

    def verify(self):
        with self._lock:
            if self._failed:
                raise ScopeUnavailable('nps_loopback_scope_unavailable')
            try:
                if socket.if_nametoindex(self.profile.interface) <= 0:
                    raise OSError()
                self._healthy = True
                return True
            except (OSError, ValueError):
                self._healthy, self._failed = False, True
                raise ScopeUnavailable('nps_loopback_scope_unavailable') from None


class LoopbackTlsServer(BoundedTlsServer):
    """Bind/read back lo0 and reject a foreign proxy before TLS or HTTP auth."""
    def __init__(self, profile, handler, context, *, allow_m5_owner_trial=False):
        self.profile, self.allow_m5_owner_trial = profile, allow_m5_owner_trial
        super().__init__((profile.local_https.host, profile.local_https.port),
                         handler, context, interface=profile.interface)

    def server_bind(self):
        index = socket.if_nametoindex(self.profile.interface)
        self.socket.setsockopt(socket.IPPROTO_IP, 25, index)
        if self.socket.getsockopt(socket.IPPROTO_IP, 25) != index:
            raise ScopeUnavailable('nps_HTTPS_loopback_interface_bind_mismatch')
        # Bypass the parent's generic interface bind; exact NPS tuple was
        # selected from the immutable profile, never from HTTP/CLI settings.
        from http.server import ThreadingHTTPServer
        ThreadingHTTPServer.server_bind(self)
        if self.socket.getsockname()[:2] != (self.profile.local_https.host,
                                             self.profile.local_https.port):
            raise ScopeUnavailable('nps_HTTPS_loopback_endpoint_bind_mismatch')

    def get_request(self):
        connection, peer = self.socket.accept()
        try:
            validate_proxy_peer(self.profile, peer,
                                allow_m5_owner_trial=self.allow_m5_owner_trial)
            connection.settimeout(5)
            return self.context.wrap_socket(connection, server_side=True), peer
        except Exception:
            connection.close()
            raise


def handler_for(registry, worker_factory, profile, busy=trial_busy, *, scope=None,
                allow_m5_owner_trial=False):
    scope = scope or OwnerNpsScope(profile, allow_m5_owner_trial=allow_m5_owner_trial)

    class Handler(ExistingHandler):
        def proxy_admission(self):
            if not scope.permits_peer(self.client_address):
                self.reply(403, {'error': 'exact_loopback_proxy_peer_required'})
                return False
            return True

        def owner_auth(self):
            if not self.auth():
                return False
            if self.account != OWNER_ACCOUNT:
                self.reply(403, {'error': 'nps_owner_account_required'})
                return False
            return True

        def scope_admission(self):
            try:
                scope.verify()
                return True
            except ScopeUnavailable:
                registry.close()
                self.reply(503, {'error': 'udp_network_scope_unavailable'})
                return False

        def do_CONNECT(self):
            # Never delegate to the formal Handler's TCP media path.
            self.reply(404, {'error': 'candidate_has_no_TCP_media'})

        def do_GET(self):
            if not self.proxy_admission():
                return
            if self.path == '/ping':
                self.reply(200, {'ok': True, 'node': profile.node,
                    'protocol': 'HGUE_UDP_V1', 'media': 'UDP',
                    'network_scope': scope.name, 'scope': scope.ping_scope,
                    'owner_trial': True, 'host_isolation_accepted': False})
                return
            if re.fullmatch(r'/udp/session/[0-9a-f]{32}', self.path) is None:
                self.reply(404, {'error': 'unknown_candidate_route'})
                return
            if not self.owner_auth() or not self.scope_admission():
                return
            try:
                status = registry.status(self.account, self.path[len('/udp/session/'):])
                self.reply(200 if status else 404, status or {'error': 'session_not_found'})
            except SessionError as error:
                self.reply(error.status, {'error': error.code})

        def do_POST(self):
            if not self.proxy_admission():
                return
            if self.path != '/udp/session':
                self.reply(404, {'error': 'unknown_candidate_route'})
                return
            if not self.owner_auth() or not self.scope_admission():
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or 'Transfer-Encoding' in self.headers
                        or re.fullmatch(r'[0-9]{1,3}', lengths[0]) is None):
                    raise ValueError()
                length = int(lengths[0])
                if not 1 <= length <= 512:
                    raise ValueError()
                self.connection.settimeout(5)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError()
                settings = validate_settings(json.loads(body), profile,
                    allow_m5_owner_trial=allow_m5_owner_trial)
                if busy():
                    self.reply(409, {'error': 'formal_or_other_candidate_busy_skip_trial'})
                    return
                descriptor = registry.create(self.account, settings,
                    lambda config: worker_factory(config, self.client_address[0]))
                self.reply(201, descriptor)
            except SessionError as error:
                self.reply(error.status, {'error': error.code})
            except subprocess.SubprocessError:
                self.reply(503, {'error': 'busy_check_unavailable_skip_candidate'})
            except (ValueError, OSError):
                self.reply(400, {'error': 'invalid_bounded_settings'})

        def do_DELETE(self):
            if not self.proxy_admission():
                return
            if re.fullmatch(r'/udp/session/[0-9a-f]{32}', self.path) is None:
                self.reply(404, {'error': 'unknown_candidate_route'})
                return
            # A revoked local scope still permits normal authenticated cleanup.
            if not self.owner_auth():
                return
            try:
                closed = registry.cancel(self.account, self.path[len('/udp/session/'):])
                self.reply(200 if closed else 404,
                           {'closed': True} if closed else {'error': 'session_not_found'})
            except SessionError as error:
                self.reply(error.status, {'error': error.code})

    return Handler


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True, choices=('m1', 'm5'))
    parser.add_argument('--owner-m5-trial', action='store_true',
                        help='Explicit bounded M5 owner trial; never opens friend admission')
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--native-encoder', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--max-runtime', type=int, default=600)
    args = parser.parse_args(argv)
    if not 30 <= args.max_runtime <= 3600:
        parser.error('bounded_gateway_lifetime_required')
    if args.owner_m5_trial and args.node != 'm5':
        parser.error('M5_owner_trial_flag_requires_M5_node')
    try:
        args.profile = owner_profile(args.node, allow_m5_owner_trial=args.owner_m5_trial)
    except ProfileError:
        parser.error('explicit_owner_profile_admission_required')
    return args


def main():
    args = parse_arguments()
    if not os.environ.get('DIRECT_AUTH_FILE'):
        raise SystemExit('existing_restricted_account_file_required')
    serial, avd = guest_for(args.profile, os.environ)
    if trial_busy():
        raise SystemExit('formal_or_other_candidate_busy_skip_trial')
    scope = OwnerNpsScope(args.profile, allow_m5_owner_trial=args.owner_m5_trial)
    registry = UdpLanSessions(args.profile.public_media.host, args.profile.public_media.port,
        network_scope=scope.name, node=args.node, scope_guard=scope.healthy,
        allow_m5_owner_trial=args.owner_m5_trial, guest_serial=serial, guest_avd=avd)

    def factory(config, peer):
        return LanMediaWorker(config, peer, args.profile.local_udp.host,
            args.profile.interface, args.runtime, args.packetizer, args.native_encoder,
            registry, args.evidence_dir, busy=trial_busy,
            guest_serial=serial, guest_avd=avd)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT, KEY)
    server = LoopbackTlsServer(args.profile,
        handler_for(registry, factory, args.profile, scope=scope,
                    allow_m5_owner_trial=args.owner_m5_trial),
        context, allow_m5_owner_trial=args.owner_m5_trial)
    stop = threading.Event()

    def reap():
        deadline = time.monotonic() + args.max_runtime
        next_scope_check = time.monotonic() + 1
        while not stop.wait(.1):
            if time.monotonic() >= next_scope_check:
                next_scope_check = time.monotonic() + 1
                try:
                    scope.verify()
                except ScopeUnavailable:
                    registry.close()
                    server.shutdown()
                    return
            registry.reap()
            if time.monotonic() >= deadline:
                server.shutdown()
                return

    reaper = threading.Thread(target=reap, name='owner-nps-session-reaper', daemon=True)
    reaper.start()

    def shutdown(signum, frame):
        threading.Thread(target=server.shutdown, name='owner-nps-gateway-shutdown', daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(json.dumps({'event': 'listening', 'node': args.node, 'network_scope': scope.name,
        'local_https_port': args.profile.local_https.port,
        'local_udp_port': args.profile.local_udp.port,
        'advertised_control_port': args.profile.public_control.port,
        'advertised_media_port': args.profile.public_media.port,
        'owner_m5_trial': args.owner_m5_trial, 'host_isolation_accepted': False}), flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        stop.set()
        try:
            server.server_close()
        finally:
            outcome = shutdown_registry(registry)
            print(json.dumps(outcome), flush=True)
            reaper.join(timeout=2)
            if not outcome['quiescence_confirmed']:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
