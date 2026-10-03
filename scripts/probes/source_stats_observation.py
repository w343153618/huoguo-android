#!/usr/bin/env python3
"""Bounded current Stats snapshot with a fresh process/session/focus bracket.

Default CLI is inert. Explicit collection never opens menus or resumes a player.
UI XML travels in the existing ADB stdout pipe from one private, owned device
temporary directory. Success requires its removal receipt; no host XML file.
Descriptor FPS is not measured decoding, rendering or motion.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import time

try:
    from scripts.probes import source_decoder_observation as observation
    from scripts.probes import source_ui_stats as stats
except ModuleNotFoundError:
    import source_decoder_observation as observation
    import source_ui_stats as stats

UI_CREATED = b'__HUOGUO_UI_OWNED_CREATED__\n'
UI_DONE = b'\n__HUOGUO_UI_DUMP_CLEANUP_DONE__\n'
FOCUS = re.compile(rb'[ \t]*mCurrentFocus=Window\{[A-Za-z0-9]+ u0 '
                   + re.escape(stats.PACKAGE.encode()) + rb'/[A-Za-z0-9_.$]+\}[ \t]*')
SCHEMA = 'source-stats-observation-v1'


def owned_directory(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r'[a-f0-9]{24}', nonce):
        raise ValueError('internal_nonce')
    return '/data/local/tmp/huoguo-source-stats-' + nonce


def snapshot_script(nonce):
    # Atomic mkdir establishes ownership before the receipt. The private parent
    # bounds access even if the UI tool's file-mode behavior differs by release.
    directory = owned_directory(nonce)
    return ('umask 077; d=' + directory + '; mkdir "$d" || exit 20; '
            'printf "__HUOGUO_UI_OWNED_CREATED__\\n"; '
            'trap \'rm -f "$d/ui.xml"; rmdir "$d"\' EXIT; '
            'uiautomator dump "$d/ui.xml" >/dev/null 2>&1 || exit 21; '
            '[ -f "$d/ui.xml" ] && [ ! -L "$d/ui.xml" ] || exit 22; '
            'cat "$d/ui.xml" || exit 23; '
            'rm -f "$d/ui.xml" && rmdir "$d" || exit 24; trap - EXIT; '
            'printf "\\n__HUOGUO_UI_DUMP_CLEANUP_DONE__\\n"')


def cleanup_script(nonce):
    return ('d=' + owned_directory(nonce) + '; [ ! -e "$d" ] && exit 0; '
            '[ -d "$d" ] && [ ! -L "$d" ] || exit 25; '
            'rm -f "$d/ui.xml" && rmdir "$d" || exit 26; [ ! -e "$d" ]')


def parse_foreground(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > observation.MAX_DUMP_BYTES:
        return False
    rows = [line for line in bytes(raw).splitlines() if b'mCurrentFocus=' in line]
    return len(rows) == 1 and FOCUS.fullmatch(rows[0]) is not None


def parse_pipe_snapshot(raw, expected_video_id):
    """Keep completion separate from whether a closed Stats XML is valid."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) >= stats.MAX_BYTES:
        return stats._unknown('pipe_rejected'), False
    raw = bytes(raw)
    if (not raw.startswith(UI_CREATED) or not raw.endswith(UI_DONE)
            or raw.count(UI_CREATED) != 1 or raw.count(UI_DONE) != 1):
        return stats._unknown('pipe_rejected'), False
    return stats.parse_stats(raw[len(UI_CREATED):-len(UI_DONE)], expected_video_id), True


