import unittest
from stream_settings import parse_settings

class StreamSettingsTest(unittest.TestCase):
    def test_old_client_default(self):
        self.assertEqual(parse_settings({'max_size':1600},960),(1600,2500000))
    def test_custom_bitrate_and_boundaries(self):
        for rate in (500000, 2500000, 5123000, 12000000):
            self.assertEqual(parse_settings({'max_size':1200,'video_bit_rate':rate},960),(1200,rate))
    def test_reject_invalid_types_and_range(self):
        for rate in (True,False,None,'5000000',500000.0,0,-1,499999,12000001):
            with self.subTest(rate=rate),self.assertRaises(ValueError):
                parse_settings({'video_bit_rate':rate},1600)
    def test_reject_invalid_resolution_or_object(self):
        for settings in ([],None,{'max_size':True},{'max_size':720}):
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                parse_settings(settings,1600)
