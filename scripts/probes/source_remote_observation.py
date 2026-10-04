"""Explicit read-only source target qualification; importing never queries devices.

Intercept the existing collector's same owned UI pipe before its raw buffer is
cleared. No second dump, host XML file, menu/input command or guest lease.
"""
from scripts.probes import source_decoder_observation as decoder
from scripts.probes import source_stats_observation as observation
from scripts.probes import source_remote_control as control
from scripts.probes import read_source_renderer_preflight as renderer


def collect(adb, serial, expected_video_id, *, required_state='paused',
            display_width, display_height, timeout=15,
            reader_factory=decoder.BoundedReader, require_display=False):
    # Validate geometry/content before creating any reader or querying a device.
    control.parse_target(b'', expected_video_id=expected_video_id,
                         required_state=required_state, display_width=display_width,
                         display_height=display_height)
    target = control._unknown('target_not_collected')
    geometry, rotations = [], []

    def dimensions(raw, info):
        try:
            value = renderer.dimensions(raw.decode('ascii')) if info['command_ok'] else {}
            geometry.append(info['child_reaped'] and
                value.get('override', value.get('physical')) ==
                {'width': display_width, 'height': display_height})
        finally:
            raw.clear()

    class Intercept:
        def __init__(self, *args):
            self.reader = reader_factory(*args)
            if require_display:
                dimensions(*self.reader.read(['wm', 'size'], 4096))

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def read(self, args, byte_limit=decoder.MAX_DUMP_BYTES):
            nonlocal target
            raw, info = self.reader.read(args, byte_limit)
            if require_display and args == ['dumpsys', '-t', '3', 'window']:
                try:
                    rotations.append(renderer.rotation(raw.decode('ascii')) == 0
                                     if info['command_ok'] and info['child_reaped'] else False)
                except UnicodeError:
                    rotations.append(False)
            if require_display and args == [decoder.IDENTITY_SCRIPT] and geometry and len(rotations) == 2:
                dimensions(*self.reader.read(['wm', 'size'], 4096))
            if (info['command_ok'] and raw.startswith(observation.UI_CREATED)
                    and raw.endswith(observation.UI_DONE)
                    and raw.count(observation.UI_CREATED) == 1
                    and raw.count(observation.UI_DONE) == 1
                    and len(raw) < observation.stats.MAX_BYTES):
                target = control.parse_target(bytes(raw[len(observation.UI_CREATED):-len(observation.UI_DONE)]),
                    expected_video_id=expected_video_id, required_state=required_state,
                    display_width=display_width, display_height=display_height)
            return raw, info

    qualification = observation.collect(adb, serial, expected_video_id,
        required_state=required_state, timeout=timeout, reader_factory=Intercept)
    qualified = qualification['qualified'] and target['available']
    display_verified = require_display and geometry == [True, True] and rotations == [True, True]
    if require_display:
        qualified = qualified and display_verified
    return {'schema': 'owner-source-remote-observation-v1', 'target_qualified': bool(qualified),
            'source': qualification, 'target': target,
            'source_process_bracket_verified': qualification['qualified'],
            'display_geometry_verified': bool(display_verified),
            'target_from_same_Stats_snapshot': target['available'],
            'authenticated_attempt_verified': False, 'input_executed': False,
            'playback_transition_verified': False, 'host_XML_file_created': False}
