"""Fake numeric reports only; no packet sender, ADB, VM, media or network action."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC=importlib.util.spec_from_file_location("phone_udp_summary",Path(__file__).resolve().parents[1]/"scripts/probes/summarize_phone_udp.py")
SUMMARY=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


def fixture():
    start=10_000_000_000
    frames=[]
    for index in range(30):
        arrival=start+2_000_000_000+index*16_666_667
        queued=arrival+1_000_000;ready=queued+20_000_000;callback=ready+1_000_000
        frames.append(dict(pts_us=1_000_000+index*16667,received_ns=arrival,input_queued_ns=queued,
            decoder_ready_ns=ready,released_ns=ready+300_000,callback_ns=callback,
            scheduled_ns=callback+60_000_000,vendor_render_ns=callback+60_000_000))
    return dict(scope="OFFLINE FAKE REPORT",transport="mock authenticated UDP",source="numeric test fixture, no real phone",
        requested_video_bps=4_000_000,wire_burst_budget_bps=40_000_000,
        host=dict(sent_datagrams=400,sent_encrypted_bytes=448000,keyframe_feedback=2,keyframes_forwarded=1),
        host_failures=[],native_events=[
            dict(event="summary",elapsed_us=1_000_000,source_frames=60,output_frames=50,output_packets=100,final=False),
            dict(event="summary",elapsed_us=2_000_000,source_frames=120,output_frames=110,output_packets=200,final=False)],
        phone=dict(fps_limit=120,buffer_ms=80,requested_seconds=3,start_ns=start,first_server_packet_ns=start+1_500_000_000,
            receive_end_ns=start+4_500_000_000,udp_packets=390,authenticated_packets=385,received_media_frames=165,
            queued_media_frames=160,codec_callback_count=150,presentation_records_evicted=0,
            native_fec=dict(packets=385,frames_delivered=165,frames_expired=3,recovered_shards=10,reference_lost=2,
                dependency_dropped=4,keyframe_requests=2,needs_keyframe=0,max_assembly_latency_us=74000),
            presentation_frames=frames,media_input_observations=[
                {key:row[key] for key in ("pts_us","received_ns","input_queued_ns")} for row in frames],
            # These deliberately wrong pre-aggregates must not replace raw timing.
            input_queue_to_decoder_ready_ms=[9999],source_access_unit_bytes=[1000,2000,3000],samples=[
                dict(t_ns=start+1_000_000_000,media_receive_fps=0,codec_callback_fps=0,native_fec=dict(frames_expired=0,recovered_shards=0)),
                dict(t_ns=start+2_000_000_000,media_receive_fps=30,codec_callback_fps=25,native_fec=dict(frames_expired=1,recovered_shards=4)),
                dict(t_ns=start+3_000_000_000,media_receive_fps=60,codec_callback_fps=55,native_fec=dict(frames_expired=1,recovered_shards=7)),
                dict(t_ns=start+4_000_000_000,media_receive_fps=55,codec_callback_fps=50,native_fec=dict(frames_expired=3,recovered_shards=10))]))


class UdpSummaryCheck(unittest.TestCase):
    def test_requested_cap_and_host_loopback_are_not_phone_display_evidence(self):
        with self.assertRaises(ValueError):
            SUMMARY.summarize(dict(source_frames=120,source_encoded_fps=60))
        result=SUMMARY.summarize(fixture())
        self.assertEqual(result["phone_configuration"]["fps_limit"],120)
        self.assertAlmostEqual(result["logical_media_receive_fps_over_receive_window"],55)
        self.assertFalse(result["actual_display_fps_measured"])
        self.assertFalse(result["actual_audio_video_skew_measured"])
        self.assertFalse(result["codec_callbacks"]["physical_display_cadence_measured"])

    def test_unverified_future_vendor_is_rejected_and_raw_local_stages_recomputed(self):
        result=SUMMARY.summarize(fixture())
        self.assertEqual(result["codec_timestamp_validity"]["future_timestamps"],30)
        self.assertTrue(result["codec_timestamp_validity"]["requested_target_echo_suspected"])
        self.assertEqual(result["independent_local_stage_times"]["input_queue_to_decoder_ready_ms"]["mean"],20)
        self.assertEqual(result["independent_local_stage_times"]["all_queued_media_receive_to_input_queue_ms"]["mean"],1)
        self.assertAlmostEqual(result["codec_callbacks"]["receipt_interval_fps"],60,places=4)
        self.assertFalse(result["codec_callbacks"]["complete_history_retained"])

    def test_sample_windows_keep_startup_and_actual_fluctuations_separate(self):
        result=SUMMARY.summarize(fixture())["sample_statistics"]
        all_samples=result["all_samples_including_startup"]
        media=result["full_media_intervals_only"]
        self.assertEqual(all_samples["sample_count"],4)
        self.assertEqual(media["sample_count"],2)
        self.assertEqual(all_samples["rates"]["media_receive_fps"]["min"],0)
        self.assertEqual(media["rates"]["media_receive_fps"]["mean"],57.5)
        self.assertEqual(media["codec_callback_fps_samples_below_55"],1)

    def test_cumulative_native_and_host_snapshots_are_not_summed(self):
        result=SUMMARY.summarize(fixture())
        self.assertEqual(result["native_fec_cumulative_counters"]["frames_expired"],3)
        self.assertEqual([row["native_fec_delta"]["frames_expired"] for row in result["per_second_series"]],[0,1,0,2])
        self.assertEqual(result["host_packetizer"]["latest_cumulative_counters"]["source_frames"],120)
        self.assertEqual(result["host_packetizer"]["interval_rates"]["source_frames_per_second"]["mean"],60)
        self.assertFalse(result["host_packetizer"]["latest_snapshot_is_final"])
        self.assertFalse(result["cross_scope_packet_count_difference"]["is_network_packet_loss_measurement"])

    def test_duration_weighted_host_rates_use_elapsed_delta(self):
        raw=fixture()
        raw["native_events"].append(dict(event="summary",elapsed_us=4_000_000,source_frames=200,output_frames=180,output_packets=300,final=True))
        result=SUMMARY.summarize(raw)["host_packetizer"]
        self.assertAlmostEqual(result["duration_weighted_interval_rates"]["source_frames_per_second"],140/3)
        self.assertTrue(result["latest_snapshot_is_final"])

    def test_invalid_samples_timestamps_stages_and_counters_stay_distinct(self):
        raw=fixture()
        raw["phone"]["samples"][2]["native_fec"]["frames_expired"]=0
        raw["phone"]["samples"][2]["media_receive_fps"]=float("nan")
        raw["phone"]["samples"][3]["t_ns"]=float("nan")
        raw["phone"]["presentation_frames"][0]["decoder_ready_ns"]=1
        result=SUMMARY.summarize(raw)
        self.assertEqual(result["invalid_local_stage_records"]["input_queue_to_decoder_ready_ms"],1)
        self.assertTrue(any("counter decreased" in warning for warning in result["warnings"]))
        self.assertIsNone(result["per_second_series"][3]["t_ns"])
        json.dumps(result,allow_nan=False)

    def test_empty_partial_failure_report_does_not_invent_measurements(self):
        result=SUMMARY.summarize(dict(host={},phone=dict(failure_class="IOException")))
        self.assertIsNone(result["logical_media_receive_fps_over_receive_window"])
        self.assertIsNone(result["codec_callbacks"]["receipt_interval_fps"])
        self.assertEqual(result["phone_configuration"]["failure_class"],"IOException")
        self.assertFalse(result["actual_display_fps_measured"])


if __name__=="__main__":
    unittest.main()
