#!/usr/bin/env python3
"""Read only Morphe's active media-session state; never emit raw dumpsys content."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

TARGET_PACKAGE = 'app.morphe.android.youtube'
MAX_DUMP_BYTES = 1024 * 1024
PACKAGE = re.compile(r'^(?P<indent>[ \t]{0,64})package=(?P<package>[A-Za-z0-9._]+)\s*$')
OWNER = re.compile(r'ownerPid=\d+,\s*ownerUid=\d+,\s*userId=\d+\s*$')
# AOSP PlaybackState.toString() uses NAME(number); older dumps use number.
# Both are accepted only inside the anchored outer PlaybackState value, and
# the symbolic spelling must agree with the numeric AOSP state.
STATE_NAMES = {name: state for state, name in enumerate((
    'NONE', 'STOPPED', 'PAUSED', 'PLAYING', 'FAST_FORWARDING', 'REWINDING',
    'BUFFERING', 'ERROR', 'CONNECTING', 'SKIPPING_TO_PREVIOUS',
    'SKIPPING_TO_NEXT', 'SKIPPING_TO_QUEUE_ITEM'))}
PLAYBACK = re.compile(
    r'^state=PlaybackState\s*\{\s*state=(?:(?P<state_name>[A-Z_]{1,32})\('
    r'(?P<named_state>\d{1,2})\)|(?P<numeric_state>\d{1,2})),\s*position=(?P<position>-?\d{1,19}),'
    r'\s*buffered position=-?\d{1,19},\s*speed=(?P<speed>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
    r',\s*updated=(?P<updated>\d{1,19})(?:\s*,|\s*\})')


def unknown():
    # Unknown is explicit; zero is not presented as a paused/stopped player.
    return {'schema': 1, 'unknown': True, 'matching_sessions': 0, 'active_sessions': 0,
            'state_known': False, 'state': None, 'position_ms': None, 'updated_elapsed_ms': None,
            'speed': None, 'media_fps_known': False, 'media_fps': None}


def parse_dump(raw):
    """Parse only numeric PlaybackState from one unambiguous active target session.

    The raw value is never placed in returned objects, log messages, exceptions,
    files or command arguments. AOSP package/owner/active/state indentation is
    used to avoid attributing another session's playback state to the target.
    """
    result = unknown()
    if not isinstance(raw, bytes) or len(raw) > MAX_DUMP_BYTES:
        return result
    lines = raw.decode('utf-8', errors='replace').splitlines()
    matching, active = 0, []
    for index, line in enumerate(lines):
        package = PACKAGE.fullmatch(line)
        if not package or package['package'] != TARGET_PACKAGE:
            continue
        indent = package['indent']
        if index == 0 or not lines[index-1].startswith(indent) or not OWNER.fullmatch(lines[index-1][len(indent):]):
            continue
        matching += 1
        enabled, playback = False, None
        for following in lines[index+1:]:
            if following.strip() and (not following.startswith(indent) or PACKAGE.fullmatch(following)):
                break
            # Inspect same-level fields only. Nested descriptions and metadata
            # cannot supply the accepted active/state fields.
            if not following.startswith(indent):
                continue
            field = following[len(indent):]
            if field.startswith((' ', '\t')):
                continue
            if field in ('active=true', 'active=false'):
                enabled = field == 'active=true'
            elif field.startswith('state='):
                match = PLAYBACK.match(field[:1024])
                if match:
                    state = int(match['named_state'] or match['numeric_state'])
                    position, speed, updated = int(match['position']), float(match['speed']), int(match['updated'])
                    valid_name = match['state_name'] is None or STATE_NAMES.get(match['state_name']) == state
                    if (valid_name and 0 <= state <= 11 and -1 <= position <= 9223372036854775807 and
                            0 <= updated <= 9223372036854775807 and math.isfinite(speed) and abs(speed) <= 1000):
                        playback = (state, position, speed, updated)
        if enabled:
            active.append(playback)
    result['matching_sessions'], result['active_sessions'] = matching, len(active)
    if len(active) == 1 and active[0] is not None:
        state, position, speed, updated = active[0]
        result.update(unknown=False, state_known=True, state=state, position_ms=position,
                      updated_elapsed_ms=updated, speed=speed)
    return result


def collect(adb, serial, timeout):
    """One bounded read, in memory only. No root, transport key or device write."""
    begin = time.monotonic_ns()
    info = {'command_ok': False, 'error_code': 0, 'host_started_monotonic_ns': begin,
            'host_finished_monotonic_ns': begin}
    # error_code: 0 success, 1 executable/launch, 2 timeout, 3 output bound,
    # 4 command exit, 5 local read. No stderr, exception text, serial or paths
    # are emitted.
    try:
        process = subprocess.Popen([str(adb), '-s', serial, 'shell', 'dumpsys', 'media_session'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        info.update(error_code=1, host_finished_monotonic_ns=time.monotonic_ns())
        return dict(unknown(), **info)
    raw = bytearray(); status = 0; snapshot = unknown()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = begin / 1e9 + timeout
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = 2; break
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, min(65536, MAX_DUMP_BYTES-len(raw)+1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        raw.extend(chunk)
                        if len(raw) > MAX_DUMP_BYTES:
                            status = 3; break
                if status:
                    break
        if status:
            process.kill()
        try:
            code = process.wait(timeout=max(.01, deadline-time.monotonic()) if not status else 2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2); status = 2
        if not status and code != 0:
            status = 4
        snapshot = parse_dump(bytes(raw)) if status == 0 else unknown()
    except OSError:
        status = 5
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=2)
        process.stdout.close()
        raw.clear()
    info.update(command_ok=status == 0, error_code=status, host_finished_monotonic_ns=time.monotonic_ns())
    return dict(snapshot, **info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--adb', type=Path, default=Path.home()/'Library/Android/sdk/platform-tools/adb')
    parser.add_argument('--timeout', type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 20 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', args.serial):
        parser.error('Invalid bounded timeout/device selector')
    print(json.dumps(collect(args.adb, args.serial, args.timeout), allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
