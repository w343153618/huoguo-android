import json
import unittest
from unittest.mock import Mock

from scripts.probes import source_remote_observation as target
from tests import test_source_stats_observation as existing
from tests.test_source_decoder_observation import FakeReader, identity, PRIVATE


def values():
    rows = existing.values()
    root = existing.snapshot()
    root[1].set('class', 'android.widget.ImageButton')
    root[1].set('enabled', 'true')
    root[1].set('clickable', 'true')
    root[1].set('bounds', '[486,370][594,478]')
    rows[3] = existing.pipe(root)
    return rows


class SourceRemoteObservationChecks(unittest.TestCase):
    def run_case(self, rows=None):
        reader = FakeReader(values() if rows is None else rows)
        result = target.collect('inert-not-adb', 'emulator-5556', 'aqz-KE-bpKQ',
            display_width=1080, display_height=1920, reader_factory=lambda *_: reader)
        return result, reader

    def test_same_dump_and_complete_identity_bracket_do_not_claim_attempt_or_input(self):
        result, reader = self.run_case()
        self.assertTrue(result['target_qualified'])
        self.assertTrue(result['source_process_bracket_verified'])
        self.assertTrue(result['target_from_same_Stats_snapshot'])
        self.assertEqual(len(reader.arguments), 7)
        self.assertEqual(sum('uiautomator dump' in ' '.join(args) for args, _ in reader.arguments), 1)
        for name in ('authenticated_attempt_verified', 'input_executed',
                     'playback_transition_verified', 'host_XML_file_created'):
            self.assertFalse(result[name])
        self.assertTrue(all(not raw for raw in reader.buffers))
        for private in (PRIVATE, 'inert-private-text', 'fixtureTrack', 'visionOS', '<?xml'):
            self.assertNotIn(private, json.dumps(result))

    def test_parseable_target_cannot_override_restarted_source_or_wrong_owner(self):
        for index, raw in ((6, identity(start=453402)), (6, identity(uid=10001)),
                           (2, existing.session(pid=123, state=2)), (5, existing.focus('foreign.app'))):
            rows = values(); rows[index] = raw
            result, _ = self.run_case(rows)
            self.assertTrue(result['target']['available'])
            self.assertFalse(result['target_qualified'])

    def test_missing_closed_UI_receipt_cannot_qualify_target(self):
        rows = values(); rows[3] = rows[3].replace(existing.m.UI_DONE, b'')
        rows.insert(4, b'')
        result, reader = self.run_case(rows)
        self.assertFalse(result['target_qualified'])
        self.assertFalse(result['target']['available'])
        self.assertTrue(all(not raw for raw in reader.buffers))

    def test_command_error_cannot_parse_fabricated_success_pipe(self):
        rows = values(); rows[3] = (rows[3], 4); rows.insert(4, b'')
        result, _ = self.run_case(rows)
        self.assertFalse(result['target_qualified'])
        self.assertFalse(result['target']['available'])

    def test_stats_qualification_without_visible_clickable_control_stays_unavailable(self):
        rows = existing.values()
        result, _ = self.run_case(rows)
        self.assertTrue(result['source_process_bracket_verified'])
        self.assertFalse(result['target_qualified'])

    def test_invalid_geometry_or_video_reject_before_query_factory(self):
        factory = Mock(side_effect=AssertionError('reader'))
        for width, video in ((True, 'aqz-KE-bpKQ'), (9000, 'aqz-KE-bpKQ'), (1080, None)):
            with self.assertRaises(ValueError):
                target.collect('inert', 'emulator-5556', video, display_width=width,
                    display_height=1920, reader_factory=factory)
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
