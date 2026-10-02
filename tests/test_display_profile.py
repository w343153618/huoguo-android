import subprocess
import unittest
from unittest.mock import patch

import display_profile as profile


class FakeAdb:
    def __init__(self):
        self.calls = []
        self.boot_complete = "1\n"
        self.boot_id = "11111111-2222-3333-4444-555555555555\n"
        self.size = "Physical size: 540x1200\n"
        self.density = "Physical density: 210\n"
        self.cutout = "\n"
        self.cutout_rect = "@17040000 -> \n"
        self.fail_disable = False
        self.fail_height = False

    def __call__(self, *args):
        self.calls.append(args)
        if args == ("shell", "getprop", "sys.boot_completed"):
            output = self.boot_complete
        elif args == ("shell", "cat", "/proc/sys/kernel/random/boot_id"):
            output = self.boot_id
        elif args == ("shell", "wm", "size"):
            output = self.size
        elif args == ("shell", "wm", "density"):
            output = self.density
        elif args[:7] == ("shell", "su", "0", "cmd", "overlay", "disable", "--user"):
            if self.fail_disable:
                raise subprocess.CalledProcessError(1, args, stderr="Permission denied")
            output = ""
        elif args[:5] == ("shell", "cmd", "overlay", "lookup", "--user"):
            resource = args[-1]
            if resource == profile._CUTOUT_RESOURCES[0]:
                output = self.cutout
            elif resource == profile._CUTOUT_RESOURCES[1]:
                output = self.cutout_rect
            else:
                if self.fail_height:
                    return subprocess.CompletedProcess(args, 1, "", "Resource unavailable")
                output = "24.0dip\n"
        else:
            raise AssertionError("Unexpected adb call: %r" % (args,))
        return subprocess.CompletedProcess(args, 0, output, "")

    def mutations(self):
        return [args for args in self.calls if "disable" in args]


