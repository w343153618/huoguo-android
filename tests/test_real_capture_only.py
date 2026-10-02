import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

PATH = Path(__file__).resolve().parents[1]/'scripts/probes/measure_real_capture_only.py'
SPEC = importlib.util.spec_from_file_location('real_capture_only', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def image(width=4, height=6, fmt=1, seq=3, pts=1000, pixels=None):
    return SimpleNamespace(format=SimpleNamespace(width=width, height=height, format=fmt),
                           width=width, height=height, seq=seq, timestampUs=pts,
                           image=b'x'*(width*height*4) if pixels is None else pixels)


class RealCaptureOnlyChecks(unittest.TestCase):
    def test_no_pixels_exported_only_numeric_frame_metadata(self):
        row = PROBE.frame_row(image(pixels=b'PRIVATE_XYZ!'*8), 1200000, 1500, 1)
        self.assertTrue(all(type(value) is int for value in row.values()))
        self.assertNotIn('PRIVATE', json.dumps(row))
        self.assertEqual(row['source_pts_us'], 1000)
        self.assertEqual(row['pixel_bytes'], 96)

    def test_invalid_image_or_timestamp_is_rejected(self):
        for value in (image(width=3), image(height=5), image(fmt=2), image(pixels=b''),
                      image(seq=-1), image(seq=2**32), image(pts=-1)):
            with self.subTest(value=value.format), self.assertRaises(ValueError):
                PROBE.frame_row(value, 100, 100, 1)

    def test_capture_gaps_source_gaps_and_wall_age_are_distinct(self):
        rows = [PROBE.frame_row(image(seq=seq, pts=pts), host, wall, 1)
                for seq, pts, host, wall in ((1, 1000, 10_000_000, 1500),
                                           (2, 21000, 130_000_000, 21500),
                                           (3, 140000, 150_000_000, 139000))]
        result = PROBE.summarize(rows, 0, 200_000_000)
        self.assertEqual(result['grpc_return_gaps_ms']['max'], 120)
        self.assertEqual(result['source_screenshot_pts_gaps_ms']['max'], 119)
        self.assertEqual(result['negative_screenshot_age_count'], 1)
        self.assertEqual(result['capture_fps'], 15)

    def test_sequence_wrap_duplicate_gap_and_reset_are_not_network_loss(self):
        rows = [PROBE.frame_row(image(seq=seq), index*1000, 1000, 1)
                for index, seq in enumerate((0xfffffffe, 0xffffffff, 0, 0, 3, 1))]
        result = PROBE.summarize(rows, 0, 10000)
        self.assertEqual(result['duplicate_screenshot_sequence_count'], 1)
        self.assertEqual(result['missing_screenshot_sequence_numbers'], 2)
        self.assertEqual(result['screenshot_sequence_reset_count'], 1)

    def test_playing_gate_unknown_paused_foreign_and_nonfinite_fail(self):
        good = dict(command_ok=True, state_known=True, state=3, speed=1.0)
        self.assertTrue(PROBE.playing(good))
        for replacement in (dict(command_ok=False), dict(state_known=False), dict(state=2),
                            dict(speed=0), dict(speed=float('inf')), dict(speed=float('nan'))):
            self.assertFalse(PROBE.playing(dict(good, **replacement)))

    def test_focus_is_current_selected_player_only(self):
        self.assertTrue(PROBE.focus_matches(b'mCurrentFocus=Window{1 u0 app.morphe.android.youtube/.WatchActivity}\n'))
        for raw in (b'activityHistory=app.morphe.android.youtube/.WatchActivity',
                    b'mCurrentFocus=Window{1 u0 other.app/.Activity}\nPRIVATE_MARKER',
                    b'mCurrentFocus=Window{1 u0 not.app.morphe.android.youtube/.WatchActivity}',
                    b'x'*(1024*1024+1)):
            self.assertFalse(PROBE.focus_matches(raw))


if __name__ == '__main__':
    unittest.main()
