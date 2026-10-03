"""Explicit owner experiment control, not a lease or a process ownership proof.

Caller must hold the live M1 admission reservation throughout. Read the exact
source identity, foreground and active MediaSession immediately before a fixed
MEDIA_PLAY/PAUSE and again afterward. ADB success alone never verifies state.
The device can change between queries; this is a bounded bracket, not atomic.
No CLI, import-time device access, menus, preferences, data or service signals.
"""
import time

from scripts.probes import source_decoder_observation as observation
from scripts.probes import source_stats_observation as stats
LABELS = frozenset(('source_control_selector', 'source_control_identity',
    'source_control_read_unavailable', 'source_control_bracket_rejected',
    'source_control_state_not_changed', 'source_control_budget_or_reap'))


class Rejected(ValueError):
    pass


def transition(adb, serial, expected, target, *, reader_factory=observation.BoundedReader):
    if serial != 'emulator-5556' or target not in ('playing', 'paused'):
        raise Rejected('source_control_selector')
    if (type(expected) is not dict or set(expected) != {'pid', 'uid', 'start_ticks'}
            or any(type(expected[k]) is not int or not (0 if k == 'uid' else 1) <= expected[k]
                   <= (9223372036854775807 if k == 'start_ticks' else 2147483647)
                   for k in expected)):
        raise Rejected('source_control_identity')
    reader = reader_factory(adb, serial, 6)
    commands = []

    def read(args, parser, bound=observation.MAX_DUMP_BYTES):
        raw, info = reader.read(args, bound)
        commands.append(info)
        try:
            if info.get('command_ok') is not True or info.get('child_reaped') is not True:
                raise Rejected('source_control_read_unavailable')
            return parser(raw)
        finally:
            raw.clear()

    def bracket():
        identity = read([observation.IDENTITY_SCRIPT], observation.parse_identity,
                        observation.IDENTITY_BYTES)
        focused = read(['dumpsys', '-t', '3', 'window'], stats.parse_foreground)
        def session(raw):
            value = observation.playback.parse_dump(bytes(raw))
            return value, observation.active_owner(raw)
        value, owner = read(['dumpsys', '-t', '3', 'media_session'], session)
        if (identity.get('known') is not True or any(identity.get(k) != v for k, v in expected.items())
                or focused is not True or owner != (expected['pid'], expected['uid'])
                or value.get('unknown') is not False or value.get('state') not in (2, 3)
                or (value['state'] == 3 and value.get('speed') != 1)):
            raise Rejected('source_control_bracket_rejected')
        return value

    before = bracket()
    wanted = 3 if target == 'playing' else 2
    sent = before['state'] != wanted
    if sent:
        read(['input', 'keyevent', '126' if wanted == 3 else '127'], lambda raw: None, 1024)
    after = bracket()
    if after['state'] != wanted:
        raise Rejected('source_control_state_not_changed')
    elapsed = time.monotonic_ns() - reader.started_ns
    if elapsed < 0 or elapsed > 6_000_000_000 or reader.unreaped:
        raise Rejected('source_control_budget_or_reap')
    return {'schema': 'owner-source-window-control-v1', 'identity': dict(expected),
            'target': target, 'state_before': before['state'], 'state_after': after['state'],
            'command_sent': sent, 'all_local_children_reaped': True,
            'host_clock_domain': 'python_time_monotonic_ns', 'elapsed_ns': elapsed,
            'command_count': len(commands), 'raw_total_bytes': reader.total_bytes,
            'atomic_source_ownership_proven': False, 'admission_owned_by_caller': True}
