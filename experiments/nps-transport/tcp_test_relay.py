#!/usr/bin/env python3
"""A loopback-only, fixed-destination relay binding every NPS socket to physical NICs.

macOS IP_BOUND_IF restricts kernel routing for the outbound socket. No ordinary
unbound connect, DNS, proxy environment, or VPN fallback is ever used.
"""
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time

DESTINATION = ('146.56.249.175', 18024)
LISTEN = ('127.0.0.1', 18027)
INTERFACES = tuple(os.environ.get('NPC_PHYSICAL_INTERFACES', 'en7,en0').split(','))
IP_BOUND_IF = 25  # Apple XNU bsd/netinet/in.h
LINK_CHECK_SECONDS = 5
FAILBACK_STABLE_SECONDS = 15
slots = threading.BoundedSemaphore(64)

def physical_connect(interfaces=INTERFACES):
    if sys.platform != 'darwin':
        raise OSError('Physical NPS relay requires macOS')
    for name in interfaces:
        # Never accept utun/tun or allow a missing device to become index zero.
        if not name.startswith('en') or not name[2:].isdigit():
            continue
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            index = socket.if_nametoindex(name)
            if index <= 0:
                raise OSError('Missing physical interface')
            upstream.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, index)
            if upstream.getsockopt(socket.IPPROTO_IP, IP_BOUND_IF) != index:
                raise OSError('Interface binding was not applied')
            upstream.settimeout(6)
            upstream.connect(DESTINATION)
            upstream.settimeout(None)
            upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return upstream, name, index
        except OSError:
            upstream.close()
    raise OSError('No allowed physical NPS path; refusing proxy fallback')

def interface_usable(name, index=None, source_ip=None):
    """Check link state and IPv4 address, not just the persistent device index."""
    if not re.fullmatch(r'en[0-9]+', name):
        return False
    try:
        current_index = socket.if_nametoindex(name)
        if current_index <= 0 or (index is not None and current_index != index):
            return False
        state = subprocess.run(['/sbin/ifconfig', name], capture_output=True,
                               text=True, timeout=2, check=False)
        if state.returncode != 0 or 'status: active' not in state.stdout:
            return False
        addresses = re.findall(r'^\s*inet\s+(\d{1,3}(?:\.\d{1,3}){3})\b',
                               state.stdout, re.MULTILINE)
        return bool(addresses) and (source_ip is None or source_ip in addresses)
    except (OSError, subprocess.TimeoutExpired):
        return False

def watch_connection(upstream, name, index, closed, interfaces=INTERFACES):
    """Force a reconnect after link loss, or when a stable higher-priority link returns."""
    source_ip = upstream.getsockname()[0]
    better_since = None
    while not closed.wait(LINK_CHECK_SECONDS):
        if not interface_usable(name, index, source_ip):
            return 'bound interface lost its link or address'
        try:
            rank = interfaces.index(name)
        except ValueError:
            return 'bound interface is no longer allowed'
        preferred = next((candidate for candidate in interfaces[:rank]
                          if interface_usable(candidate)), None)
        if preferred is None:
            better_since = None
            continue
        now = time.monotonic()
        if better_since is None or better_since[0] != preferred:
            better_since = preferred, now
            continue
        if now - better_since[1] < FAILBACK_STABLE_SECONDS:
            continue
        try:
            probe, _, _ = physical_connect((preferred,))
            probe.close()
            return 'higher-priority physical interface reachable: ' + preferred
        except OSError:
            # Link-up alone does not prove the Tencent endpoint is reachable.
            better_since = preferred, now
    return None

def handle(client):
    upstream = None
    closed = threading.Event()
    def close():
        if closed.is_set():
            return
        closed.set()
        for sock in (client, upstream):
            if sock:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                sock.close()
    def pump(source, target):
        try:
            while not closed.is_set():
                data = source.recv(65536)
                if not data: break
                target.sendall(data)
        except OSError: pass
        finally: close()
    try:
        upstream, name, index = physical_connect()
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        logging.info('NPS physical connection via %s; binding=%d', name, index)
        workers = [threading.Thread(target=pump, args=pair, daemon=True)
                   for pair in ((client, upstream), (upstream, client))]
        for worker in workers: worker.start()
        reason = watch_connection(upstream, name, index, closed)
        if reason:
            logging.info('NPS connection will re-establish: %s', reason)
        close()
        for worker in workers: worker.join(timeout=2)
    except OSError:
        logging.warning('NPS path unavailable; connection closed')
    finally:
        close()
        slots.release()

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(32)
    logging.info('Fixed-destination physical NPS relay listening on loopback')
    try:
        while True:
            client, _ = server.accept()
            if not slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=handle, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        logging.info('TCP test relay stopped')
    finally:
        server.close()

if __name__ == '__main__': main()
