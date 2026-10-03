#!/usr/bin/env python3
"""Bounded read-only Morphe state/identity observation; decoder format stays unknown."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

try:
    from scripts.probes import source_playback_state as playback
except ModuleNotFoundError:
    import source_playback_state as playback

MAX_DUMP_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * MAX_DUMP_BYTES
IDENTITY_BYTES = 8192
CLEANUP_RESERVE_NS = 2_000_000_000
COMMAND_NS = 3_000_000_000
STATUS_MARKER = b'\n__HUOGUO_STATUS__\n'
STAT_MARKER = b'\n__HUOGUO_STAT__\n'
OWNER = re.compile(r'ownerPid=(\d+),\s*ownerUid=(\d+),\s*userId=(\d+)\s*$')
# The package is a fixed repository constant, not a caller-supplied shell value.
IDENTITY_SCRIPT = (
    'pids=$(pidof ' + playback.TARGET_PACKAGE + ') || exit 10; '
    'set -- $pids; [ "$#" -eq 1 ] || exit 11; p=$1; '
    'case "$p" in ""|*[!0-9]*) exit 12;; esac; '
    'printf "%s\\n" "$p"; cat /proc/"$p"/cmdline || exit 13; '
    'printf "\\n__HUOGUO_STATUS__\\n"; cat /proc/"$p"/status || exit 14; '
    'printf "\\n__HUOGUO_STAT__\\n"; cat /proc/"$p"/stat || exit 15'
)


def command_info():
    return {'command_ok': False, 'error_code': 0, 'raw_bytes': 0,
            'host_started_monotonic_ns': None, 'host_finished_monotonic_ns': None,
            'child_reaped': True}


class BoundedReader:
    """Only owns its local ADB children; no process signals to device services."""
    def __init__(self, adb, serial, timeout):
        self.adb, self.serial = str(adb), serial
        self.started_ns = time.monotonic_ns()
        self.deadline_ns = self.started_ns + int(timeout * 1e9)
        self.read_deadline_ns = self.deadline_ns - CLEANUP_RESERVE_NS
        self.total_bytes = 0
        self.unreaped = False

    def read(self, args, byte_limit=MAX_DUMP_BYTES):
        info = command_info()
        begin = time.monotonic_ns()
        info['host_started_monotonic_ns'] = begin
        raw = bytearray()
        remaining_bytes = MAX_TOTAL_BYTES - self.total_bytes
        if self.unreaped or begin >= self.read_deadline_ns:
            info.update(error_code=7, host_finished_monotonic_ns=begin)
            return raw, info
        if remaining_bytes <= 0:
            info.update(error_code=6, host_finished_monotonic_ns=begin)
            return raw, info
        limit = min(byte_limit, MAX_DUMP_BYTES, remaining_bytes)
        read_end = min(self.read_deadline_ns, begin + COMMAND_NS)
        process = None
        try:
            process = subprocess.Popen([self.adb, '-s', self.serial, 'shell', *args],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = (read_end - time.monotonic_ns()) / 1e9
                    if remaining <= 0:
                        info['error_code'] = 2
                        break
                    for key, _ in selector.select(remaining):
                        try:
                            chunk = os.read(key.fd, min(65536, limit-len(raw)))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        raw.extend(chunk)
                        self.total_bytes += len(chunk)
                        # At the limit, conservatively reject without reading an
                        # extra byte. Exact-limit output is also unknown.
                        if len(raw) >= limit:
                            info['error_code'] = 3
                            break
                    if info['error_code']:
                        break
            if not info['error_code']:
                try:
                    code = process.wait(timeout=max(0, (read_end-time.monotonic_ns())/1e9))
                    if code != 0:
                        info['error_code'] = 4
                except subprocess.TimeoutExpired:
                    info['error_code'] = 2
        except (OSError, ValueError):
            info['error_code'] = 1 if process is None else 5
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=max(0, min(2, (self.deadline_ns-time.monotonic_ns())/1e9)))
                    except (OSError, subprocess.TimeoutExpired):
                        info['child_reaped'] = False
                        self.unreaped = True
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        info['error_code'] = info['error_code'] or 5
            info.update(command_ok=info['error_code'] == 0 and info['child_reaped'],
                        raw_bytes=len(raw), host_finished_monotonic_ns=time.monotonic_ns())
        return raw, info


def unknown_identity():
    return {'known': False, 'pid': None, 'uid': None, 'start_ticks': None}


def parse_identity(raw):
    """Exact cmdline + PID/Tgid/UID + /proc stat startticks, never emit text."""
    out = unknown_identity()
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > IDENTITY_BYTES:
        return out
    raw = bytes(raw)
    if raw.count(STATUS_MARKER) != 1 or raw.count(STAT_MARKER) != 1:
        return out
    head, rest = raw.split(STATUS_MARKER)
    status, stat = rest.split(STAT_MARKER)
    try:
        pid_raw, cmdline = head.split(b'\n', 1)
        if not re.fullmatch(rb'[1-9][0-9]{0,9}', pid_raw):
            return out
        pid = int(pid_raw)
        package = playback.TARGET_PACKAGE.encode('ascii')
        # Android argv0 renaming can zero-fill the remaining original argument
        # space. Accept only the exact package followed by one or more NULs;
        # another argument or a process-name suffix must still be rejected.
        if len(cmdline) <= len(package) or cmdline.rstrip(b'\0') != package:
            return out
        fields = {}
        for name in (b'Pid', b'Tgid', b'Uid'):
            matches = re.findall(rb'^' + name + rb':[ \t]*([^\r\n]*)$', status, re.MULTILINE)
            if len(matches) != 1:
                return out
            fields[name] = matches[0].split()
        if fields[b'Pid'] != [pid_raw] or fields[b'Tgid'] != [pid_raw]:
            return out
        uids = fields[b'Uid']
        if len(uids) != 4 or any(not re.fullmatch(rb'[0-9]{1,10}', value) for value in uids):
            return out
        if len(set(uids)) != 1:
            return out
        uid = int(uids[0])
        # comm may contain spaces or parentheses; field22 is relative to the
        # last closing parenthesis, not a naive split of the complete stat row.
        match = re.fullmatch(rb'([1-9][0-9]{0,9}) \([^\r\n]*\) ([^\r\n]+)\n?', stat)
        if not match or int(match[1]) != pid:
            return out
        suffix = match[2].split()
        if len(suffix) < 20 or not re.fullmatch(rb'[A-Za-z]', suffix[0]):
            return out
        if not re.fullmatch(rb'[0-9]{1,19}', suffix[19]):
            return out
        start = int(suffix[19])
        if not (0 < pid <= 2147483647 and 0 <= uid <= 2147483647 and 0 < start <= 9223372036854775807):
            return out
        out.update(known=True, pid=pid, uid=uid, start_ticks=start)
    except (ValueError, OverflowError):
        pass
    return out


def active_owner(raw):
    """Same anchored package/indent boundary as the reused PlaybackState parser."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_DUMP_BYTES:
        return None
    lines = bytes(raw).decode('utf-8', errors='replace').splitlines()
    active = []
    for index, line in enumerate(lines):
        package = playback.PACKAGE.fullmatch(line)
        if not package or package['package'] != playback.TARGET_PACKAGE or index == 0:
            continue
        indent = package['indent']
        if not lines[index-1].startswith(indent):
            continue
        owner = OWNER.fullmatch(lines[index-1][len(indent):])
        if owner is None:
            continue
        enabled = []
        for following in lines[index+1:]:
            if following.strip() and (not following.startswith(indent) or playback.PACKAGE.fullmatch(following)):
                break
            field = following[len(indent):] if following.startswith(indent) else ''
            if field in ('active=true', 'active=false'):
                enabled.append(field == 'active=true')
        if len(enabled) != 1:
            return None
        if enabled[0]:
            values = tuple(int(value) for value in owner.groups())
            if any(value > 2147483647 for value in values):
                return None
            active.append(values[:2])
    return active[0] if len(active) == 1 else None


