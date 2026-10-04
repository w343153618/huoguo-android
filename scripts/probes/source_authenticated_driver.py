"""Default-inert owner source phases through an already authenticated helper.

Callbacks own device I/O; this module never launches a device command, reads a
credential or controls a guest. JSON alone cannot establish App Attempt ownership.
The matching helper rechecks that ownership inside every native dispatch.
"""
import json
import time

HELPER_SHA256 = '9b1cc1434e45e58ad45f0787f8931783de84e48b78a2c5dbbd236eff92d19d05'
LABELS = frozenset(('source_input_ready_schema', 'source_input_target_unqualified',
    'source_input_identity_changed', 'source_input_geometry_changed',
    'source_input_target_stale', 'source_input_attempt_changed',
    'source_input_nonce_changed', 'source_input_transition_unverified',
    'source_input_phase_order', 'source_input_observer_budget',
    'source_input_helper_unmatched', 'source_input_preexisting_marker',
    'source_input_marker_metadata', 'source_input_marker_selector',
    'source_input_marker_command', 'source_input_marker_bound', 'source_input_marker_changed'))


class Rejected(ValueError):
    pass


def _require(value, label):
    if not value:
        raise Rejected(label)


def names(phase):
    _require(type(phase) is int and phase in (1, 2), 'source_input_phase_order')
    return {kind: 'udp-ui-source-%d-%s' % (phase, kind)
            for kind in ('ready', 'command', 'dispatched', 'verified')}


def parse_ready(raw, phase):
    _require(type(raw) is bytes and 0 < len(raw) <= 2048, 'source_input_ready_schema')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'source_input_ready_schema')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(Rejected('source_input_ready_schema')))
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected('source_input_ready_schema') from None
    keys = {'phase', 'nonce', 'app_generation', 'ui_generation',
            'surface_width', 'surface_height', 'image_width', 'image_height'}
    _require(type(value) is dict and set(value) == keys
             and all(type(v) is int for v in value.values()), 'source_input_ready_schema')
    _require(value['phase'] == phase and type(phase) is int and phase in (1, 2)
             and 1 <= value['nonce'] <= (1 << 63) - 1
             and 0 <= value['app_generation'] <= (1 << 31) - 1
             and 0 <= value['ui_generation'] <= (1 << 63) - 1
             and all(16 <= value[k] <= 8192 for k in keys if k.endswith(('_width', '_height'))),
             'source_input_ready_schema')
    return value


def nonce_body(ready):
    return (str(ready['nonce']) + '\n').encode('ascii')


def helper_readback(value, *, mode='native'):
    if mode not in ('native', 'pause-only'):
        return False
    if (type(value) is not dict or 'failure_class' in value
            or value.get('requested_authenticated_source_input') is not True
            or value.get('helper_owned_attempt_started') is not True
            or value.get('source_owned_marker_cleanup_failed', False) is not False):
        return False
    if mode == 'pause-only' and (value.get('source_pause_only_recovery') is not True
            or value.get('source_recovery_no_steady_window') is not True
            or value.get('source_recovery_no_reconnect') is not True
            or any(key in value for key in ('source_phase_2_local_tap',
                'source_phase_2_external_observer_confirmation'))):
        return False
    for phase in ((1, 2) if mode == 'native' else (1,)):
        if value.get('source_phase_%d_external_observer_confirmation' % phase) is not True:
            return False
        receipt = value.get('source_phase_%d_local_tap' % phase)
        if type(receipt) is not dict:
            return False
        if any(receipt.get(k) is not True for k in
               ('down_attempted', 'down_returned', 'up_attempted', 'up_returned')):
            return False
        if any(receipt.get(k) is not False for k in ('cancel_attempted', 'cancel_returned',
               'cancel_skipped_for_changed_owner', 'remote_playback_verified_by_helper')):
            return False
    return True