def collect(adb, serial, expected_video_id, *, required_state='paused', timeout=15,
            reader_factory=observation.BoundedReader):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 3 <= timeout <= 15):
        raise ValueError('invalid_bounded_timeout')
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', serial):
        raise ValueError('invalid_device_selector')
    if not isinstance(expected_video_id, str) or not stats.VIDEO_ID.fullmatch(expected_video_id):
        raise ValueError('expected_video_id')
    if required_state not in ('paused', 'playing'):
        raise ValueError('required_state')
    reader = reader_factory(adb, serial, timeout)

    def read_parse(args, parser, fallback, bound=observation.MAX_DUMP_BYTES):
        raw, info = reader.read(args, bound)
        try:
            result = parser(raw) if info['command_ok'] else fallback()
            return result, info
        finally:
            raw.clear()

    def identity():
        value, info = read_parse([observation.IDENTITY_SCRIPT], observation.parse_identity,
                                 observation.unknown_identity, observation.IDENTITY_BYTES)
        return dict(value, **info)

    def focus():
        value, info = read_parse(['dumpsys', '-t', '3', 'window'],
                                 parse_foreground, lambda: False)
        return dict(foreground=value, **info)

    def state():
        def parse(raw):
            value = observation.playback.parse_dump(bytes(raw))
            return value, observation.active_owner(raw) if not value['unknown'] else None
        (value, owner), info = read_parse(['dumpsys', '-t', '3', 'media_session'], parse,
                                         lambda: (observation.playback.unknown(), None))
        return dict(value, **info), owner

    before = identity()
    focus_before = focus()
    state_before, owner_before = state()
    nonce = os.urandom(12).hex()
    raw, ui_info = reader.read([snapshot_script(nonce)])
    try:
        created = raw.startswith(UI_CREATED)
        ui, ui_complete = (parse_pipe_snapshot(raw, expected_video_id)
                           if ui_info['command_ok'] else (stats._unknown('command_failed'), False))
    finally:
        raw.clear()
    cleanup_info = None
    temp_removed = ui_complete
    if created and not ui_complete:
        raw, cleanup_info = reader.read([cleanup_script(nonce)], observation.IDENTITY_BYTES)
        try:
            temp_removed = cleanup_info['command_ok'] and not raw
        finally:
            raw.clear()
    state_after, owner_after = state()
    focus_after = focus()
    after = identity()
    stable = before['known'] and after['known'] and all(
        before[k] == after[k] for k in ('pid', 'uid', 'start_ticks'))
    owners = (before['known'] and owner_before == (before['pid'], before['uid'])
              and after['known'] and owner_after == (after['pid'], after['uid']))
    desired = 2 if required_state == 'paused' else 3
    state_match = all(not s['unknown'] and s['state'] == desired
                      and (desired != 3 or s['speed'] == 1) for s in (state_before, state_after))
    # The transient controls can disappear during playing. A visible contrary
    # state rejects; absence never replaces the independently bound MediaSession.
    ui_state_match = ui['player_ui_state'] in (None, required_state)
    finished = time.monotonic_ns()
    all_commands = (before, focus_before, state_before, ui_info, state_after, focus_after, after)
    completed = all(v['command_ok'] and v['child_reaped'] for v in all_commands)
    qualified = (completed and not reader.unreaped and stable and owners
                 and focus_before['foreground'] and focus_after['foreground']
                 and state_match and ui_state_match and ui_complete and ui['format_known']
                 and 0 <= finished - reader.started_ns <= int(timeout * 1e9))
    return {'schema': SCHEMA, 'qualified': bool(qualified), 'required_state': required_state,
            'identity_before': before, 'identity_after': after, 'identity_stable': bool(stable),
            'focus_before': focus_before, 'focus_after': focus_after,
            'playback_before': state_before, 'playback_after': state_after,
            'session_owner_matches_bracket': bool(owners), 'state_matches_bracket': bool(state_match),
            'stats': ui, 'ui_command': ui_info, 'remote_uia_completion_receipt': ui_complete,
            'device_UI_temp_created': created, 'device_UI_temp_removal_confirmed': temp_removed,
            'fallback_cleanup_command': cleanup_info,
            'all_local_children_reaped': not reader.unreaped,
            'remote_uia_status': 'completed' if ui_complete else 'unconfirmed',
            'host_UI_files_created': False,
            'raw_total_bytes': reader.total_bytes,
            'host_started_python_monotonic_ns': reader.started_ns,
            'host_finished_python_monotonic_ns': finished,
            'visual_motion_observed': None, 'decoded_or_presented_fps': None,
            'overlay_on_during_collection': ui['format_known'],
            'overlay_or_collection_overhead_measured': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--adb', type=Path, default=Path.home() / 'Library/Android/sdk/platform-tools/adb')
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--expected-video-id', default='aqz-KE-bpKQ')
    parser.add_argument('--required-state', choices=('paused', 'playing'), default='paused')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'phase': 'prepared_not_collected', 'device_operations': 0}))
        return 0
    try:
        result = collect(args.adb, args.serial, args.expected_video_id, required_state=args.required_state)
    except (ValueError, OSError):
        print(json.dumps({'phase': 'collection_refused', 'qualified': False}))
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True, separators=(',', ':')))
    return 0 if result['qualified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
