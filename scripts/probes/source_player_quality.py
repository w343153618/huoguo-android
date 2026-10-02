#!/usr/bin/env python3
"""Observe a player's quality menu before capture; retain numeric format only."""
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PACKAGE = 'app.morphe.android.youtube'


def parse_quality(root):
    values = []
    for node in root.iter('node'):
        match = re.fullmatch(r'Quality (\d{3,4})p(?:(30|60|120))?', node.get('content-desc', ''))
        if match:
            values.append((int(match[1]), int(match[2]) if match[2] else None))
    if len(values) != 1:
        return {'known': False, 'height': None, 'fps': None}
    return {'known': True, 'height': values[0][0], 'fps': values[0][1]}


def collect(adb, serial='emulator-5556'):
    def shell(*args):
        return subprocess.run([str(adb), '-s', serial, 'shell', *args],
                              capture_output=True, check=True, timeout=15).stdout

    def ui():
        shell('uiautomator', 'dump', '/data/local/tmp/huoguo-source-quality.xml')
        raw = shell('cat', '/data/local/tmp/huoguo-source-quality.xml')
        if len(raw) > 1024 * 1024:
            raise ValueError('UI output bound')
        return ET.fromstring(raw)

    def bounds(node):
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
        if not match:
            raise ValueError('Missing observed bounds')
        return tuple(int(x) for x in match.groups())

    result = {'known': False, 'height': None, 'fps': None, 'scope': 'observed_player_quality_menu_not_decoded_frame_count'}
    menu_open = False
    try:
        root = ui()
        button = next((n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/player_overflow_button'), None)
        if button is None:
            player = next((n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/watch_player'), None)
            if player is None:
                return dict(result, failure='not_watch_player')
            left, top, right, bottom = bounds(player)
            # Open controls away from the centre pause/previous/next buttons.
            shell('input', 'tap', str(left + (right-left)//10), str(top + (bottom-top)*2//5))
            time.sleep(.6)
            # uiautomator waits for idle and can outlast the player's controls.
            # This probe is restricted to the observed 1080-wide M1 player;
            # its overflow bounds were [936,134][1080,278]. No general UI
            # automation or quality change is attempted on other layouts.
            if right-left != 1080:
                return dict(result, failure='unverified_player_layout')
            shell('input', 'tap', str(right-72), str(top+78))
            menu_open = True
            time.sleep(.6)
            return dict(result, **parse_quality(ui()))
        if button is None:
            return dict(result, failure='quality_button_unavailable')
        left, top, right, bottom = bounds(button)
        shell('input', 'tap', str((left+right)//2), str((top+bottom)//2))
        menu_open = True
        time.sleep(.6)
        return dict(result, **parse_quality(ui()))
    except (subprocess.SubprocessError, OSError, ValueError, ET.ParseError) as error:
        return dict(result, failure=type(error).__name__)
    finally:
        if menu_open:
            try:
                shell('input', 'keyevent', 'KEYCODE_BACK')
            except (subprocess.SubprocessError, OSError):
                pass


if __name__ == '__main__':
    print(json.dumps(collect(Path.home()/'Library/Android/sdk/platform-tools/adb')))
