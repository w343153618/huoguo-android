#!/usr/bin/env python3
"""Loopback-only QUIC/KCP relay with a fixed Tencent destination and physical egress.

NPC connects to localhost over UDP. Every upstream socket is bound to an allowed
macOS Ethernet/Wi-Fi interface with IP_BOUND_IF; a TUN or unbound fallback is
never used. Use one process per protocol so no arbitrary destination is exposed.
"""

import argparse
import logging
import os
import socket
import sys
import threading
import time

from npc_physical_relay import IP_BOUND_IF, interface_usable

DESTINATION_HOST = '146.56.249.175'
PORTS = {'quic': (18025, 8025), 'kcp': (18026, 8024)}
INTERFACES = tuple(os.environ.get('NPC_PHYSICAL_INTERFACES', 'en7,en0').split(','))
MAX_PEERS = 16
IDLE_SECONDS = 120
CHECK_SECONDS = 5
FAILBACK_STABLE_SECONDS = 15


def physical_udp_connect(destination, interfaces=INTERFACES):
    if sys.platform != 'darwin':
        raise OSError('Physical UDP relay requires macOS')
    for name in interfaces:
        if not name.startswith('en') or not name[2:].isdigit():
            continue
        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            index = socket.if_nametoindex(name)
            if index <= 0:
                raise OSError('Missing physical interface')
            upstream.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, index)
            if upstream.getsockopt(socket.IPPROTO_IP, IP_BOUND_IF) != index:
                raise OSError('Interface binding was not applied')
            upstream.connect(destination)
            source_ip = upstream.getsockname()[0]
            if not interface_usable(name, index, source_ip):
                raise OSError('Bound interface has no active matching IPv4 address')
            upstream.settimeout(1)
            return upstream, name, index, source_ip
        except OSError:
            upstream.close()
    raise OSError('No allowed physical UDP path; refusing proxy fallback')


class Peer:
    def __init__(self, client, listener, destination, remove, interfaces=INTERFACES):
        self.client = client
        self.listener = listener
        self.destination = destination
        self.remove = remove
        self.interfaces = interfaces
        self.upstream, self.name, self.index, self.source_ip = physical_udp_connect(
            destination, interfaces)
        self.last_activity = time.monotonic()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.better_since = None
        self.last_check = self.last_activity
        self.trace_count = 0

    def trace(self, direction, size):
        if os.environ.get('NPC_RELAY_PACKET_TRACE') == '1' and self.trace_count < 24:
            logging.info('UDP peer %s %s %d bytes', self.client, direction, size)
            self.trace_count += 1

    def send(self, payload):
        with self.lock:
            if self.closed.is_set():
                raise OSError('UDP peer closed')
            self.upstream.send(payload)
            self.trace('to-server', len(payload))
            self.last_activity = time.monotonic()

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self.closed.set()
            self.upstream.close()
        self.remove(self.client, self)

    def path_valid(self, now):
        if not interface_usable(self.name, self.index, self.source_ip):
            return False
        rank = self.interfaces.index(self.name)
        preferred = next((name for name in self.interfaces[:rank]
                          if interface_usable(name)), None)
        if preferred is None:
            self.better_since = None
            return True
        if self.better_since is None or self.better_since[0] != preferred:
            self.better_since = (preferred, now)
            return True
        if now - self.better_since[1] < FAILBACK_STABLE_SECONDS:
            return True
        try:
            probe, _, _, _ = physical_udp_connect(self.destination, (preferred,))
            probe.close()
            return False
        except OSError:
            self.better_since = (preferred, now)
            return True

    def receive_loop(self):
        logging.info('UDP peer %s via %s source %s', self.client, self.name, self.source_ip)
        try:
            while not self.closed.is_set():
                now = time.monotonic()
                if now - self.last_activity > IDLE_SECONDS:
                    break
                if now - self.last_check >= CHECK_SECONDS:
                    self.last_check = now
                    if not self.path_valid(now):
                        logging.info('UDP peer %s physical path changed', self.client)
                        break
                try:
                    payload = self.upstream.recv(65535)
                except socket.timeout:
                    continue
                self.last_activity = time.monotonic()
                self.trace('to-npc', len(payload))
                self.listener.sendto(payload, self.client)
        except OSError:
            pass
        finally:
            self.close()


def serve(protocol, interfaces=INTERFACES):
    listen_port, destination_port = PORTS[protocol]
    destination = (DESTINATION_HOST, destination_port)
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(('127.0.0.1', listen_port))
    peers = {}
    peers_lock = threading.Lock()

    def remove(client, peer):
        with peers_lock:
            if peers.get(client) is peer:
                del peers[client]

    logging.info('%s relay 127.0.0.1:%d -> %s:%d', protocol, listen_port, *destination)
    while True:
        payload, client = listener.recvfrom(65535)
        with peers_lock:
            peer = peers.get(client)
            if peer is None and len(peers) >= MAX_PEERS:
                continue
            if peer is None:
                try:
                    peer = Peer(client, listener, destination, remove, interfaces)
                except OSError as exc:
                    logging.warning('Physical UDP path unavailable: %s', exc)
                    continue
                peers[client] = peer
                threading.Thread(target=peer.receive_loop, daemon=True).start()
        try:
            peer.send(payload)
        except OSError:
            peer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('protocol', choices=PORTS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        serve(args.protocol)
    except KeyboardInterrupt:
        logging.info('%s relay stopped', args.protocol)


if __name__ == '__main__':
    main()
