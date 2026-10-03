#!/usr/bin/env python3
"""Parse a bounded, already collected Morphe Stats UI snapshot without devices.

This utility never opens menus, reads credentials, or launches ADB. Its format
fields describe the player's current UI allocation, not decoded/presented FPS.
The caller must separately bind the snapshot to a fresh process and session.
"""
import argparse
import json
import os
import re
import stat
import xml.etree.ElementTree as ET

PACKAGE = 'app.morphe.android.youtube'
PREFIX = PACKAGE + ':id/'
MAX_BYTES = 1024 * 1024
MAX_NODES = 512
MAX_DEPTH = 32
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
CODEC = r'(?:avc1(?:\.[0-9a-fA-F]{1,16})?|vp09(?:\.[0-9.]{1,48})?|av01(?:\.[0-9A-Za-z.]{1,48})?)'
SUFFIX = r'(?:\s+\([A-Za-z0-9 ._-]{1,64}\))?'
VIDEO = re.compile(r'(\d{1,6})\s+(' + CODEC + r')\s+(\d{1,4})x(\d{1,4})@(\d{1,3}(?:\.\d{1,3})?)' + SUFFIX)
AUDIO = re.compile(r'(\d{1,6})(?::[A-Za-z0-9_-]{1,80})?\s+(opus|aac|mp4a(?:\.[0-9.]{1,48})?)' + SUFFIX)
CONTROL_CHARS = dict.fromkeys(map(ord, '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069'), None)


class Rejected(ValueError):
    pass


def _text(value):
    if len(value) > 256:
        raise Rejected('text_bound')
    return value.translate(CONTROL_CHARS).strip()


def _unknown(reason):
    return {'schema': 1, 'status': reason, 'format_known': False,
            'video_id': None, 'video_format': None, 'audio_format': None,
            'viewport': None, 'dropped_frames_cumulative': None,
            'player_ui_state': None, 'position_ui_seconds': None,
            'process_identity_verified': False, 'observed_content_fps': None,
            'decoded_or_presented_fps_verified': False}


def parse_stats(raw, expected_video_id=None):
    """Return closed numeric fields; ambiguous/foreign/malformed UI stays unknown."""
    if expected_video_id is not None and (not isinstance(expected_video_id, str)
                                          or not VIDEO_ID.fullmatch(expected_video_id)):
        raise ValueError('expected_video_id')
    result = _unknown('unavailable')
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise Rejected('byte_bound')
        # ElementTree does not resolve external resources; also reject all DTDs
        # before parsing rather than allowing internal entity expansion.
        decoded = raw.decode('utf-8')
        if '\x00' in decoded or '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
            raise Rejected('doctype')
        root = ET.fromstring(decoded)
        stack = [(root, 0)]
        nodes = []
        while stack:
            node, depth = stack.pop()
            if depth > MAX_DEPTH or len(nodes) >= MAX_NODES:
                raise Rejected('tree_bound')
            nodes.append(node)
            stack.extend((child, depth + 1) for child in node)
        if root.tag != 'hierarchy':
            raise Rejected('root')
        layouts = [n for n in nodes if n.get('resource-id') == PREFIX + 'nerd_stats_layout']
        if not layouts:
            return result
        if len(layouts) != 1:
            raise Rejected('duplicate_layout')
        layout = layouts[0]
        if (layout.tag != 'node' or layout.get('package') != PACKAGE
                or layout.get('class') != 'android.widget.RelativeLayout'):
            raise Rejected('foreign_layout')

        def value(name, required=True):
            entries = [n for n in layout.iter('node') if n.get('resource-id') == PREFIX + name]
            if not entries and not required:
                return None
            if len(entries) != 1:
                raise Rejected('missing_or_duplicate_field')
            node = entries[0]
            if node.get('package') != PACKAGE or node.get('class') != 'android.widget.TextView':
                raise Rejected('foreign_field')
            return _text(node.get('text', ''))

        ident = value('video_id')
        if not VIDEO_ID.fullmatch(ident):
            raise Rejected('video_id')
        if expected_video_id is not None and ident != expected_video_id:
            return _unknown('different_video')
        video = VIDEO.fullmatch(value('video_format'))
        audio = AUDIO.fullmatch(value('audio_format'))
        if video is None or audio is None:
            raise Rejected('format')
        itag, codec, width, height, fps = video.groups()
        width, height, fps = int(width), int(height), float(fps)
        if not (16 <= width <= 8192 and 16 <= height <= 8192 and 1 <= fps <= 240):
            raise Rejected('format_bounds')
        result.update(status='observed', format_known=True, video_id=ident,
            video_format={'itag': int(itag), 'codec': codec, 'width': width,
                          'height': height, 'descriptor_fps': fps},
            audio_format={'itag': int(audio[1]), 'codec': audio[2]})

        viewport = value('viewport', False)
        if viewport is not None:
            match = re.fullmatch(r'(\d{1,4})x(\d{1,4})x(true|false)', viewport)
            if match is None or not all(1 <= int(match[i]) <= 8192 for i in (1, 2)):
                raise Rejected('viewport')
            result['viewport'] = {'width': int(match[1]), 'height': int(match[2]),
                                  'flag': match[3] == 'true'}
        dropped = value('dropped_frames', False)
        if dropped is not None:
            match = re.fullmatch(r'(\d{1,15})\s*/\s*(\d{1,15})', dropped)
            if match is None or int(match[1]) > int(match[2]):
                raise Rejected('dropped_frames')
            result['dropped_frames_cumulative'] = {'dropped': int(match[1]), 'total': int(match[2])}

        for name, output in (('player_control_play_pause_replay_button', 'player_ui_state'),
                             ('time_bar_current_time', 'position_ui_seconds')):
            entries = [n for n in nodes if n.get('resource-id') == PREFIX + name]
            if len(entries) > 1:
                raise Rejected('ambiguous_player_state')
            if not entries:
                continue
            node = entries[0]
            if node.get('package') != PACKAGE:
                raise Rejected('foreign_player_state')
            if output == 'player_ui_state':
                result[output] = {'Play video': 'paused', 'Pause video': 'playing',
                                  'Replay video': 'ended'}.get(node.get('content-desc'))
            else:
                current = _text(node.get('text', ''))
                match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})', current)
                if match and int(match[2]) < 60 and int(match[3]) < 60:
                    result[output] = int(match[1] or 0) * 3600 + int(match[2]) * 60 + int(match[3])
        return result
    except (Rejected, ET.ParseError, UnicodeDecodeError):
        return _unknown('rejected')


def read_owned(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BYTES):
            raise ValueError('owned_bounded_regular_snapshot_required')
        return stream.read(MAX_BYTES + 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input')
    parser.add_argument('--expected-video-id')
    args = parser.parse_args(argv)
    if args.input is None:
        print(json.dumps({'phase': 'prepared_not_collected', 'device_operations': 0}))
        return 0
    try:
        result = parse_stats(read_owned(args.input), args.expected_video_id)
    except (OSError, ValueError):
        result = _unknown('input_rejected')
    print(json.dumps(result, separators=(',', ':'), sort_keys=True))
    return 0 if result['format_known'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
