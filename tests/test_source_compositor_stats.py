import unittest
from scripts.probes.collect_source_compositor_stats import parse_timestats, parse_compositor, parse_global_timestats


class SourceCompositorStatsCheck(unittest.TestCase):
    def test_video_layer_only_and_no_raw_names(self):
        raw = '''layerName = secret account window
totalFrames = 999
layerName = SurfaceView[app.morphe.android.youtube/secret account](BLAST)#245
totalFrames = 100
droppedFrames = 7
frameRate = 60.00
account = private-token
present2present histogram is as below:
0ms=3 16ms=80 100ms=2
layerName = app.morphe.android.youtube/UI
totalFrames = 20
'''
        result=parse_timestats(raw)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['fields']['droppedFrames'],7)
        self.assertEqual(result[0]['histograms_ms']['present2present']['16'],80)
        self.assertNotIn('secret',str(result))
        self.assertNotIn('private-token',str(result))

    def test_malformed_histogram_not_partially_accepted(self):
        raw='layerName = SurfaceView[app.morphe.android.youtube/main]\npresent2present histogram is as below:\n16ms=10 arbitrary\n'
        self.assertEqual(parse_timestats(raw)[0]['histograms_ms'],{})

    def test_physical_and_render_refresh_are_distinct(self):
        result=parse_compositor('renderRate=60.00 Hz\nactiveMode={id=0, vsyncRate=120.00 Hz}\nTotal missed frame count: 11\nHWC missed frame count: 9\nGPU missed frame count: 2\n')
        self.assertEqual(result['physical_vsync_hz'],120)
        self.assertEqual(result['render_hz'],60)
        self.assertEqual(result['missed_hwc'],9)

    def test_global_composition_counts_not_layer_counts(self):
        result = parse_global_timestats('totalFrames = 80\nclientCompositionFrames = 45\n'
                                       'layerName = private window\ntotalFrames = 999\n')
        self.assertEqual(result, {'totalFrames': 80, 'clientCompositionFrames': 45})


if __name__=='__main__':
    unittest.main()
