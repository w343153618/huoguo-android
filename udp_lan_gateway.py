#!/usr/bin/env python3
"""Isolated authenticated LAN or registered-Tailnet UDP candidate.

Uses the existing account verifier and M1 certificate for HTTPS signaling only.
No public tunnel, cloud rule, display setting, or production listener is changed.
Never replaces gateway.py or silently falls back to TCP media.
"""
import argparse
import ipaddress
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
from http.server import ThreadingHTTPServer

from gateway import Handler as ExistingHandler, CERT, KEY
from udp_lan_sessions import UdpLanSessions, SessionError
from udp_lan_worker import LanMediaWorker, owner_raw_queue_policy
from udp_network_scope import LanScope, TailnetScope, ScopeUnavailable, same_private_lan


class BoundedTlsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, context, interface=None):
        self.context = context
        self.interface = interface
        super().__init__(address, handler)

    def server_bind(self):
        if self.interface is not None:
            index = socket.if_nametoindex(self.interface)
            self.socket.setsockopt(socket.IPPROTO_IP, 25, index)
            if self.socket.getsockopt(socket.IPPROTO_IP, 25) != index:
                raise ScopeUnavailable('tailnet_HTTPS_interface_bind_mismatch')
        super().server_bind()

    def get_request(self):
        connection, peer = self.socket.accept()
        connection.settimeout(5)
        try:
            return self.context.wrap_socket(connection, server_side=True), peer
        except Exception:
            connection.close()
            raise


