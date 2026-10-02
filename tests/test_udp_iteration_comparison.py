import unittest

from scripts.probes.compare_udp_iterations import surface_window


class SurfaceWindowTest(unittest.TestCase):
    def surface(self, seconds):
        return {'presentation_ns': [1_000_000_000 + round(s * 1e9) for s in seconds]}

    def test_freeze_crossing_start_is_not_hidden_by_interior_only_gaps(self):
        timestamps = [0, 4.396] + [6.504 + i * .02 for i in range(1230)]
        result = surface_window(self.surface(timestamps), 1_000_000_000)
        self.assertTrue(result['timestamps_cover_entire_window'])
        self.assertAlmostEqual(result['boundary_clipped_gap_ms']['max'], 1504)
        # The first crossing's silence inside the measured window is 1.504 s.
        self.assertEqual(result['boundary_clipped_gaps_over_100ms'], 1)

    def test_entire_window_frozen_is_measured_as_zero_presents(self):
        result = surface_window(self.surface([0, 4, 31]), 1_000_000_000)
        self.assertTrue(result['available'])
        self.assertTrue(result['timestamps_cover_entire_window'])
        self.assertEqual(result['full_window_fps'], 0)
        self.assertEqual(result['boundary_clipped_gap_ms']['max'], 25000)

    def test_missing_sampling_is_not_a_zero_fps_result(self):
        self.assertFalse(surface_window(self.surface([0, 1]), 1_000_000_000)['available'])
        self.assertFalse(surface_window({}, 1_000_000_000)['available'])

    def test_duplicate_or_reversed_samples_are_rejected(self):
        for seconds in ([0, 6, 6, 31], [0, 7, 6, 31]):
            with self.assertRaises(ValueError):
                surface_window(self.surface(seconds), 1_000_000_000)


if __name__ == '__main__':
    unittest.main()
