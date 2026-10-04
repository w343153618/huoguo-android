"""Read-only post-native-touch state bracket; never sends a guest key or touch."""
import time

from scripts.probes import source_decoder_observation as source
from scripts.probes import source_stats_observation as stats
from scripts.probes.source_authenticated_driver import Rejected


def collect_state(adb, serial, expected, required_state, *,
                  reader_factory=source.BoundedReader, timeout=6):
    if (serial != 'emulator-5556' or required_state not in ('playing', 'paused')
            or type(timeout) not in (int, float) or not 3 <= timeout <= 6
            or type(expected) is not dict or set(expected) != {'pid', 'uid', 'start_ticks'}
            or any(type(v) is not int or (0 if k == 'uid' else 1) > v
                   or v > ((1 << 63)-1 if k == 'start_ticks' else (1 << 31)-1)
                   for k, v in expected.items())):
        raise Rejected('source_input_identity_changed')
    reader = reader_factory(adb, serial, timeout)
    wanted, observations = (3 if required_state == 'playing' else 2), []

    def read(args, parse, bound=source.MAX_DUMP_BYTES):
        raw, info = reader.read(args, bound)
        try:
            if not info['command_ok'] or not info['child_reaped']:
                raise Rejected('source_input_transition_unverified')
            return parse(raw)
        finally:
            raw.clear()

    for _ in range(2):
        identity = read([source.IDENTITY_SCRIPT], source.parse_identity, source.IDENTITY_BYTES)
        focused = read(['dumpsys', '-t', '3', 'window'], stats.parse_foreground)
        state, owner = read(['dumpsys', '-t', '3', 'media_session'],
            lambda raw: (source.playback.parse_dump(bytes(raw)), source.active_owner(raw)))
        if (identity.get('known') is not True or any(identity.get(k) != v for k, v in expected.items())
                or focused is not True or owner != (expected['pid'], expected['uid'])
                or state['unknown'] or state['state'] != wanted
                or wanted == 3 and state['speed'] != 1):
            raise Rejected('source_input_transition_unverified')
        observations.append({'state': state['state'], 'position_ms': state['position_ms']})
    elapsed = time.monotonic_ns() - reader.started_ns
    if not 0 <= elapsed <= int(timeout * 1e9) or reader.unreaped:
        raise Rejected('source_input_observer_budget')
    return {'schema': 'authenticated-source-state-observation-v1', 'verified': True,
            'identity': dict(expected), 'state': required_state, 'observations': observations,
            'all_local_children_reaped': True, 'elapsed_python_monotonic_ns': elapsed,
            'command_count': 6, 'raw_total_bytes': reader.total_bytes,
            'input_executed_by_observer': False, 'state_bracket_atomic': False,
            'visual_motion_observed': None, 'format_or_presentation_fps_verified': False}
