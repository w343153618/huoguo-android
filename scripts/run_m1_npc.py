#!/usr/bin/env python3
"""Run the existing NPS client identity in command mode without a key in argv.

Configuration is kept outside Git. Command mode uses the existing client key
associated with this deployment's fixed public tunnel.
"""
import configparser
import os
from pathlib import Path

config_path = Path(os.environ.get('M1_NPC_CONFIG', str(Path.home()/'.config/huoguo-android/m1-npc.conf')))
if config_path.stat().st_mode & 0o077:
    raise SystemExit('NPC identity file must be owner-only')
config=configparser.ConfigParser(interpolation=None);config.read(config_path)
section=config['common']
if section['server_addr']!='127.0.0.1:18024' or section['conn_type']!='tcp':
    raise SystemExit('NPC must use the physical loopback relay')
binary=Path(os.environ.get('M1_NPC_BINARY', str(Path.home()/'.local/share/huoguo-android/npc/v0.34.7/npc')))
environment=dict(os.environ,NPC_SERVER_VKEY=section['vkey'],NPC_SERVER_ADDR='127.0.0.1:18024')
environment.pop('NPC_CONFIG_PATH',None)
os.execve(binary,[str(binary),'-type=tcp','-log=stdout','-log_level=warn','-debug=false'],environment)
