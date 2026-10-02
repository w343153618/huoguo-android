import unittest
from scripts.probes.analyze_source_frame_timeline import analyze


class FrameTimelineCheck(unittest.TestCase):
    HEADER='ts_ns,dur_ns,scope_id,jank_mask,none_jank,present_kind_id,on_time_finish,gpu_composition,display_frame_token\n'

    def test_missing_surfaceview_not_claimed_as_zero_video_jank(self):
        r=analyze(self.HEADER+'1,10,1,36,0,2,1,0,4\n')
        self.assertFalse(r['video_surfaceview_covered'])
        self.assertEqual(r['summary']['sf_display']['jank_flags_counts']['sf_scheduling'],1)
        self.assertEqual(r['summary']['sf_display']['jank_flags_counts']['prediction_error'],1)

    def test_non_animating_and_gpu_deadline_are_separate(self):
        r=analyze(self.HEADER+'1,10,1,64,0,0,1,0,4\n')
        self.assertEqual(r['summary']['sf_display']['jank_flags_counts']['non_animating'],1)
        self.assertEqual(r['summary']['sf_display']['jank_flags_counts']['sf_gpu_deadline'],0)


if __name__=='__main__':unittest.main()
