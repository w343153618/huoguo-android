import unittest
from scripts.probes import source_window_control as m
from tests.test_source_decoder_observation import FakeReader, identity, session
from tests.test_source_stats_observation import focus

IDENT = {'pid': 5212, 'uid': 10235, 'start_ticks': 453401}


class SourceWindowControlTests(unittest.TestCase):
    def run_case(self, entries, target='paused'):
        reader = FakeReader(entries)
        value = m.transition('inert', 'emulator-5556', IDENT, target,
                             reader_factory=lambda *_: reader)
        return value, reader

    def test_pause_requires_post_state_and_exact_bracket(self):
        result, reader = self.run_case([identity(), focus(), session(), b'', identity(), focus(), session(state=2)])
        self.assertEqual(result['state_after'], 2)
        self.assertEqual(reader.arguments[3][0], ['input', 'keyevent', '127'])
        self.assertEqual(result['command_count'], 7)
        self.assertFalse(result['atomic_source_ownership_proven'])
        self.assertTrue(all(not raw for raw in reader.buffers))

    def test_already_paused_has_no_input(self):
        result, reader = self.run_case([identity(), focus(), session(state=2)] * 2)
        self.assertFalse(result['command_sent'])
        self.assertFalse(any(args[0][0] == 'input' for args in reader.arguments))

    def test_resume_has_fixed_key_and_verified_playing(self):
        result, reader = self.run_case([identity(), focus(), session(state=2), b'', identity(), focus(), session()], 'playing')
        self.assertEqual(result['state_after'], 3)
        self.assertEqual(reader.arguments[3][0], ['input', 'keyevent', '126'])

    def test_foreign_identity_focus_owner_or_state_blocks_input(self):
        for entries in ([identity(start=1), focus(), session()],
                        [identity(), focus(package='foreign.app'), session()],
                        [identity(), focus(), session(pid=42)],
                        [identity(), focus(), session(state=4)],
                        [identity(), focus(), session(speed=0)]):
            reader = FakeReader(entries)
            with self.assertRaises(m.Rejected):
                m.transition('inert', 'emulator-5556', IDENT, 'paused', reader_factory=lambda *_: reader)
            self.assertFalse(any(args[0][0] == 'input' for args in reader.arguments))

    def test_successful_keyevent_is_not_state_success(self):
        with self.assertRaisesRegex(m.Rejected, 'state_not_changed'):
            self.run_case([identity(), focus(), session(), b'', identity(), focus(), session()])

    def test_post_identity_focus_owner_changes_fail(self):
        for end in ([identity(start=1), focus(), session(state=2)],
                    [identity(), focus(package='foreign.app'), session(state=2)],
                    [identity(), focus(), session(uid=42, state=2)]):
            with self.assertRaises(m.Rejected):
                self.run_case([identity(), focus(), session(), b''] + end)

    def test_read_error_rejected_and_buffers_cleared(self):
        reader = FakeReader([(identity(), 2)])
        with self.assertRaises(m.Rejected):
            m.transition('inert', 'emulator-5556', IDENT, 'paused', reader_factory=lambda *_: reader)
        self.assertTrue(all(not raw for raw in reader.buffers))

    def test_selector_and_identity_fail_before_any_reader(self):
        for serial, expected, target in (('emulator-5554', IDENT, 'paused'),
                 ('emulator-5556', {**IDENT, 'pid': True}, 'playing'),
                 ('emulator-5556', {**IDENT, 'private': 'x'}, 'paused'),
                 ('emulator-5556', IDENT, 'toggle')):
            with self.assertRaises(m.Rejected):
                m.transition('inert', serial, expected, target,
                             reader_factory=lambda *_: self.fail('must not read'))


if __name__ == '__main__':
    unittest.main()
