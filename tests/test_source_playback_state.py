import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1]/'scripts/probes/source_playback_state.py'
SPEC = importlib.util.spec_from_file_location('source_playback_state', PATH)
PROBE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(PROBE)


def session(package=PROBE.TARGET_PACKAGE, active=True, state=3, speed='1.0'):
    return (f'  session (userId=0)\n'
            f'    ownerPid=10, ownerUid=1000, userId=0\n'
            f'    package={package}\n'
            f'    active={str(active).lower()}\n'
            f'    state=PlaybackState {{state={state}, position=12345, buffered position=15000, '
            f'speed={speed}, updated=777, actions=123, custom actions=[], active item id=-1, error=null}}\n'
            f'    metadata: SYNTHETIC_PRIVATE_MARKER_DO_NOT_EXPORT\n')


class SourcePlaybackStateTests(unittest.TestCase):
    def test_playing_numeric_whitelist(self):
        out = PROBE.parse_dump(session().encode())
        self.assertFalse(out['unknown']); self.assertEqual(out['state'], 3)
        self.assertEqual(out['position_ms'], 12345); self.assertEqual(out['updated_elapsed_ms'], 777)
        self.assertEqual(out['speed'], 1.0); self.assertFalse(out['media_fps_known'])
        self.assertTrue(all(value is None or type(value) in (bool, int, float) for value in out.values()))
        self.assertNotIn('SYNTHETIC_PRIVATE_MARKER', json.dumps(out))

    def test_paused_active_is_observable(self):
        out = PROBE.parse_dump(session(state=2, speed='0.0').encode())
        self.assertFalse(out['unknown']); self.assertEqual(out['state'], 2); self.assertEqual(out['speed'], 0.0)

    def test_real_numeric_prefix_parses_inner_state(self):
        # These bounded numeric fields reproduce root's in-memory token read;
        # the remainder is synthetic and has no title/account/media URL.
        raw = session().replace('position=12345, buffered position=15000',
                                'position=635000, buffered position=0').replace('updated=777', 'updated=863673')
        out = PROBE.parse_dump(raw.encode())
        self.assertFalse(out['unknown']); self.assertEqual(out['state'], 3)
        self.assertEqual(out['position_ms'], 635000); self.assertEqual(out['updated_elapsed_ms'], 863673)

    def test_primary_aosp_named_state_format(self):
        for name, state in PROBE.STATE_NAMES.items():
            with self.subTest(state=state):
                out = PROBE.parse_dump(session(state=f'{name}({state})').encode())
                self.assertFalse(out['unknown']); self.assertEqual(out['state'], state)
        out = PROBE.parse_dump(session(state='PLAYING(3)').replace(
            'position=12345, buffered position=15000',
            'position=635000, buffered position=0').replace('updated=777', 'updated=863673').encode())
        self.assertEqual(out['position_ms'], 635000); self.assertEqual(out['updated_elapsed_ms'], 863673)

    def test_named_state_mismatch_unknown_and_metadata_cannot_supply_state(self):
        for token in ('PLAYING(2)', 'PAUSED(3)', 'UNKNOWN(3)', 'PLAYING(99)'):
            self.assertTrue(PROBE.parse_dump(session(state=token).encode())['unknown'])
        raw = session().replace('state=PlaybackState {state=3,', 'state=null {state=3,')
        raw += '    metadata: state=PlaybackState {state=PLAYING(3), position=12345, buffered position=0, speed=1.0, updated=777}\n'
        self.assertTrue(PROBE.parse_dump(raw.encode())['unknown'])

    def test_foreign_session_never_selected(self):
        out = PROBE.parse_dump(session(package='example.other', state=2).encode())
        self.assertTrue(out['unknown']); self.assertIsNone(out['state'])

    def test_inactive_and_missing_sessions_are_unknown(self):
        for raw in (b'', session(active=False).encode()):
            with self.subTest(raw_length=len(raw)):
                out = PROBE.parse_dump(raw); self.assertTrue(out['unknown']); self.assertIsNone(out['position_ms'])

    def test_ambiguous_active_sessions_do_not_guess(self):
        out = PROBE.parse_dump((session()+session(state=2)).encode())
        self.assertEqual(out['active_sessions'], 2); self.assertTrue(out['unknown'])

    def test_indentation_and_owner_boundaries(self):
        nested = '  metadata:\n    package='+PROBE.TARGET_PACKAGE+'\n    active=true\n'
        self.assertTrue(PROBE.parse_dump(nested.encode())['unknown'])
        out = PROBE.parse_dump((session()+session(package='other.app', state=2)).encode())
        self.assertEqual(out['state'], 3)

    def test_malformed_state_and_nonfinite_speed_do_not_escape(self):
        for speed in ('NaN', 'Infinity', '1e999', '1001'):
            out = PROBE.parse_dump(session(speed=speed).encode()); self.assertTrue(out['unknown'])
        self.assertTrue(PROBE.parse_dump(session(state=99).encode())['unknown'])

    def test_null_state_and_size_bounds(self):
        raw = session().replace('state=PlaybackState {', 'state=null ignored {').encode()
        self.assertTrue(PROBE.parse_dump(raw)['unknown'])
        self.assertTrue(PROBE.parse_dump(b'x'*(PROBE.MAX_DUMP_BYTES+1))['unknown'])

    def test_missing_adb_does_not_output_error_text(self):
        result = PROBE.collect('/nonexistent/SYNTHETIC_PRIVATE_MARKER', 'emulator-5556', 1)
        self.assertEqual(result['error_code'], 1); self.assertTrue(result['unknown'])
        self.assertNotIn('PRIVATE_MARKER', json.dumps(result))

    def test_local_read_failure_is_numeric_and_cleans_up(self):
        process = mock.Mock()
        process.poll.return_value = 0
        with mock.patch.object(PROBE.subprocess, 'Popen', return_value=process), \
                mock.patch.object(PROBE.selectors, 'DefaultSelector', side_effect=OSError('PRIVATE_MARKER')):
            result = PROBE.collect('/synthetic/no-device-operation', 'emulator-5556', 1)
        self.assertEqual(result['error_code'], 5)
        self.assertFalse(result['command_ok']); self.assertTrue(result['unknown'])
        process.stdout.close.assert_called_once()
        self.assertNotIn('PRIVATE_MARKER', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
