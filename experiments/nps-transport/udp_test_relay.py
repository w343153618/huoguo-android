#!/usr/bin/env python3
"""Experimental QUIC/KCP bridge relay using dedicated high ports.

Reuse physical-interface binding and peer handling from the existing relay.
Only this process receives the experimental port mapping; importing this module
does not change the core defaults or start any listener.
"""

from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))
import npc_udp_physical_relay as relay

# (M1 loopback port, dedicated cloud experimental UDP bridge port).
# These do not configure the formal NPS service or the App's media endpoint.
PORTS = {'quic': (48026, 48026), 'kcp': (48025, 48025)}


def main():
    if not all(32768 <= port <= 65535 for pair in PORTS.values() for port in pair):
        raise ValueError('Independent NPS test relays require high ports')
    relay.PORTS = dict(PORTS)
    relay.main()


if __name__ == '__main__':
    main()
