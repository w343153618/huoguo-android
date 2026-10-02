import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "phone_transport_report", Path(__file__).resolve().parents[1] / "scripts/probes/run_phone_transport.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class PhoneReportMetricsCheck(unittest.TestCase):
    def report(self, frames):
        result = {key: None for key in (
            "transport", "host", "source", "mode", "requested_bps", "decoder", "hardware",
            "size", "fps_limit", "buffer_ms", "late_total", "running_at_end", "adaptive_rejected")}
        result.update(presentation_frames=frames, samples=[{}] * 3 + [
            dict(render_fps=45, audio_queued_ms=134, audio_queued_estimate_ms=132, audio_timestamp_age_ms=2)],
            render_gaps_ms=[16.7], surface_render_vs_scheduled_ms=[0],
            audio_minus_video_pts_estimate_ms=[-111], input_queue_to_decoder_ready_ms=[172])
        return result

    def test_future_target_echo_does_not_certify_display_or_audio_video(self):
        frames = [dict(surface_ns=200 + index * 100, callback_ns=150 + index * 100,
                       scheduled_ns=200 + index * 100, released_ns=100 + index * 100) for index in range(30)]
        summary = REPORT.summarize(self.report(frames))
        validity = summary["codec_timestamp_validity"]
        self.assertEqual(validity["future_timestamps"], 30)
        self.assertTrue(validity["requested_target_echo_suspected"])
        self.assertFalse(summary["actual_display_fps_measured"])
        self.assertFalse(summary["actual_audio_video_skew_measured"])
        self.assertNotIn("surface_render_vs_scheduled_ms", summary)
        self.assertNotIn("audio_minus_video_pts_estimate_ms", summary)
        self.assertEqual(summary["vendor_callback_observations"]["audio_minus_video_pts_estimate_ms"]["mean"], -111)
        self.assertEqual(summary["input_queue_to_decoder_ready_ms"]["mean"], 172)
        self.assertEqual(summary["audio_queued_estimate_ms"]["mean"], 132)

    def test_render_before_release_is_not_causally_valid(self):
        validity = REPORT.codec_timestamp_validity(self.report([
            dict(surface_ns=100, callback_ns=150, released_ns=120, scheduled_ns=100)]))
        self.assertEqual(validity["before_release_timestamps"], 1)
        self.assertFalse(validity["usable_for_presentation_timestamp_estimate"])

    def test_past_target_echo_still_is_not_independent_presentation(self):
        frames = [dict(surface_ns=200 + index * 100, callback_ns=250 + index * 100,
                       scheduled_ns=200 + index * 100, released_ns=100 + index * 100) for index in range(30)]
        validity = REPORT.codec_timestamp_validity(self.report(frames))
        self.assertEqual(validity["invalid_causal_timestamps"], 0)
        self.assertEqual(validity["status"], "unverified_requested_target_echo")
        self.assertFalse(validity["usable_for_presentation_timestamp_estimate"])

    def test_plausible_timestamp_is_still_not_physical_display_proof(self):
        validity = REPORT.codec_timestamp_validity(self.report([
            dict(surface_ns=200, callback_ns=250, released_ns=100, scheduled_ns=180)]))
        self.assertTrue(validity["usable_for_presentation_timestamp_estimate"])
        self.assertFalse(validity["independent_display_presentation_measured"])

    def test_fixed_report_pointer_preserves_large_payload(self):
        payload = json.dumps({"frames": [dict(pts=index, note="完整采样" * 8) for index in range(10000)]},
                             ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(payload), 1_000_000)
        response = ("INSTRUMENTATION_RESULT: report_file=" + REPORT.REPORT_BASENAME
                    + "\nINSTRUMENTATION_RESULT: report_bytes=" + str(len(payload))).encode()
        self.assertEqual(len(REPORT.read_instrumentation_report(response, lambda: payload)["frames"]), 10000)
        response = b"INSTRUMENTATION_RESULT: report_file=../../other\nINSTRUMENTATION_RESULT: report_bytes=1"
        with self.assertRaises(ValueError):
            REPORT.read_instrumentation_report(response, lambda: self.fail("arbitrary path read"))


if __name__ == "__main__":
    unittest.main()
