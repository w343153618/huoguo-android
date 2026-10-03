#!/usr/bin/env python3
"""Fixed formal NPC QUIC bridge, with explicit physical-interface selection.

This is a separate entry point: importing it neither binds a socket nor changes
the legacy core's ports. It is not an App UDP Datagram media implementation.
"""

import argparse
import logging
import os
from pathlib import Path
import re
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
import npc_udp_physical_relay as relay

PORTS = {'quic': (48126, 8025)}


def physical_interfaces(environment=None):
    environment = os.environ if environment is None else environment
    selected = environment.get('NPC_PHYSICAL_INTERFACES', '')
    interfaces = tuple(value.strip() for value in selected.split(','))
    if not interfaces or any(re.fullmatch(r'en[0-9]+', value) is None for value in interfaces):
        raise SystemExit('NPC_PHYSICAL_INTERFACES must explicitly name physical en interfaces')
    if len(set(interfaces)) != len(interfaces):
        raise SystemExit('NPC_PHYSICAL_INTERFACES must not contain duplicates')
    return interfaces


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    interfaces = physical_interfaces()
    # Only this newly started process receives the formal QUIC route. Legacy
    # defaults and the separate 48025/48026 experimental entry are untouched.
    relay.PORTS = dict(PORTS)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        relay.serve('quic', interfaces)
    except KeyboardInterrupt:
        logging.info('Formal QUIC relay stopped')


if __name__ == '__main__':
    main()
