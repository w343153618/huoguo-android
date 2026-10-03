#!/usr/bin/env python3
"""Run the existing NPS client identity in command mode without a key in argv.

Configuration is kept outside Git. Command mode uses the existing client key
associated with this deployment's fixed public tunnel. The two exact loopback
routes permit an explicit QUIC deployment and the original TCP rollback. M5 can
reuse this wrapper by setting M1_NPC_CONFIG and M1_NPC_BINARY to its own paths.
"""
import configparser
import os
from pathlib import Path
import stat

ALLOWED_ROUTES = frozenset((('127.0.0.1:18024', 'tcp'),
                           ('127.0.0.1:48126', 'quic')))


def execution(environment=None):
    """Read the private identity and build an exec request, without logging it."""
    environment = dict(os.environ if environment is None else environment)
    config_path = Path(environment.get('M1_NPC_CONFIG',
        str(Path.home()/'.config/huoguo-android/m1-npc.conf')))
    file_stat = config_path.stat()
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise SystemExit('NPC identity file must be a regular 0600 file')
    config = configparser.ConfigParser(interpolation=None)
    try:
        with config_path.open() as source:
            config.read_file(source)
        section = config['common']
        address, protocol, key = section['server_addr'], section['conn_type'], section['vkey']
    except (configparser.Error, KeyError):
        # Parser errors can include the offending line, which could be a vkey.
        raise SystemExit('NPC identity configuration is invalid') from None
    if (address, protocol) not in ALLOWED_ROUTES:
        raise SystemExit('NPC must use an allowed physical loopback relay')
    if not key:
        raise SystemExit('NPC identity is empty')
    binary = Path(environment.get('M1_NPC_BINARY',
        str(Path.home()/'.local/share/huoguo-android/npc/v0.34.7/npc')))
    environment.update(NPC_SERVER_VKEY=key, NPC_SERVER_ADDR=address)
    environment.pop('NPC_CONFIG_PATH', None)
    arguments = [str(binary), '-type='+protocol, '-log=stdout',
                 '-log_level=warn', '-debug=false']
    return binary, arguments, environment


def main():
    binary, arguments, environment = execution()
    os.execve(binary, arguments, environment)


if __name__ == '__main__':
    main()
