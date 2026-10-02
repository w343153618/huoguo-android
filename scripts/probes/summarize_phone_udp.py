#!/usr/bin/env python3
"""Offline numeric summary of combined run_phone_udp host/phone evidence. No device actions."""
import argparse
import json
import math
from pathlib import Path

try:
    from scripts.probes.run_phone_transport import codec_timestamp_validity, distribution
except ModuleNotFoundError:
    from run_phone_transport import codec_timestamp_validity, distribution

NATIVE_COUNTERS = ("packets", "wire_bytes", "invalid", "duplicate", "settled_packets", "expired_packets",
                   "frames_expired", "frames_delivered", "recovered_shards", "reference_lost", "keyframe_requests",
                   "dependency_dropped", "memory_rejected", "clock_mapping_rejected", "clock_mapping_evictions",
                   "clock_mapping_expired", "logical_body_rejected", "completed_bodies")
NATIVE_GAUGES = ("clock_mappings_active", "needs_keyframe", "max_assembly_latency_us")
HOST_PACKETIZER_COUNTERS = ("source_frames", "config_messages", "source_idr", "output_frames", "output_packets",
    "plaintext_bytes", "estimated_ipv4_encrypted_wire_bytes", "frame_budget_drops", "output_deadline_drops",
    "unsupported_frames", "dependent_source_drops", "stdout_blocked_us")
HOST_PACKETIZER_GAUGES = ("stdout_max_blocked_us", "max_au_bytes", "max_wire_frame_bytes", "wire_bitrate")
PHONE_COUNTERS = ("udp_packets", "udp_payload_bytes", "foreign_peer_packets", "oversized_packets", "header_errors",
    "authentication_errors", "replay_errors", "authenticated_packets", "ready_requests", "keyframe_feedback_requests",
    "received_media_frames", "queued_media_frames", "queued_config_records", "logical_body_errors", "decoder_input_timeouts",
    "waiting_idr_dropped_frames", "codec_callback_count", "late_discarded_count", "presentation_records_evicted",
    "presentation_pending_evicted", "surface_unmatched_callbacks")
SAMPLE_RATES = ("media_receive_fps", "codec_callback_fps", "udp_payload_mbps", "late_discarded_fps")


