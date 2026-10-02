import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "codec_file_report", Path(__file__).resolve().parents[1] / "scripts/probes/run_codec_file_probe.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class CodecFileReportCheck(unittest.TestCase):
    def pointer(self, size, basename=REPORT.REPORT_BASENAME):
        return ("INSTRUMENTATION_RESULT: report_file=" + basename + "\n"
                "INSTRUMENTATION_RESULT: report_bytes=" + str(size)).encode()

    def test_large_private_report_preserves_every_frame(self):
        payload = json.dumps({"presentation_frames": [dict(pts_us=index, note="bounded numeric sample " * 16)
                                                    for index in range(3601)]}).encode()
        self.assertGreater(len(payload), 1_000_000)
        self.assertEqual(3601, len(REPORT.read_report(self.pointer(len(payload)), lambda: payload)["presentation_frames"]))

    def test_only_fixed_codec_report_basename_is_allowed(self):
        for name in ("../../auth.json", "transport-test-report.json", REPORT.REPORT_REMOTE):
            with self.subTest(name=name), self.assertRaises(ValueError):
                REPORT.read_report(self.pointer(1, name), lambda: self.fail("No arbitrary path read"))

    def test_pointer_requires_exact_bounded_byte_count_and_object(self):
        for count in (0, -1, 64 * 1024 * 1024 + 1, "NaN"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                REPORT.read_report(self.pointer(count), lambda: self.fail("Invalid count must not read"))
        with self.assertRaises(ValueError):
            REPORT.read_report(self.pointer(10), lambda: b"{}")
        with self.assertRaises(ValueError):
            REPORT.read_report(self.pointer(2), lambda: b"[]")

    def test_small_partial_failure_report_stays_interpretable(self):
        summary = REPORT.summarize({"failure_class": "IOException", "last_completed_stage": "fixture_validated"})
        self.assertEqual(summary["failure_class"], "IOException")
        self.assertFalse(summary["actual_display_fps_measured"])
        self.assertFalse(summary["streaming_network_measured"])

    def test_callback_throughput_never_certifies_display_and_future_echo_is_rejected(self):
        origin = 10_000_000_000
        frames = [dict(pts_us=index, callback_ns=origin + index * 16_666_667,
                       vendor_render_ns=origin + index * 16_666_667 + 80_000_000,
                       scheduled_ns=origin + index * 16_666_667 + 80_000_000,
                       released_ns=origin + index * 16_666_667 - 1_000_000,
                       received_ns=origin + index * 16_666_667 - 180_000_000,
                       input_queued_ns=origin + index * 16_666_667 - 170_000_000,
                       decoder_ready_ns=origin + index * 16_666_667 - 5_000_000) for index in range(120)]
        summary = REPORT.summarize(dict(presentation_frames=frames, queued_media_frames=120,
            feed_start_ns=origin - 180_000_000, feed_end_ns=origin + 2_000_000_000,
            input_queue_to_decoder_ready_ms=[165] * 120))
        self.assertAlmostEqual(summary["codec_callback_interval_fps"], 60, places=3)
        self.assertFalse(summary["actual_display_fps_measured"])
        self.assertFalse(summary["actual_audio_video_skew_measured"])
        self.assertEqual(summary["codec_timestamp_validity"]["future_timestamps"], 120)
        self.assertEqual(summary["input_queue_to_decoder_ready_ms"]["mean"], 165)
        self.assertEqual(summary["steady_input_queue_to_decoder_ready_ms"]["mean"], 165)
        self.assertLess(summary["steady_input_queue_to_decoder_ready_ms"]["count"], 120)

    def listing(self, experimental=True, target=None):
        client = REPORT.client_configuration(experimental)
        return ("instrumentation:" + client["component"] + " (target=" +
                (target or client["client_package"]) + ")\n").encode()

    def matched_report(self, experimental=True, fps=120, buffer_ms=100, arrival=True, display=60):
        client = REPORT.client_configuration(experimental)
        return {"client_package": client["client_package"], "probe_package": client["probe_package"],
                "experimental_client": experimental, "fps_limit": fps, "buffer_ms": buffer_ms,
                "video_release_mode": "scheduled", "decoder_reanchor_enabled": not arrival,
                "requested_display_hz": fps if display is None else display,
                "display_hz_explicit": display is not None, "running_at_end": True,
                "display_mode_start": {"available": True, "refresh_hz": display or fps},
                "display_mode_end": {"available": True, "refresh_hz": display or fps},
                "display_mode_start_matches_requested": True,
                "display_mode_end_matches_requested": True}

    def test_fixed_pairs_preserve_defaults_without_arbitrary_package_paths(self):
        formal = REPORT.client_configuration()
        self.assertEqual(formal["component"], REPORT.COMPONENT)
        self.assertEqual(formal["report_file"], REPORT.REPORT_REMOTE)
        self.assertEqual(formal["fixture_file"], REPORT.FIXTURE_REMOTE)
        experiment = REPORT.client_configuration(True)
        self.assertEqual(experiment["report_file"],
                         "/data/user/0/local.remoteandroid.direct.experiment/files/codec-test-report.json")
        self.assertTrue(REPORT.verify_instrumentation_target(self.listing(True), True))
        self.assertTrue(REPORT.verify_instrumentation_target(self.listing(False), False))
        with self.assertRaises(ValueError):
            REPORT.client_configuration("arbitrary.package")

    def test_target_identity_rejects_mismatch_duplicates_malformed_and_unbounded_listing(self):
        good = self.listing()
        for value in (self.listing(target="local.remoteandroid.direct"), good + good,
                      good.replace(b" (target=", b" malformed="), b"", b"\xff", b"x" * 65537):
            with self.subTest(value_length=len(value)), self.assertRaises((RuntimeError, ValueError)):
                REPORT.verify_instrumentation_target(value, True)

    def test_requested_report_identity_clock_buffer_and_explicit_display_fail_closed(self):
        expected = self.matched_report()
        self.assertTrue(REPORT.verify_codec_report(expected, True, 120, 100, "scheduled", True, 60))
        mutations = (("client_package", "local.remoteandroid.direct"),
                     ("probe_package", "local.remoteandroid.phoneprobe"),
                     ("experimental_client", 1), ("buffer_ms", 80),
                     ("decoder_reanchor_enabled", True), ("requested_display_hz", 120),
                     ("display_mode_end_matches_requested", False),
                     ("display_mode_start", {"available": True, "refresh_hz": float("inf")}),
                     ("display_mode_end", {"available": True, "refresh_hz": 120}))
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError):
                REPORT.verify_codec_report(dict(expected, **{key: value}), True, 120, 100, "scheduled", True, 60)
        with self.assertRaises(ValueError):
            REPORT.verify_codec_report({}, True, 120, 100, "scheduled", True, 60)
        # Legacy omission keeps its original refresh hint and records rather than
        # certifies the physical mode; an explicit mode cannot use this exception.
        formal = self.matched_report(False, 60, 80, False, None)
        formal["display_mode_end_matches_requested"] = False
        formal["display_mode_end"] = {"available": True, "refresh_hz": 90}
        self.assertTrue(REPORT.verify_codec_report(formal, False, 60, 80, "scheduled", False, None))

    def test_main_target_failure_precedes_all_private_file_operations(self):
        wrong = subprocess.CompletedProcess([], 0, self.listing(target="local.remoteandroid.direct"), b"")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.json"
            argv = ["codec", "--experimental-client", "--profile", "same-fixture", "--output", str(output)]
            with mock.patch("sys.argv", argv), mock.patch.object(REPORT.subprocess, "run", return_value=wrong) as run:
                with self.assertRaises(RuntimeError):
                    REPORT.main()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-4:], ["shell", "pm", "list", "instrumentation"])
            self.assertFalse(output.exists())

    def run_mocked_main(self, report, output):
        payload = json.dumps(report).encode();calls=[]
        def execute(command, **kwargs):
            calls.append(command)
            if command[-4:] == ["shell", "pm", "list", "instrumentation"]:
                body = self.listing()
            elif "am instrument" in command[-1]:
                body = self.pointer(len(payload))
            elif "cat " in command[-1]:
                body = payload
            else:
                body = b""
            return subprocess.CompletedProcess(command, 0, body, b"")
        argv = ["codec", "--experimental-client", "--fps", "120", "--buffer", "100", "--arrival-clock",
                "--display-hz", "60", "--profile", "same-fixture", "--output", str(output)]
        with mock.patch("sys.argv", argv), mock.patch.object(REPORT.subprocess, "run", side_effect=execute), mock.patch("builtins.print"):
            REPORT.main()
        return calls

    def test_experimental_main_reads_cleans_only_matched_private_report_and_forwards_options(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.json"
            calls = self.run_mocked_main(self.matched_report(), output)
            saved = json.loads(output.read_text())
            self.assertTrue(saved["valid_codec_component_test"])
            self.assertTrue(saved["instrumentation_target_verified"])
            self.assertTrue(saved["package_report_verified"])
            private = [command[-1] for command in calls if command[-2] == "shell"]
            self.assertEqual(sum("rm -f " in command for command in private), 2)
            self.assertTrue(all("direct.experiment/files/codec-test-report.json" in command
                                for command in private if "rm -f " in command or "cat " in command))
            invocation = next(command for command in private if "am instrument" in command)
            for argument in ("-e buffer_ms 100", "-e arrival_clock true", "-e display_hz 60",
                             "-e experimental_client true", "phoneprobe.experiment/local.remoteandroid.direct.CodecFileProbe"):
                self.assertIn(argument, invocation)

    def test_mismatched_phone_readback_keeps_failure_evidence_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.json"
            with self.assertRaises(SystemExit):
                self.run_mocked_main(dict(self.matched_report(), buffer_ms=80), output)
            saved = json.loads(output.read_text())
            self.assertFalse(saved["valid_codec_component_test"])
            self.assertFalse(saved["package_report_verified"])
            self.assertEqual(saved["report_validation_failure_class"], "ValueError")
            self.assertEqual(saved["buffer_ms"], 80)

    def test_java_probe_accepts_fixed_pairs_and_independent_clock_display_without_audio_or_inbox(self):
        java = (Path(__file__).resolve().parents[1] / "experiments/nps-transport/phone/CodecFileProbe.java").read_text()
        self.assertIn('new PlaybackClock(buffer,0,decoderReanchorEnabled)', java)
        self.assertIn('buffer>100', java)
        self.assertIn('getTargetContext().getPackageName()', java)
        self.assertIn('window.preferredDisplayModeId=restoreDisplayModeId', java)
        self.assertNotIn('new UdpAudioReceiver', java)
        self.assertNotIn('new VideoInbox', java)


if __name__ == "__main__":
    unittest.main()
