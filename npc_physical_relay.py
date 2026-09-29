#!/usr/bin/env python3
"""A loopback-only, fixed-destination relay binding every NPS socket to physical NICs.

macOS IP_BOUND_IF restricts kernel routing for the outbound socket. No ordinary
unbound connect, DNS, proxy environment, or VPN fallback is ever used.
"""
import logging
import os
import socket
import sys
import threading
import time

DESTINATION = ('146.56.249.175', 8024)
LISTEN = ('127.0.0.1', 18024)
INTERFACES = tuple(os.environ.get('NPC_PHYSICAL_INTERFACES', 'en11,en0').split(','))
IP_BOUND_IF = 25  # Apple XNU bsd/netinet/in.h
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
        while not closed.wait(2):
            # Disconnect on unplug/device replacement rather than following a new route.
            try:
                if socket.if_nametoindex(name) != index:
                    break
            except OSError: break
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
    while True:
        client, _ = server.accept()
        if not slots.acquire(blocking=False):
            client.close()
            continue
        threading.Thread(target=handle, args=(client,), daemon=True).start()

if __name__ == '__main__': main()