class DisplayProfileTest(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdb()
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "540x1200"}
        self.memo = patch.multiple(profile, _attempted_boot_id=None,
                                   _attempted_profile=None, _last_result=None)
        self.memo.start()
        self.addCleanup(self.memo.stop)

    def apply(self):
        return profile.apply_540_profile(self.adb, environ=self.env)

    def test_only_explicit_physical_540_profile_runs(self):
        for env in ({}, {"DIRECT_PHYSICAL_DISPLAY": "1080x2400"},
                    {"DIRECT_PHYSICAL_DISPLAY": "540p"}):
            self.assertEqual(profile.apply_540_profile(self.adb, environ=env)["status"], "skipped")
        self.assertEqual(self.adb.calls, [])

    def test_boot_must_finish_before_mutation(self):
        self.adb.boot_complete = "0\n"
        self.assertEqual(self.apply()["status"], "deferred")
        self.assertEqual(self.adb.mutations(), [])
        self.adb.boot_complete = "1\n"
        self.assertEqual(self.apply()["status"], "applied")

    def test_logical_override_does_not_impersonate_physical_profile(self):
        self.adb.size = "Physical size: 1080x2400\nOverride size: 540x1200\n"
        with self.assertRaises(profile.PhysicalDisplayProfileError):
            self.apply()
        self.assertEqual(self.adb.mutations(), [])

    def test_density_and_conflicting_overrides_fail_without_mutation(self):
        for size, density in (
            ("Physical size: 540x1200\n", "Physical density: 420\nOverride density: 210\n"),
            ("Physical size: 540x1200\nOverride size: 720x1600\n", "Physical density: 210\n"),
            ("Physical size: 540x1200\n", "Physical density: 210\nOverride density: 420\n"),
            ("Override size: 540x1200\n", "Physical density: 210\n"),
        ):
            self.adb.size, self.adb.density = size, density
            with self.assertRaises(profile.PhysicalDisplayProfileError):
                self.apply()
            self.assertEqual(self.adb.mutations(), [])

    def test_disables_only_pixel6_and_resolves_empty_reference(self):
        result = self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["cutouts"], dict.fromkeys(profile._CUTOUT_RESOURCES, ""))
        self.assertEqual(result["status_bar_heights"]["portrait"], "24.0dip")
        self.assertEqual(self.adb.mutations(), [
            ("shell", "su", "0", "cmd", "overlay", "disable", "--user", "0", overlay)
            for overlay in profile._PIXEL6_OVERLAYS
        ])
        for forbidden in ("fabricate", "enable", "enable-exclusive", "set-priority", "reboot"):
            self.assertFalse(any(forbidden in args for args in self.adb.calls))

    def test_one_attempt_per_boot_and_retry_on_new_boot(self):
        self.assertEqual(self.apply()["status"], "applied")
        count = len(self.adb.calls)
        self.assertTrue(self.apply()["cached"])
        self.assertEqual(len(self.adb.mutations()), 2)
        self.assertEqual(self.adb.calls[count:], [
            ("shell", "getprop", "sys.boot_completed"),
            ("shell", "cat", "/proc/sys/kernel/random/boot_id"),
        ])
        self.adb.boot_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n"
        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(len(self.adb.mutations()), 4)

    def test_overlay_failure_is_explained_and_not_repeated_per_connection(self):
        self.adb.fail_disable = True
        with self.assertLogs(profile._log, level="WARNING") as messages:
            result = self.apply()
        self.assertEqual(result["status"], "warning")
        self.assertIn("Permission denied", " ".join(result["errors"]))
        self.assertIn("adjustment incomplete", " ".join(messages.output))
        self.assertTrue(self.apply()["cached"])
        self.assertEqual(len(self.adb.mutations()), 2)

    def test_remaining_cutout_is_detected_but_remote_access_not_blocked(self):
        self.adb.cutout = "M 507,64 a 33,33 0 1 0 66,0 Z @left\n"
        with self.assertLogs(profile._log, level="WARNING"):
            result = self.apply()
        self.assertEqual(result["status"], "warning")
        self.assertIn("still has a cutout", " ".join(result["errors"]))

    def test_height_probe_failure_does_not_invalidate_removed_cutout(self):
        self.adb.fail_height = True
        with self.assertLogs(profile._log, level="WARNING"):
            result = self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["status_bar_heights"], {})

    def test_missing_boot_id_never_mutates_or_memoizes(self):
        self.adb.boot_id = "\n"
        with self.assertLogs(profile._log, level="WARNING"):
            self.assertEqual(self.apply()["status"], "deferred")
        self.assertEqual(self.adb.mutations(), [])
        self.adb.boot_id = "recovered-boot-id\n"
        self.assertEqual(self.apply()["status"], "applied")

    def test_explicit_1080_profile_preserves_physical_geometry(self):
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "1080x1920"}
        self.adb.size = "Physical size: 1080x1920\n"
        self.adb.density = "Physical density: 480\n"
        result = self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["profile"], "1080x1920")
        self.assertEqual(result["physical_density"], 480)
        self.assertEqual([args for args in self.adb.calls if args[:2] == ("shell", "wm")],
                         [("shell", "wm", "size"), ("shell", "wm", "density")])

    def test_1080_rejects_an_old_720_override(self):
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "1080x1920"}
        self.adb.size = "Physical size: 1080x1920\nOverride size: 720x1280\n"
        self.adb.density = "Physical density: 480\n"
        with self.assertRaises(profile.PhysicalDisplayProfileError):
            self.apply()
        self.assertEqual(self.adb.mutations(), [])

    def test_explicit_720_profile_preserves_physical_geometry(self):
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "720x1280"}
        self.adb.size = "Physical size: 720x1280\n"
        self.adb.density = "Physical density: 320\n"
        result = self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["profile"], "720x1280")
        self.assertEqual(result["physical_density"], 320)
        self.assertEqual(result["cutouts"], dict.fromkeys(profile._CUTOUT_RESOURCES, ""))
        self.assertEqual(len(self.adb.mutations()), 2)
        self.assertEqual([args for args in self.adb.calls if args[:2] == ("shell", "wm")],
                         [("shell", "wm", "size"), ("shell", "wm", "density")])
        self.assertTrue(self.apply()["cached"])
        self.assertEqual(len(self.adb.mutations()), 2)

    def test_720_requires_physical_mode_and_rejects_conflicting_overrides(self):
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "720x1280"}
        for size, density in (
            ("Physical size: 540x1200\nOverride size: 720x1280\n", "Physical density: 320\n"),
            ("Physical size: 720x1280\n", "Physical density: 210\nOverride density: 320\n"),
            ("Physical size: 720x1280\nOverride size: 540x1200\n", "Physical density: 320\n"),
            ("Physical size: 720x1280\n", "Physical density: 320\nOverride density: 210\n"),
            ("Override size: 720x1280\n", "Physical density: 320\n"),
        ):
            with self.subTest(size=size, density=density):
                self.adb.size, self.adb.density = size, density
                with self.assertRaises(profile.PhysicalDisplayProfileError):
                    self.apply()
                self.assertEqual(self.adb.mutations(), [])

    def test_profile_change_cannot_reuse_prior_geometry_validation(self):
        self.assertEqual(self.apply()["status"], "applied")
        self.env = {"DIRECT_PHYSICAL_DISPLAY": "720x1280"}
        with self.assertRaises(profile.PhysicalDisplayProfileError):
            self.apply()
        self.assertEqual(len(self.adb.mutations()), 2)
        self.adb.size, self.adb.density = "Physical size: 720x1280\n", "Physical density: 320\n"
        result = self.apply()
        self.assertEqual(result["profile"], "720x1280")
        self.assertNotIn("cached", result)
        self.assertEqual(len(self.adb.mutations()), 4)


if __name__ == "__main__":
    unittest.main()