def numeric(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def stats(values):
    return distribution([value for value in values if numeric(value)])


def numbers(source, names):
    return {name: source[name] for name in names if numeric(source.get(name)) and source[name] >= 0}


def duration_ns(start, end):
    return (end - start) / 1e9 if type(start) is int and type(end) is int and 0 < start < end else None


def stages(frames, names):
    output, invalid = {}, {}
    for name, earlier, later in names:
        values, invalid_count = [], 0
        for frame in frames:
            if earlier not in frame or later not in frame:
                continue
            start, end = frame[earlier], frame[later]
            if type(start) is not int or type(end) is not int or start <= 0 or end < start:
                invalid_count += 1
            else:
                values.append((end - start) / 1e6)
        output[name] = stats(values)
        invalid[name] = invalid_count
    return output, invalid


def sample_series(phone):
    """Use actual sample intervals; retain startup and separately label full media windows."""
    previous_time = phone.get("start_ns")
    first = phone.get("first_server_packet_ns")
    finish = phone.get("receive_end_ns")
    previous_native = {key: 0 for key in NATIVE_COUNTERS}
    series, warnings = [], []
    for index, sample in enumerate(phone.get("samples", [])):
        if not isinstance(sample, dict):
            warnings.append("Non-object sample at index " + str(index)); continue
        now = sample.get("t_ns")
        elapsed = duration_ns(previous_time, now)
        full_media = (elapsed is not None and type(first) is int and first > 0 and previous_time >= first
                      and type(finish) is int and now <= finish)
        row = {"sample_index": index, "t_ns": now if type(now) is int and now > 0 else None, "interval_seconds": elapsed, "full_media_interval": full_media,
               "rates": numbers(sample, SAMPLE_RATES), "native_fec_delta": {}}
        native = sample.get("native_fec", {})
        if isinstance(native, dict):
            row["native_fec_gauges"] = numbers(native, NATIVE_GAUGES)
            for key in NATIVE_COUNTERS:
                value = native.get(key)
                if not numeric(value) or value < 0:
                    continue
                previous = previous_native.get(key)
                if previous is not None and value >= previous:
                    row["native_fec_delta"][key] = value - previous
                elif previous is not None:
                    warnings.append("Native counter decreased: " + key + " at sample " + str(index))
                previous_native[key] = value
        if elapsed is None:
            warnings.append("Unknown/non-increasing sample interval at index " + str(index))
        series.append(row)
        if type(now) is int:
            previous_time = now
    scopes = {}
    for name, subset in (("all_samples_including_startup", series),
                         ("full_media_intervals_only", [row for row in series if row["full_media_interval"]])):
        scopes[name] = {"sample_count": len(subset), "rates": {
            key: stats([row["rates"].get(key) for row in subset]) for key in SAMPLE_RATES}}
        for threshold in (30, 50, 55):
            for key in ("media_receive_fps", "codec_callback_fps"):
                values = [row["rates"][key] for row in subset if key in row["rates"]]
                scopes[name][key + "_samples_below_" + str(threshold)] = sum(value < threshold for value in values)
    return series, scopes, warnings


def packetizer_events(events):
    """Summary events are cumulative snapshots, not additive per-second counters."""
    snapshots = [event for event in events if isinstance(event, dict) and event.get("event") == "summary"]
    latest = snapshots[-1] if snapshots else {}
    series, previous, warnings = [], None, []
    for event in snapshots:
        now = event.get("elapsed_us")
        if previous is not None and type(now) is int and type(previous.get("elapsed_us")) is int and now > previous["elapsed_us"]:
            seconds = (now - previous["elapsed_us"]) / 1e6
            row = {"end_elapsed_us": now, "interval_seconds": seconds, "rates": {}}
            for name in ("source_frames", "output_frames", "output_packets"):
                value, before = event.get(name), previous.get(name)
                if numeric(value) and numeric(before) and value >= before:
                    row["rates"][name + "_per_second"] = (value - before) / seconds
                else:
                    warnings.append("Missing/decreasing packetizer counter: " + name)
            series.append(row)
        previous = event
    output = {"summary_snapshots": len(snapshots), "latest_snapshot_is_final": latest.get("final") is True,
              "latest_elapsed_us": latest.get("elapsed_us") if type(latest.get("elapsed_us")) is int and latest["elapsed_us"] >= 0 else None,
              "latest_cumulative_counters": numbers(latest, HOST_PACKETIZER_COUNTERS),
              "latest_gauges": numbers(latest, HOST_PACKETIZER_GAUGES), "interval_series": series,
              "interval_rates": {key: stats([row["rates"].get(key) for row in series]) for key in
                  ("source_frames_per_second", "output_frames_per_second", "output_packets_per_second")},
              "encoder_budget_feedback_events": sum(event.get("event") == "encoder_budget_feedback" for event in events if isinstance(event, dict)),
              "frame_rejected_events": sum(event.get("event") == "frame_rejected" for event in events if isinstance(event, dict))}
    if snapshots and not output["latest_snapshot_is_final"]:
        warnings.append("Packetizer latest summary is not final; its cumulative counts can omit the last partial interval")
    total_seconds=sum(row["interval_seconds"] for row in series)
    output["interval_span_seconds"]=total_seconds
    output["duration_weighted_interval_rates"]={key:
        sum(row["rates"][key]*row["interval_seconds"] for row in series if key in row["rates"])
        /sum(row["interval_seconds"] for row in series if key in row["rates"])
        if any(key in row["rates"] for row in series) else None
        for key in ("source_frames_per_second","output_frames_per_second","output_packets_per_second")}
    return output, warnings


def summarize(report):
    if not isinstance(report, dict) or not isinstance(report.get("host"), dict) or not isinstance(report.get("phone"), dict):
        raise ValueError("Expected combined run_phone_udp host+phone JSON; host-only loopback is not real-phone evidence")
    host, phone = report["host"], report["phone"]
    summary = {"summary_scope": "offline_combined_host_real_phone_UDP_video_component_metadata",
               "actual_display_fps_measured": False, "actual_audio_video_skew_measured": False, "native_touch_tested": False}
    summary["experiment"] = {key: report[key] for key in
        ("scope", "started_utc", "finished_utc", "transport", "route", "bind_ip", "peer_ip", "interface", "source")
        if isinstance(report.get(key), str)}
    summary["experiment"].update(numbers(report, ("requested_video_bps", "wire_burst_budget_bps")))
    summary["phone_configuration"] = {key: phone[key] for key in
        ("profile", "video_release_mode", "decoder", "source_geometry", "last_completed_stage", "failure_class")
        if isinstance(phone.get(key), str)}
    summary["phone_configuration"].update(numbers(phone, ("fps_limit", "buffer_ms", "requested_seconds", "socket_receive_buffer_bytes")))
    for key in ("hardware", "running_at_end"):
        if type(phone.get(key)) is bool:
            summary["phone_configuration"][key] = phone[key]
    summary["host_counters"] = numbers(host, ("sent_datagrams", "sent_encrypted_bytes", "invalid_feedback", "ready_messages",
                                             "keyframe_feedback", "keyframes_forwarded"))
    summary["host_failures"] = [{key: value[key] for key in ("stage", "error_type") if isinstance(value.get(key), str)}
        for value in report.get("host_failures", []) if isinstance(value, dict)]
    summary["phone_counters"] = numbers(phone, PHONE_COUNTERS)
    native = phone.get("native_fec", {})
    summary["native_fec_cumulative_counters"] = numbers(native, NATIVE_COUNTERS) if isinstance(native, dict) else {}
    summary["native_fec_final_gauges"] = numbers(native, NATIVE_GAUGES) if isinstance(native, dict) else {}
    first, receive_end = phone.get("first_server_packet_ns"), phone.get("receive_end_ns")
    seconds = duration_ns(first, receive_end)
    summary["phone_media_receive_window_seconds"] = seconds
    received = phone.get("received_media_frames")
    summary["logical_media_receive_fps_over_receive_window"] = received / seconds if seconds and numeric(received) and received >= 0 else None
    summary["media_startup_wait_seconds"] = duration_ns(phone.get("start_ns"), first)
    frames = [frame for frame in phone.get("presentation_frames", []) if isinstance(frame, dict)]
    inputs = [frame for frame in phone.get("media_input_observations", []) if isinstance(frame, dict)]
    stage_times, invalid_stages = stages(frames, (
        ("receive_to_input_queue_ms", "received_ns", "input_queued_ns"),
        ("input_queue_to_decoder_ready_ms", "input_queued_ns", "decoder_ready_ns"),
        ("decoder_ready_to_java_callback_ms", "decoder_ready_ns", "callback_ns")))
    all_input, all_input_invalid = stages(inputs, (("all_queued_media_receive_to_input_queue_ms", "received_ns", "input_queued_ns"),))
    stage_times.update(all_input); invalid_stages.update(all_input_invalid)
    summary["independent_local_stage_times"] = stage_times
    summary["invalid_local_stage_records"] = invalid_stages
    summary["source_access_unit_bytes"] = stats(phone.get("source_access_unit_bytes", []))
    summary["codec_timestamp_validity"] = codec_timestamp_validity(dict(presentation_frames=frames))
    callback_times = [frame["callback_ns"] for frame in frames if type(frame.get("callback_ns")) is int and frame["callback_ns"] > 0]
    callback_seconds = duration_ns(callback_times[0], callback_times[-1]) if len(callback_times) > 1 else None
    callback_gaps = [(after - before) / 1e6 for before, after in zip(callback_times, callback_times[1:])]
    summary["codec_callbacks"] = {"raw_records": len(frames), "positive_receipt_times": len(callback_times),
        "receipt_interval_seconds": callback_seconds,
        "receipt_interval_fps": (len(callback_times) - 1) / callback_seconds if callback_seconds else None,
        "receipt_gaps_ms": stats([gap for gap in callback_gaps if gap >= 0]),
        "receipt_clock_reversals": sum(gap < 0 for gap in callback_gaps),
        "receipt_gaps_over_50ms": sum(gap > 50 for gap in callback_gaps),
        "receipt_gaps_over_100ms": sum(gap > 100 for gap in callback_gaps),
        "complete_history_retained": phone.get("presentation_records_evicted",0)==0 and phone.get("codec_callback_count")==len(frames),
        "physical_display_cadence_measured": False}
    summary["per_second_series"], summary["sample_statistics"], warnings = sample_series(phone)
    summary["host_packetizer"], host_warnings = packetizer_events(report.get("native_events", []))
    warnings.extend(host_warnings)
    if not summary["codec_callbacks"]["complete_history_retained"]:
        warnings.append("Raw callback records do not cover the complete reported callback count; timing describes the retained record span")
    sent, observed = host.get("sent_datagrams"), phone.get("udp_packets")
    if numeric(sent) and numeric(observed):
        summary["cross_scope_packet_count_difference"] = {"host_sent_minus_phone_observed": sent - observed,
            "is_network_packet_loss_measurement": False,
            "limitation": "Host send/phone receive windows and cutoff tails differ; receiver totals also include rejected foreign packets"}
    summary["warnings"] = warnings
    summary["metric_definitions"] = {
        "logical_media_receive_fps_over_receive_window": "Complete logical media bodies counted by Java; includes local IDR-wait drops; not unique-picture or display FPS",
        "codec_callbacks": "OnFrameRenderedListener Java receipt count/timing; vendor timestamps may echo requested future targets",
        "independent_local_stage_times": "Recomputed from System.nanoTime stages; codec input-to-ready includes buffering/queues, not GPU execution alone",
        "native_fec_cumulative_counters": "Final snapshot; do not sum cumulative samples or add overlapping expiry/dependency/drop counters as global frame loss",
        "recovered_shards": "FEC shard reconstruction count, not recovered/visibly presented frame count",
        "keyframe_feedback_requests": "Requests sent; host keyframes_forwarded counts control forwarding, not IDR receipt or completed visible recovery",
        "per_second_series": "Actual sample intervals; native counter deltas are changes, gauges remain gauges",
        "full_media_intervals_only": "Intervals whose start >= first authenticated HGUD and end <= receiver window end; startup/partial buckets remain in all_samples",
        "host_packetizer": "Cumulative source/packetizer snapshots and deltas; local stdout scope, not network receipt or physical source refresh rate",
    }
    summary["not_measured"] = ["Physical phone display FPS/cadence: collect independent SurfaceFlinger actual-present evidence",
        "Acoustic audio/video synchronization (video-only component)", "Native touch/input-to-photon latency",
        "One-way host-to-phone latency: host and phone monotonic clocks are not synchronized",
        "WAN NAT/P2P or V50 acceptance unless separately measured on those exact paths/devices"]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.report.stat().st_size > 64 * 1024 * 1024:
        parser.error("Report exceeds the 64 MiB offline processing bound")
    result = summarize(json.loads(args.report.read_text()))
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        if args.output.resolve() == args.report.resolve():
            parser.error("Keep original raw evidence; summary output must be a different path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
