"""Inert play/pause target extraction for a future authenticated owner input test.

Only parses an already collected paused/playing Morphe Stats snapshot. It never
opens menus, sends input, authenticates, reads a store, or takes a guest lease.
JSON success proves neither process ownership nor an executed touch or playback.
"""
import re
import xml.etree.ElementTree as ET

from scripts.probes import source_ui_stats as stats


def _unknown(reason):
    return {'schema': 'owner-source-remote-target-v1', 'available': False,
            'reason': reason, 'process_identity_or_attempt_verified': False,
            'input_executed': False, 'playback_transition_verified': False}


def parse_target(raw, *, expected_video_id, required_state, display_width, display_height):
    if not isinstance(expected_video_id, str) or not stats.VIDEO_ID.fullmatch(expected_video_id):
        raise ValueError('expected_video_id_required')
    if required_state not in ('paused', 'playing'):
        raise ValueError('source_state_bound')
    if (type(display_width) is not int or type(display_height) is not int
            or not 16 <= display_width <= 8192 or not 16 <= display_height <= 8192):
        raise ValueError('source_display_geometry_bound')
    description = stats.parse_stats(raw, expected_video_id)
    if not description['format_known'] or description['player_ui_state'] != required_state:
        return _unknown('qualified_Stats_and_visible_player_state_required')
    # parse_stats already rejects DTD/entities, oversized/deep trees and foreign
    # or duplicate fields. Parse the same bounded bytes; never a later file read.
    root = ET.fromstring(raw.decode('utf8'))
    nodes = [n for n in root.iter('node')
             if n.get('resource-id') == stats.PREFIX + 'player_control_play_pause_replay_button']
    if len(nodes) != 1:
        return _unknown('unique_player_control_required')
    node = nodes[0]
    label = 'Play video' if required_state == 'paused' else 'Pause video'
    if (node.get('package') != stats.PACKAGE or node.get('class') != 'android.widget.ImageButton'
            or node.get('content-desc') != label or node.get('clickable') != 'true'
            or node.get('enabled') != 'true'):
        return _unknown('enabled_native_player_control_required')
    match = re.fullmatch(r'\[(\d{1,5}),(\d{1,5})\]\[(\d{1,5}),(\d{1,5})\]', node.get('bounds', ''))
    if match is None:
        return _unknown('player_control_bounds')
    x0, y0, x1, y1 = map(int, match.groups())
    if not (0 <= x0 < x1 <= display_width and 0 <= y0 < y1 <= display_height):
        return _unknown('player_control_bounds')
    # Integer half-up normalized image coordinates; the later actual touch path
    # must fit the current Surface/rotation and require the same owned attempt.
    return {'schema': 'owner-source-remote-target-v1', 'available': True,
            'required_state': required_state,
            'action_code': 1 if required_state == 'paused' else 2,
            'reference_width': display_width, 'reference_height': display_height,
            'bounds': [x0, y0, x1, y1],
            'x_u16': ((x0 + x1) * 65535 + display_width) // (2 * display_width),
            'y_u16': ((y0 + y1) * 65535 + display_height) // (2 * display_height),
            'process_identity_or_attempt_verified': False,
            'input_executed': False, 'playback_transition_verified': False}