def physical_lan_address(host, interface):
    """Only an explicitly selected, current M1 physical RFC1918 IPv4."""
    address = ipaddress.IPv4Address(host)
    if not any(address in ipaddress.IPv4Network(prefix) for prefix in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('physical_private_LAN_IPv4_required')
    if interface not in ('en7', 'en0'):
        raise ValueError('explicit_M1_physical_interface_required')
    actual = subprocess.run(['ipconfig', 'getifaddr', interface], check=True,
                            capture_output=True, text=True, timeout=2).stdout.strip()
    if actual != host:
        raise ValueError('physical_interface_IPv4_mismatch')
    return str(address)


def formal_busy():
    """Conservative read-only gate: any established formal M1 socket is busy.

    This may skip an update download too. It does not inspect the payload or
    stop another session. Check again in the worker before touching the guest.
    """
    result = subprocess.run(['lsof', '-nP', '-iTCP:15556', '-sTCP:ESTABLISHED', '-t'],
                            capture_output=True, text=True, timeout=2)
    if result.returncode not in (0, 1):
        return True
    return bool(result.stdout.strip())


def shutdown_registry(registry, timeout=45.0):
    """Closed shutdown result only; neither revocation nor daemon exit is proof."""
    try:
        result = registry.close_and_wait(timeout)
        if (result.get('quiescence_confirmed') is not True or
                type(result.get('stop_failures')) is not int or result['stop_failures'] < 0):
            raise ValueError('Closed shutdown result required')
        return {'event': 'candidate_shutdown', 'quiescence_confirmed': True,
                'stop_failures': result['stop_failures']}
    except SessionError as error:
        known = {'udp_shutdown_quiescence_timeout', 'udp_cleanup_failed',
                 'udp_shutdown_worker_unavailable'}
        return {'event': 'candidate_shutdown', 'quiescence_confirmed': False,
                'reason': error.code if error.code in known else 'udp_shutdown_unclassified_failure'}
    except Exception:
        return {'event': 'candidate_shutdown', 'quiescence_confirmed': False,
                'reason': 'udp_shutdown_unclassified_failure'}


def handler_for(registry, worker_factory, host, busy=formal_busy, *, scope=None):
    scope = scope or LanScope(host)
    class Handler(ExistingHandler):
        def verify_network_scope(self):
            try:
                scope.verify()
                return True
            except ScopeUnavailable:
                # Revoke only this candidate's reservation. DELETE remains
                # available for authenticated owner cleanup on an exact peer.
                registry.close()
                self.reply(503, {'error': 'udp_network_scope_unavailable'})
                return False

        def do_CONNECT(self):
            self.reply(404, {'error': 'candidate_has_no_TCP_media'})

        def do_GET(self):
            if self.path == '/ping':
                self.reply(200, {'ok': True, 'node': ('M1-LAN-UDP-candidate' if scope.name == 'lan'
                                                    else 'M1-Tailnet-UDP-candidate'),
                                 'protocol': 'HGUE_UDP_V1', 'media': 'UDP',
                                 'scope': scope.ping_scope, 'network_scope': scope.name})
                return
            if self.path.startswith('/udp/session/'):
                if not self.auth():
                    return
                if scope.name == 'tailnet':
                    if not scope.permits_peer(self.client_address[0]):
                        self.reply(403, {'error': 'candidate_requires_registered_Tailnet_peer'})
                        return
                    if not self.verify_network_scope():
                        return
                try:
                    status = registry.status(self.account, self.path[len('/udp/session/'):])
                    self.reply(200 if status else 404, status or {'error': 'session_not_found'})
                except SessionError as error:
                    self.reply(error.status, {'error': error.code})
                return
            self.reply(404, {'error': 'unknown_candidate_route'})

        def do_POST(self):
            if self.path != '/udp/session':
                self.reply(404, {'error': 'unknown_candidate_route'})
                return
            if not scope.permits_peer(self.client_address[0]):
                self.reply(403, {'error': ('candidate_requires_same_physical_LAN' if scope.name == 'lan'
                                         else 'candidate_requires_registered_Tailnet_peer')})
                return
            if not self.auth():
                return
            if not self.verify_network_scope():
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or 'Transfer-Encoding' in self.headers or
                        not re.fullmatch(r'[0-9]{1,3}', lengths[0])):
                    raise ValueError('bounded_content_length_required')
                length = int(lengths[0])
                if not 1 <= length <= 512:
                    raise ValueError('settings_body_bound')
                self.connection.settimeout(5)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('incomplete_settings')
                settings = json.loads(body)
                if busy():
                    self.reply(409, {'error': 'formal_session_busy_skip_candidate'})
                    return
                peer = self.client_address[0]
                descriptor = registry.create(self.account, settings,
                                             lambda config: worker_factory(config, peer))
                self.reply(201, descriptor)
            except SessionError as error:
                self.reply(error.status, {'error': error.code})
            except subprocess.SubprocessError:
                self.reply(503, {'error': 'busy_check_unavailable_skip_candidate'})
            except (ValueError, OSError):
                self.reply(400, {'error': 'invalid_bounded_settings'})

        def do_DELETE(self):
            if not self.path.startswith('/udp/session/'):
                self.reply(404, {'error': 'unknown_candidate_route'})
                return
            if not self.auth():
                return
            if scope.name == 'tailnet' and not scope.permits_peer(self.client_address[0]):
                self.reply(403, {'error': 'candidate_requires_registered_Tailnet_peer'})
                return
            sid = self.path[len('/udp/session/'):]
            try:
                closed = registry.cancel(self.account, sid)
                self.reply(200 if closed else 404,
                           {'closed': True} if closed else {'error': 'session_not_found'})
            except SessionError as error:
                self.reply(error.status, {'error': error.code})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--interface', required=True)
    parser.add_argument('--network-scope', choices=('lan', 'tailnet'), default='lan')
    parser.add_argument('--allow-owner-surface-submit-lead', action='store_true',
                        help='Bounded owner experiment only: explicitly permit requested lead 16 ms; default 0 remains')
    parser.add_argument('--allow-owner-enobufs-retry', action='store_true',
                        help='Owner sender experiment only: bounded ENOBUFS backoff within original deadlines; default off')
    parser.add_argument('--https-port', type=int, default=45560)
    parser.add_argument('--udp-port', type=int, default=45963)
    parser.add_argument('--runtime', type=Path, required=True,
                        help='Private candidate hardware runtime with patched cancel-capable guest JAR')
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--native-encoder', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--capture-trace-dir', type=Path,
                        help='Owner diagnostic opt-in: existing private trace parent; default off; never accepted from HTTP')
    parser.add_argument('--owner-raw-queue-policy', choices=('fifo', 'latest'), default='fifo',
                        help='LAN trace experiment only: choose complete pre-encode raw frames; default FIFO remains')
    parser.add_argument('--max-runtime', type=int, default=600)
    args = parser.parse_args()
    if args.https_port != 45560 or args.udp_port != 45963 or not 30 <= args.max_runtime <= 3600:
        parser.error('fixed isolated ports and bounded lifetime required')
    try:
        owner_raw_queue_policy(args.owner_raw_queue_policy, args.network_scope, args.capture_trace_dir)
    except ValueError as error:
        parser.error(str(error))
    if not os.environ.get('DIRECT_AUTH_FILE'):
        parser.error('use the existing restricted account file; do not create an account')
    try:
        if args.network_scope == 'lan':
            host = physical_lan_address(args.host, args.interface)
            scope = LanScope(host)
        else:
            scope = TailnetScope(args.host, args.interface)
            host = scope.host
    except (ValueError, ScopeUnavailable) as error:
        parser.error(str(error))
    registry = UdpLanSessions(host, args.udp_port, network_scope=scope.name,
                              scope_guard=scope.healthy,
                              allow_owner_surface_submit_lead=args.allow_owner_surface_submit_lead)
    def factory(config, peer):
        return LanMediaWorker(config, peer, host, args.interface, args.runtime,
                              args.packetizer, args.native_encoder, registry,
                              args.evidence_dir, busy=formal_busy,
                              enobufs_retry_enabled=args.allow_owner_enobufs_retry,
                              capture_trace_dir=args.capture_trace_dir,
                              raw_queue_policy=args.owner_raw_queue_policy)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT, KEY)
    server = BoundedTlsServer((host, args.https_port),
                              handler_for(registry, factory, host, scope=scope), context,
                              interface=args.interface if scope.name == 'tailnet' else None)
    stop = threading.Event()
    def reap():
        deadline = time.monotonic() + args.max_runtime
        next_scope_check = time.monotonic() + 1
        while not stop.wait(.1):
            if scope.name == 'tailnet' and time.monotonic() >= next_scope_check:
                next_scope_check = time.monotonic() + scope.CHECK_SECONDS
                try:
                    scope.verify()
                except ScopeUnavailable:
                    registry.close()
                    print(json.dumps({'event': 'candidate_scope_revoked',
                                      'network_scope': scope.name,
                                      'reason': scope.last_failure}), flush=True)
                    server.shutdown()
                    break
            registry.reap()
            if time.monotonic() >= deadline:
                server.shutdown()
                break
    thread = threading.Thread(target=reap, name='udp-session-reaper', daemon=True)
    thread.start()
    def shutdown(signum, frame):
        threading.Thread(target=server.shutdown, name='owned_gateway_shutdown', daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(json.dumps({'event': 'listening', 'host': host, 'https_port': args.https_port,
                      'udp_port': args.udp_port, 'scope': scope.ping_scope,
                      'network_scope': scope.name, 'inner_interface': args.interface,
                      'capture_trace_enabled': args.capture_trace_dir is not None,
                      'owner_raw_queue_policy_requested': args.owner_raw_queue_policy,
                      'owner_enobufs_retry_enabled': args.allow_owner_enobufs_retry}), flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        stop.set()
        try:
            server.server_close()
        finally:
            outcome = shutdown_registry(registry)
            print(json.dumps(outcome), flush=True)
            thread.join(timeout=2)
            if not outcome['quiescence_confirmed']:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