def command(ready, observed, expected_identity, now_ns, *, mode='native'):
    _require(mode in ('native', 'pause-only') and
             (mode != 'pause-only' or ready['phase'] == 1), 'source_input_phase_order')
    _require(type(observed) is dict and observed.get('target_qualified') is True
             and observed.get('source_process_bracket_verified') is True
             and observed.get('target_from_same_Stats_snapshot') is True
             and observed.get('display_geometry_verified') is True,
             'source_input_target_unqualified')
    source, target = observed['source'], observed['target']
    _require(source.get('qualified') is True and source.get('all_local_children_reaped') is True
             and source.get('remote_uia_completion_receipt') is True
             and source.get('device_UI_temp_removal_confirmed') is True,
             'source_input_target_unqualified')
    for field in ('identity_before', 'identity_after'):
        identity = source.get(field, {})
        _require(identity.get('known') is True
                 and all(identity.get(k) == v for k, v in expected_identity.items()),
                 'source_input_identity_changed')
    started, finished = (source.get('host_started_python_monotonic_ns'),
                         source.get('host_finished_python_monotonic_ns'))
    _require(type(started) is int and type(finished) is int and type(now_ns) is int
             and 0 <= finished - started <= 6_000_000_000
             and 0 <= now_ns - finished <= 1_000_000_000, 'source_input_target_stale')
    state = 'playing' if mode == 'pause-only' else ('paused' if ready['phase'] == 1 else 'playing')
    action_code = 2 if state == 'playing' else 1
    _require(target.get('action_code') == action_code and target.get('required_state') == state
             and source.get('required_state') == state, 'source_input_target_unqualified')
    width, height = target.get('reference_width'), target.get('reference_height')
    _require(type(width) is int and type(height) is int and 16 <= width <= 8192
             and 16 <= height <= 8192
             and width * ready['image_height'] == height * ready['image_width'],
             'source_input_geometry_changed')
    for axis in ('x_u16', 'y_u16'):
        _require(type(target.get(axis)) is int and 0 <= target[axis] <= 65535,
                 'source_input_target_unqualified')
    return ('%d %d %d %d %d %d\n' % (ready['phase'], ready['nonce'],
            target['x_u16'], target['y_u16'], width, height)).encode('ascii')


class Coordinator:
    """Two ordered phases; callbacks must use bounded matching-owner operations."""
    def __init__(self, markers, observe_target, observe_transition, expected_identity,
                 *, clock=time.monotonic_ns, mode='native'):
        _require(mode in ('native', 'pause-only'), 'source_input_phase_order')
        _require(type(expected_identity) is dict
                 and set(expected_identity) == {'pid', 'uid', 'start_ticks'}
                 and all(type(v) is int and (0 if k == 'uid' else 1) <= v
                         <= ((1 << 63) - 1 if k == 'start_ticks' else (1 << 31) - 1)
                         for k, v in expected_identity.items()), 'source_input_identity_changed')
        self.markers, self.observe_target, self.observe_transition = markers, observe_target, observe_transition
        self.expected_identity, self.clock = dict(expected_identity), clock
        self.phase, self.ready, self.first_ready = 1, None, None
        self.completed, self.observations = [], {}
        self.mode = mode

    def advance(self, *, samplers_completed=False):
        if self.phase == 3:
            return False
        _require(self.phase == 1 or samplers_completed, 'source_input_phase_order')
        files = names(self.phase)
        if self.ready is None:
            raw = self.markers.read(files['ready'], 2048)
            if raw is None:
                return False
            self.ready = parse_ready(raw, self.phase)
            if self.first_ready is not None:
                _require(all(self.ready[k] == self.first_ready[k] for k in self.ready
                             if k not in ('phase', 'nonce')), 'source_input_attempt_changed')
                _require(self.ready['nonce'] != self.first_ready['nonce'], 'source_input_nonce_changed')
            before = self.clock()
            observed = self.observe_target('playing' if self.mode == 'pause-only'
                                           else ('paused' if self.phase == 1 else 'playing'))
            _require(0 <= self.clock() - before <= 6_000_000_000, 'source_input_observer_budget')
            raw_command = command(self.ready, observed, self.expected_identity, self.clock(), mode=self.mode)
            # No retry: ambiguous publication is a failed owned attempt.
            self.observations[str(self.phase)] = {'target': observed, 'command_publication_attempted': True}
            self.markers.publish(files['command'], raw_command)
            self.observations[str(self.phase)]['command_published'] = True
            return True
        dispatched = self.markers.read(files['dispatched'], 32)
        if dispatched is None:
            return False
        _require(dispatched == nonce_body(self.ready), 'source_input_nonce_changed')
        before = self.clock()
        transition = self.observe_transition('paused' if self.mode == 'pause-only'
                                              else ('playing' if self.phase == 1 else 'paused'),
                                              self.expected_identity)
        _require(0 <= self.clock() - before <= 6_000_000_000, 'source_input_observer_budget')
        _require(type(transition) is dict and transition.get('verified') is True
                 and transition.get('identity') == self.expected_identity
                 and transition.get('all_local_children_reaped') is True,
                 'source_input_transition_unverified')
        self.observations[str(self.phase)]['transition'] = transition
        self.markers.publish(files['verified'], nonce_body(self.ready))
        self.completed.append(self.phase)
        if self.phase == 1:
            self.first_ready = self.ready
        self.phase = 3 if self.mode == 'pause-only' else self.phase + 1
        self.ready = None
        return True
