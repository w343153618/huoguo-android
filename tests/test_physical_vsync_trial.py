import unittest
from scripts.probes.trial_physical_vsync import replace_vsync,EXPECTED


class PhysicalVsyncCheck(unittest.TestCase):
    def config(self):
        return ('\n'.join(k+' = '+v for k,v in EXPECTED.items())+'\nhw.lcd.width = 720\nhw.lcd.height = 1280\nhw.lcd.density = 320\nhw.lcd.vsync = 120\nother.setting = keep\n').encode()

    def test_changes_only_vsync_and_preserves_resources(self):
        before=self.config();after,old=replace_vsync(before,60)
        self.assertEqual(old,120)
        self.assertEqual(after,before.replace(b'hw.lcd.vsync = 120',b'hw.lcd.vsync = 60'))

    def test_ambiguous_setting_rejected(self):
        with self.assertRaises(ValueError):replace_vsync(self.config()+b'hw.lcd.vsync = 120\n',60)

    def test_wrong_memory_rejected(self):
        with self.assertRaises(ValueError):replace_vsync(self.config().replace(b'16384',b'8192'),60)

    def test_1080_profile_keeps_geometry_and_density(self):
        before=self.config().replace(b'width = 720',b'width = 1080').replace(b'height = 1280',b'height = 1920').replace(b'density = 320',b'density = 480')
        after,old=replace_vsync(before,60)
        self.assertEqual(old,120)
        self.assertEqual(after,before.replace(b'vsync = 120',b'vsync = 60'))

    def test_mixed_display_profile_is_rejected(self):
        with self.assertRaises(ValueError):replace_vsync(self.config().replace(b'density = 320',b'density = 480'),60)


if __name__=='__main__':unittest.main()
