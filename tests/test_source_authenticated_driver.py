import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import source_authenticated_driver as m
from scripts.probes import run_authenticated_lan_ui as driver

IDENTITY = {'pid': 3470, 'uid': 10235, 'start_ticks': 1952}
NOW = 10_000_000_000


def ready(phase=1, nonce=77):
    return {'phase': phase, 'nonce': nonce, 'app_generation': 9, 'ui_generation': 5,
            'surface_width': 1440, 'surface_height': 2560, 'image_width': 540, 'image_height': 960}


def target(state='paused'):
    return {'target_qualified': True, 'source_process_bracket_verified': True,
            'target_from_same_Stats_snapshot': True, 'display_geometry_verified': True,
            'source': {'qualified': True, 'all_local_children_reaped': True,
                'remote_uia_completion_receipt': True, 'device_UI_temp_removal_confirmed': True,
                'identity_before': dict(IDENTITY, known=True), 'identity_after': dict(IDENTITY, known=True),
                'host_started_python_monotonic_ns': NOW-1_000_000_000,
                'host_finished_python_monotonic_ns': NOW, 'required_state': state},
            'target': {'action_code': 1 if state == 'paused' else 2, 'required_state': state,
                'reference_width': 1080, 'reference_height': 1920, 'x_u16': 32768, 'y_u16': 14745}}


class Markers:
    def __init__(self): self.rows, self.writes = {}, []
    def read(self, name, bound): return self.rows.get(name)
    def publish(self, name, raw): self.writes.append((name, raw)); self.rows[name] = raw


