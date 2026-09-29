import unittest
import performance_profile as profile


class PerformanceProfileTest(unittest.TestCase):
    def setUp(self):
        profile._applied = None
        self.calls = []
        self.boot = 'boot-one'
        self.renderer = 'skiagl'
        self.settings = {}

    def adb(self, *words):
        self.calls.append(words)
        if words[1:] == ('cat', '/proc/sys/kernel/random/boot_id'):
            return self.boot
        if words[1:4] == ('su', '0', 'setprop'):
            self.renderer = words[-1]
        if words[1:] == ('getprop', 'debug.hwui.renderer'):
            return self.renderer
        if words[1:3] == ('settings', 'put'):
            self.settings[words[-2]] = words[-1]
        if words[1:3] == ('settings', 'get'):
            return self.settings.get(words[-1], 'null')
        return ''

    def test_default_is_disabled_and_does_not_touch_a_different_vm(self):
        self.assertEqual(profile.apply_performance_profile(self.adb, {}), {'status': 'disabled'})
        self.assertEqual(self.calls, [])

    def test_root_renderer_and_refresh_are_verified_and_reapplied_after_guest_reboot(self):
        settings = {'DIRECT_HWUI_RENDERER': 'skiavk', 'DIRECT_REFRESH_RATE': '120'}
        self.assertEqual(profile.apply_performance_profile(self.adb, settings)['status'], 'applied')
        count = len(self.calls)
        self.assertEqual(profile.apply_performance_profile(self.adb, settings)['status'], 'already_applied')
        self.assertEqual(len(self.calls), count + 1)
        self.boot = 'boot-two'
        self.renderer = 'skiagl'
        self.assertEqual(profile.apply_performance_profile(self.adb, settings)['status'], 'applied')
        self.assertEqual(self.renderer, 'skiavk')
        self.assertEqual(self.settings['min_refresh_rate'], '120.0')

    def test_unknown_parameters_are_rejected_without_a_root_command(self):
        with self.assertRaises(ValueError):
            profile.apply_performance_profile(self.adb, {'DIRECT_HWUI_RENDERER': 'unvalidated-renderer'})
        self.assertEqual(self.calls, [])

    def test_a_failed_readback_is_not_cached_as_success(self):
        def failed(*words):
            return 'boot' if words[1] == 'cat' else 'skiagl'
        with self.assertRaises(RuntimeError):
            profile.apply_performance_profile(failed, {'DIRECT_HWUI_RENDERER': 'skiavk'})
        self.assertIsNone(profile._applied)


if __name__ == '__main__':
    unittest.main()
