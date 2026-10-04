import unittest
from unittest.mock import Mock, patch

from scripts.probes import source_authenticated_observation as m
from scripts.probes.source_authenticated_driver import Rejected
from tests.test_source_decoder_observation import FakeReader, identity, session
from tests.test_source_stats_observation import focus

EXPECTED = {'pid': 5212, 'uid': 10235, 'start_ticks': 453401}


class SourceAuthenticatedObservationChecks(unittest.TestCase):
    def run_case(self, rows=None, state='playing'):
        s = session(state=3, speed=1) if state == 'playing' else session(state=2, speed=0)
        reader = FakeReader(rows or [identity(), focus(), s, identity(), focus(), s])
        value = m.collect_state('inert', 'emulator-5556', EXPECTED, state,
                                 reader_factory=lambda *_: reader)
        return value, reader

    def test_playing_and_paused_are_read_only_brackets_not_motion_or_input(self):
        for state in ('playing', 'paused'):
            value, reader = self.run_case(state=state)
            self.assertTrue(value['verified']); self.assertEqual(value['identity'], EXPECTED)
            self.assertFalse(value['input_executed_by_observer'])
            self.assertIsNone(value['visual_motion_observed'])
            self.assertEqual(len(reader.arguments), 6)
            self.assertTrue(all(not raw for raw in reader.buffers))
            for args, _ in reader.arguments:
                self.assertFalse('input keyevent' in ' '.join(args))
                self.assertFalse('force-stop' in ' '.join(args))

    def test_changed_source_wrong_session_focus_or_state_reject(self):
        for index, replacement in ((3, identity(start=1)), (4, focus('foreign.app')),
                                    (5, session(pid=1)), (5, session(state=2, speed=0)),
                                    (2, (session(), 4)), (5, session(speed=.5))):
            rows = [identity(), focus(), session(), identity(), focus(), session()]
            rows[index] = replacement
            with self.assertRaises(Rejected): self.run_case(rows)

    def test_foreign_serial_or_malformed_identity_reject_before_reader(self):
        factory = Mock(side_effect=AssertionError('read'))
        for serial, ident in (('emulator-5554', EXPECTED), ('emulator-5556', {'pid': True}),
                              ('emulator-5556', dict(EXPECTED, start_ticks=0))):
            with self.assertRaises(Rejected):
                m.collect_state('inert', serial, ident, 'playing', reader_factory=factory)
        factory.assert_not_called()

    def test_explicit_previous_state_polling_requires_two_consecutive_goals_without_input(self):
        rows = []
        for state in (2, 3, 2, 3, 3):
            rows.extend([identity(), focus(), session(state=state, speed=1)])
        reader = FakeReader(rows)
        with patch.object(m.time, 'sleep') as sleep:
            value = m.collect_state('inert', 'emulator-5556', EXPECTED, 'playing',
                previous_state='paused', reader_factory=lambda *_: reader, timeout=3)
        self.assertTrue(value['verified']); self.assertEqual(value['command_count'], 15)
        self.assertEqual(value['known_previous_state_observations'], 2)
        self.assertTrue(value['explicit_previous_state_polling'])
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(value['input_executed_by_observer'])

    def test_previous_state_polling_is_bounded_and_never_relaxes_foreign_ownership(self):
        for kind in ('all_previous', 'foreign_focus', 'foreign_identity', 'foreign_owner', 'unknown_state'):
            rows = [identity(), focus(), session(state=2)]*8
            if kind == 'foreign_focus': rows[4] = focus('foreign.app')
            elif kind == 'foreign_identity': rows[3] = identity(start=1)
            elif kind == 'foreign_owner': rows[5] = session(pid=1)
            elif kind == 'unknown_state': rows[5] = session(state=6)
            reader = FakeReader(rows)
            with patch.object(m.time, 'sleep'), self.assertRaises(Rejected):
                m.collect_state('inert', 'emulator-5556', EXPECTED, 'playing',
                    previous_state='paused', reader_factory=lambda *_: reader, timeout=3)
            self.assertLessEqual(len(reader.arguments), 24)
        factory=Mock(side_effect=AssertionError('read'))
        with self.assertRaises(Rejected):
            m.collect_state('inert','emulator-5556',EXPECTED,'playing',previous_state='playing',reader_factory=factory)
        factory.assert_not_called()
