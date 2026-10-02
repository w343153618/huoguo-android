#!/usr/bin/env python3
"""Isolated, opt-in authenticated LAN UDP candidate; never replaces gateway.py.

Uses the existing account verifier and M1 certificate for HTTPS signaling only.
No public tunnel, cloud rule, display setting, or production listener is changed.
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
from udp_lan_worker import LanMediaWorker


class BoundedTlsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, context):
        self.context = context
        super().__init__(address, handler)

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


def same_private_lan(peer, host):
    """Initial candidate admits same /24 only; this is not a Tailnet service."""
    try:
        address = ipaddress.IPv4Address(peer)
        subnet = ipaddress.IPv4Network(host + '/24', strict=False)
        return address in subnet and address not in (subnet.network_address, subnet.broadcast_address)
    except ValueError:
        return False


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


def handler_for(registry, worker_factory, host, busy=formal_busy):
    class Handler(ExistingHandler):
        def do_CONNECT(self):
            self.reply(404, {'error': 'candidate_has_no_TCP_media'})

        def do_GET(self):
            if self.path == '/ping':
                self.reply(200, {'ok': True, 'node': 'M1-LAN-UDP-candidate',
                                 'protocol': 'HGUE_UDP_V1', 'media': 'UDP',
                                 'scope': 'isolated_same_subnet_LAN_not_WAN'})
                return
            if self.path.startswith('/udp/session/'):
                if not self.auth():
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
            if not same_private_lan(self.client_address[0], host):
                self.reply(403, {'error': 'candidate_requires_same_physical_LAN'})
                return
            if not self.auth():
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
    parser.add_argument('--interface', choices=('en7', 'en0'), required=True)
    parser.add_argument('--https-port', type=int, default=15560)
    parser.add_argument('--udp-port', type=int, default=15963)
    parser.add_argument('--runtime', type=Path, required=True,
                        help='Private candidate hardware runtime with patched cancel-capable guest JAR')
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--native-encoder', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--max-runtime', type=int, default=600)
    args = parser.parse_args()
    if args.https_port != 15560 or args.udp_port != 15963 or not 30 <= args.max_runtime <= 3600:
        parser.error('fixed isolated ports and bounded lifetime required')
    if not os.environ.get('DIRECT_AUTH_FILE'):
        parser.error('use the existing restricted account file; do not create an account')
    host = physical_lan_address(args.host, args.interface)
    registry = UdpLanSessions(host, args.udp_port)
    def factory(config, peer):
        return LanMediaWorker(config, peer, host, args.interface, args.runtime,
                              args.packetizer, args.native_encoder, registry,
                              args.evidence_dir, busy=formal_busy)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT, KEY)
    server = BoundedTlsServer((host, args.https_port), handler_for(registry, factory, host), context)
    stop = threading.Event()
    def reap():
        deadline = time.monotonic() + args.max_runtime
        while not stop.wait(.1):
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
                      'udp_port': args.udp_port, 'scope': 'isolated_LAN_candidate'}), flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        stop.set()
        registry.close()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    main()