def structure(raw, info, historical):
    # Bytes/lines are availability only; no installed decoder schema is parsed.
    try:
        raw.decode('utf-8', errors='strict')
        valid_utf8 = True
        line_count = raw.count(b'\n') + int(bool(raw) and not raw.endswith(b'\n'))
    except UnicodeDecodeError:
        valid_utf8, line_count = False, None
    return dict(info, raw_available=info['command_ok'] and bool(raw),
                utf8_valid=valid_utf8, line_count=line_count,
                historical_queue_only=historical, installed_schema_validated=False,
                codec_identity_known=False, live_format_known=False)


def collect(adb, serial, timeout=15, *, reader_factory=BoundedReader):
    if not math.isfinite(timeout) or not 3 <= timeout <= 15:
        raise ValueError('invalid_bounded_timeout')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', serial):
        raise ValueError('invalid_device_selector')
    reader = reader_factory(adb, serial, timeout)

    def identity():
        raw, info = reader.read([IDENTITY_SCRIPT], IDENTITY_BYTES)
        try:
            return dict(parse_identity(raw) if info['command_ok'] else unknown_identity(), **info)
        finally:
            raw.clear()

    def state():
        raw, info = reader.read(['dumpsys', '-t', '3', 'media_session'])
        try:
            value = playback.parse_dump(bytes(raw)) if info['command_ok'] else playback.unknown()
            owner = active_owner(raw) if info['command_ok'] and not value['unknown'] else None
            return dict(value, **info), owner
        finally:
            raw.clear()

    before = identity()
    state_before, owner_before = state()
    raw, info = reader.read(['dumpsys', '-t', '3', 'media.metrics', '--prefix', 'codec', '--since', '-15'])
    try:
        metrics = structure(raw, info, True)
    finally:
        raw.clear()
    raw, info = reader.read(['dumpsys', '-t', '3', 'media.codec'])
    try:
        codec = structure(raw, info, False)
    finally:
        raw.clear()
    state_after, owner_after = state()
    after = identity()
    stable = before['known'] and after['known'] and all(before[key] == after[key] for key in ('pid', 'uid', 'start_ticks'))
    matched_before = before['known'] and owner_before == (before['pid'], before['uid'])
    matched_after = after['known'] and owner_after == (after['pid'], after['uid'])
    position_known = stable and matched_before and matched_after and all(
        value['position_ms'] is not None and value['position_ms'] >= 0 for value in (state_before, state_after))
    return {'schema': 1, 'identity_before': before, 'identity_after': after,
            'playback_before': state_before, 'playback_after': state_after,
            'identity_stable': stable, 'session_owner_matches_before': matched_before,
            'session_owner_matches_after': matched_after,
            'playing_state_bracket': stable and matched_before and matched_after and all(
                value['state'] == 3 and value['speed'] == 1 for value in (state_before, state_after)),
            'reported_position_changed': state_before['position_ms'] != state_after['position_ms'] if position_known else None,
            'metrics': metrics, 'codec': codec,
            'live_format_known': False, 'width': None, 'height': None, 'mime_enum': None,
            'configured_fps_known': False, 'configured_fps': None,
            'content_fps_known': False, 'content_fps': None,
            'itag_known': False, 'itag': None, 'source_bitrate_known': False, 'source_bitrate': None,
            'raw_total_bytes': reader.total_bytes, 'all_children_reaped': not reader.unreaped,
            'host_started_monotonic_ns': reader.started_ns,
            'host_finished_monotonic_ns': time.monotonic_ns()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', type=Path, default=Path.home()/'Library/Android/sdk/platform-tools/adb')
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--timeout', type=float, default=15)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 3 <= args.timeout <= 15 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', args.serial):
        parser.error('Invalid bounded timeout/device selector')
    print(json.dumps(collect(args.adb, args.serial, args.timeout), allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
