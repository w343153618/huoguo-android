"""Read-only post-native-touch state bracket; never sends a guest key or touch."""
import time

from scripts.probes import source_decoder_observation as source
from scripts.probes import source_stats_observation as stats
from scripts.probes.source_authenticated_driver import Rejected


def collect_state(adb, serial, expected, required_state, *,
                  reader_factory=source.BoundedReader, timeout=6, previous_state=None):
    if (serial != 'emulator-5556' or required_state not in ('playing', 'paused')
            or type(timeout) not in (int, float) or not 3 <= timeout <= 6
            or previous_state not in (None, 'paused' if required_state == 'playing' else 'playing')
            or type(expected) is not dict or set(expected) != {'pid', 'uid', 'start_ticks'}
            or any(type(v) is not int or (0 if k == 'uid' else 1) > v
                   or v > ((1 << 63)-1 if k == 'start_ticks' else (1 << 31)-1)
                   for k, v in expected.items())):
        raise Rejected('source_input_identity_changed')
    reader = reader_factory(adb, serial, timeout)
    wanted, observations = (3 if required_state == 'playing' else 2), []
    consecutive, commands, previous_count = 0, 0, 0

    def read(args, parse, bound=source.MAX_DUMP_BYTES):
        raw, info = reader.read(args, bound)
        try:
            if not info['command_ok'] or not info['child_reaped']:
                raise Rejected('source_input_transition_unverified')
            return parse(raw)
        finally:
            raw.clear()

    # Explicit post-input mode may observe the still-known previous state before
    # two consecutive goal observations. Same budget, eight triplets maximum;
    # no input retry, unknown state, owner/focus or identity relaxation.
    for _ in range(8 if previous_state is not None else 2):
        identity = read([source.IDENTITY_SCRIPT], source.parse_identity, source.IDENTITY_BYTES)
        focused = read(['dumpsys', '-t', '3', 'window'], stats.parse_foreground)
        state, owner = read(['dumpsys', '-t', '3', 'media_session'],
            lambda raw: (source.playback.parse_dump(bytes(raw)), source.active_owner(raw)))
        commands += 3
        if (identity.get('known') is not True or any(identity.get(k) != v for k, v in expected.items())
                or focused is not True or owner != (expected['pid'], expected['uid']) or state['unknown']):
            raise Rejected('source_input_transition_unverified')
        observed = state['state']
        observations.append({'state': observed, 'position_ms': state['position_ms']})
        if observed == wanted and (wanted != 3 or state['speed'] == 1):
            consecutive += 1
            if consecutive == 2: break
        elif previous_state is not None and observed == (2 if previous_state == 'paused' else 3):
            if observed == 3 and state['speed'] != 1:
                raise Rejected('source_input_transition_unverified')
            previous_count += 1; consecutive = 0
            time.sleep(.02)
        else:
            raise Rejected('source_input_transition_unverified')
    if consecutive != 2:
        raise Rejected('source_input_transition_unverified')
    elapsed = time.monotonic_ns() - reader.started_ns
    if not 0 <= elapsed <= int(timeout * 1e9) or reader.unreaped:
        raise Rejected('source_input_observer_budget')
    return {'schema': 'authenticated-source-state-observation-v1', 'verified': True,
            'identity': dict(expected), 'state': required_state, 'observations': observations,
            'all_local_children_reaped': True, 'elapsed_python_monotonic_ns': elapsed,
            'command_count': commands, 'raw_total_bytes': reader.total_bytes,
            'explicit_previous_state_polling': previous_state is not None,
            'known_previous_state_observations': previous_count,
            'input_executed_by_observer': False, 'state_bracket_atomic': False,
            'visual_motion_observed': None, 'format_or_presentation_fps_verified': False}