class SourceAuthenticatedDriverChecks(unittest.TestCase):
    def coordinator(self, observed=None, verified=True):
        bus = Markers()
        c = m.Coordinator(bus, lambda state: target(state) if observed is None else observed,
            lambda state, expected: {'verified': verified, 'identity': expected,
                                    'all_local_children_reaped': True}, IDENTITY, clock=lambda: NOW)
        return c, bus

    def offer(self, bus, phase, **changes):
        value = ready(phase, 77 if phase == 1 else 88); value.update(changes)
        bus.rows[m.names(phase)['ready']] = json.dumps(value).encode('ascii')

    def test_two_phases_require_actual_sampler_barrier_and_independent_transition(self):
        c, bus = self.coordinator(); self.offer(bus, 1)
        self.assertTrue(c.advance()); self.assertEqual(c.completed, [])
        self.assertEqual(bus.writes[0][1], b'1 77 32768 14745 1080 1920\n')
        bus.rows[m.names(1)['dispatched']] = b'77\n'
        self.assertTrue(c.advance()); self.assertEqual(c.completed, [1])
        self.offer(bus, 2)
        with self.assertRaisesRegex(m.Rejected, 'phase_order'): c.advance()
        self.assertEqual(len(bus.writes), 2)
        self.assertTrue(c.advance(samplers_completed=True))
        self.assertEqual(bus.writes[-1][1], b'2 88 32768 14745 1080 1920\n')
        bus.rows[m.names(2)['dispatched']] = b'88\n'
        self.assertTrue(c.advance(samplers_completed=True)); self.assertEqual(c.completed, [1, 2])
        self.assertFalse(c.advance())

    def test_missing_ready_is_quiet_and_never_observes_or_sends(self):
        c, bus = self.coordinator(); self.assertFalse(c.advance()); self.assertFalse(bus.writes)

    def test_pause_only_requires_fresh_playing_target_and_ends_after_verified_pause(self):
        bus = Markers(); calls = []
        def observed(state): calls.append(('target', state)); return target(state)
        def transition(state, identity):
            calls.append(('transition', state))
            return {'verified': True, 'identity': identity, 'all_local_children_reaped': True}
        c = m.Coordinator(bus, observed, transition, IDENTITY, clock=lambda: NOW, mode='pause-only')
        self.offer(bus, 1); self.assertTrue(c.advance())
        self.assertEqual(calls, [('target', 'playing')])
        self.assertEqual(bus.writes[0][1], b'1 77 32768 14745 1080 1920\n')
        bus.rows[m.names(1)['dispatched']] = b'77\n'; self.assertTrue(c.advance())
        self.assertEqual(calls[-1], ('transition', 'paused'))
        self.assertEqual(c.completed, [1]); self.assertEqual(c.phase, 3)
        self.assertFalse(c.advance()); self.assertEqual(len(bus.writes), 2)
        with self.assertRaises(m.Rejected): m.command(ready(), target(), IDENTITY, NOW, mode='pause-only')
        with self.assertRaises(m.Rejected): m.command(ready(2), target('playing'), IDENTITY, NOW, mode='pause-only')

    def test_pause_target_failure_never_sends_or_counts_remote_state(self):
        bus = Markers()
        c = m.Coordinator(bus, lambda _: target(), lambda *_: (_ for _ in ()).throw(AssertionError()),
                          IDENTITY, clock=lambda: NOW, mode='pause-only')
        self.offer(bus, 1)
        with self.assertRaises(m.Rejected): c.advance()
        self.assertFalse(bus.writes); self.assertEqual(c.completed, [])

    def test_pause_only_direct_reader_selection_is_explicit_and_scope_closed(self):
        common = ['--output', '/tmp/inert-output']
        options = common + ['--source-input', 'pause-only', '--network-scope', 'nps_owner', '--node', 'm1',
            '--media-only', '--credential-source', 'saved-ui', '--source-input-identity', '3470:10235:1952',
            '--source-snapshot-deployment', '/tmp/inert.json']
        self.assertEqual(driver.parse_arguments(options).source_input, 'pause-only')
        for extra in (['--source-input', 'native'], ['--source-input', 'off'],
                      ['--node', 'm5'], ['--source-snapshot-deployment', 'relative.json']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                driver.parse_arguments(options + extra)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            driver.parse_arguments(options[:-2])

    def test_nonce_ack_cannot_be_used_as_playback_verification(self):
        c, bus = self.coordinator(verified=False); self.offer(bus, 1); c.advance()
        bus.rows[m.names(1)['dispatched']] = b'77\n'
        with self.assertRaisesRegex(m.Rejected, 'transition_unverified'): c.advance()
        self.assertEqual(len(bus.writes), 1); self.assertEqual(c.completed, [])

    def test_wrong_dispatched_nonce_never_invokes_transition_or_verifies(self):
        c, bus = self.coordinator(); self.offer(bus, 1); c.advance()
        bus.rows[m.names(1)['dispatched']] = b'88\n'
        with self.assertRaisesRegex(m.Rejected, 'nonce_changed'): c.advance()
        self.assertEqual(len(bus.writes), 1)

    def test_ready_changed_attempt_or_geometry_and_replayed_nonce_reject_phase_two(self):
        for changes in ({'ui_generation': 6}, {'app_generation': 10}, {'image_height': 1080}, {'nonce': 77}):
            c, bus = self.coordinator(); self.offer(bus, 1); c.advance()
            bus.rows[m.names(1)['dispatched']] = b'77\n'; c.advance(); self.offer(bus, 2, **changes)
            with self.assertRaises(m.Rejected): c.advance(samplers_completed=True)
            self.assertEqual(len(bus.writes), 2)

    def test_ready_duplicate_extra_boolean_and_unbounded_fields_reject(self):
        for raw in (b'{"phase":1,"phase":1}', b'null', b'[]', b'\xff', b' '*2049):
            with self.assertRaises(m.Rejected): m.parse_ready(raw, 1)
        for field, value in (('nonce', True), ('nonce', 1 << 63), ('surface_width', 0), ('phase', 2)):
            row = ready(); row[field] = value
            with self.assertRaises(m.Rejected): m.parse_ready(json.dumps(row).encode(), 1)
        row = ready(); row['password'] = 'not permitted'
        with self.assertRaises(m.Rejected): m.parse_ready(json.dumps(row).encode(), 1)

    def test_foreign_identity_or_unverified_geometry_never_publishes(self):
        for change in ('identity', 'geometry', 'cleanup', 'target'):
            row = target()
            if change == 'identity': row['source']['identity_after']['start_ticks'] += 1
            elif change == 'geometry': row['display_geometry_verified'] = False
            elif change == 'cleanup': row['source']['device_UI_temp_removal_confirmed'] = False
            else: row['target']['x_u16'] = -1
            c, bus = self.coordinator(row); self.offer(bus, 1)
            with self.assertRaises(m.Rejected): c.advance()
            self.assertFalse(bus.writes)

    def test_stale_or_foreign_aspect_and_contrary_state_reject_before_publication(self):
        for mutation in ('stale', 'future', 'slow', 'aspect', 'state'):
            row = target()
            if mutation == 'stale': row['source']['host_finished_python_monotonic_ns'] = NOW-2_000_000_000
            elif mutation == 'future': row['source']['host_finished_python_monotonic_ns'] = NOW+1
            elif mutation == 'slow': row['source']['host_started_python_monotonic_ns'] = 1
            elif mutation == 'aspect': row['target']['reference_width'] = 1920
            else: row['target']['action_code'] = 2
            with self.assertRaises(m.Rejected): m.command(ready(), row, IDENTITY, NOW)

    def test_explicit_driver_scope_and_default_off_do_not_enable_host_control(self):
        common = ['--output', '/tmp/inert-output']
        self.assertEqual(driver.parse_arguments(common).source_input, 'off')
        opts = common+['--source-input', 'native', '--network-scope', 'nps_owner', '--node', 'm1',
                       '--media-only', '--credential-source', 'saved-ui', '--source-input-identity', '3470:10235:1952']
        parsed = driver.parse_arguments(opts)
        self.assertEqual(parsed.source_input_identity, IDENTITY)
        for extra in (['--node', 'm5'], ['--credential-source', 'private-file'], ['--phone-only-sampler'],
                      ['--source-input-identity', '1:2:0'], ['--source-input-identity', '1:2:3; rm -rf /']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): driver.parse_arguments(opts+extra)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): driver.parse_arguments(common+['--source-input-identity', '1:2:3'])

    def test_helper_return_and_nonce_confirmations_do_not_replace_actual_local_contact_receipts(self):
        value = {'requested_authenticated_source_input': True, 'helper_owned_attempt_started': True}
        for phase in (1, 2):
            value['source_phase_%d_external_observer_confirmation' % phase] = True
            value['source_phase_%d_local_tap' % phase] = {
                'down_attempted': True, 'down_returned': True, 'up_attempted': True, 'up_returned': True,
                'cancel_attempted': False, 'cancel_returned': False,
                'cancel_skipped_for_changed_owner': False, 'remote_playback_verified_by_helper': False}
        self.assertTrue(m.helper_readback(value))
        for key in list(value['source_phase_2_local_tap']):
            old = value['source_phase_2_local_tap'].pop(key)
            self.assertFalse(m.helper_readback(value)); value['source_phase_2_local_tap'][key] = old
        value['source_owned_marker_cleanup_failed'] = True
        self.assertFalse(m.helper_readback(value))

    def test_main_target_failure_reaps_owned_instrumentation_without_stopping_newer_app(self):
        calls = []
        class Bus:
            def require_absent(self): pass
            def cleanup(self):
                return {'owned_marker_cleanup_confirmed': True, 'cleanup_failures': 0,
                        'filesystem_checks_atomic': False}
        class Phase:
            phase = 1
            observations = {}
            def advance(self, **kwargs):
                raise m.Rejected('source_input_target_unqualified')
        class Child:
            returncode = None
            waits, signals = [], []
            def poll(self): return self.returncode
            def communicate(self, timeout):
                self.waits.append(timeout); self.returncode = 0
                return '', ''
            def terminate(self): self.signals.append('terminate')
            def kill(self): self.signals.append('kill')
        child = Child()
        def run(args, **kwargs):
            calls.append(args)
            body = args[-1]
            if args[0] == 'lsof': return subprocess.CompletedProcess(args, 1, b'', b'')
            if body == 'pidof '+driver.TARGET_PACKAGE:
                return subprocess.CompletedProcess(args, 1, '', '')
            if body.startswith('cmd package list'):
                return subprocess.CompletedProcess(args, 0, 'package:'+driver.TARGET_PACKAGE+' uid:10234\n', '')
            if body.startswith('pm path --user 0 '):
                package = body.split()[-1]
                return subprocess.CompletedProcess(args, 0, 'package:/data/app/'+package+'/base.apk\n', '')
            if 'sha256sum ' in body:
                package = 'local.huoguo.lanuitest' if 'lanuitest' in body else driver.TARGET_PACKAGE
                digest = m.HELPER_SHA256 if 'lanuitest' in package else 'd0437e51e8c2d0d27c89458b3a5e6467ee421f25337551d19ecc0992d18ecf08'
                return subprocess.CompletedProcess(args, 0, digest+'  /data/app/'+package+'/base.apk\n', '')
            return subprocess.CompletedProcess(args, 0, '', '')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            argv = ['probe', '--output', directory, '--source-input', 'native', '--network-scope',
                    'nps_owner', '--node', 'm1', '--media-only', '--credential-source', 'saved-ui',
                    '--source-input-identity', '3470:10235:1952', '--steady-seconds', '30']
            stack.enter_context(patch.object(driver.sys, 'argv', argv))
            stack.enter_context(patch.object(driver.subprocess, 'run', side_effect=run))
            spawned = stack.enter_context(patch.object(driver.subprocess, 'Popen', return_value=child))
            stack.enter_context(patch.object(driver.source_phone_markers, 'PhoneMarkers', return_value=Bus()))
            stack.enter_context(patch.object(driver.source_authenticated_driver, 'Coordinator', return_value=Phase()))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(driver.main(), 1)
            report = json.loads((Path(directory)/'ui-acceptance.json').read_text())
        self.assertEqual(spawned.call_count, 1)
        self.assertEqual(report['driver_failure_label'], 'source_input_target_unqualified')
        self.assertEqual(child.waits, [55]); self.assertFalse(child.signals)
        self.assertFalse(any('force-stop' in args[-1] for args in calls))
        self.assertFalse(report['phone_sampler_started'])
        self.assertTrue(report['authenticated_source_marker_cleanup']['owned_marker_cleanup_confirmed'])


if __name__ == '__main__': unittest.main()
