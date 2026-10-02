#!/usr/bin/env python3
"""Replay one fixed app-private synthetic fixture; no streaming endpoint or account."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess

try:
    from scripts.probes.run_phone_transport import codec_timestamp_validity, distribution
except ModuleNotFoundError:
    from run_phone_transport import codec_timestamp_validity, distribution

REPORT_BASENAME = "codec-test-report.json"
REPORT_REMOTE = "/data/user/0/local.remoteandroid.direct/files/" + REPORT_BASENAME
FIXTURE_REMOTE = "/data/user/0/local.remoteandroid.direct/files/codec-test.h264framed"
COMPONENT = "local.remoteandroid.phoneprobe/local.remoteandroid.direct.CodecFileProbe"


def client_configuration(experimental_client=False):
    """Only the two fixed app/probe pairs can supply private fixture/report paths."""
    if type(experimental_client) is not bool:
        raise ValueError("Experimental client selection must be boolean")
    package = "local.remoteandroid.direct.experiment" if experimental_client else "local.remoteandroid.direct"
    probe = "local.remoteandroid.phoneprobe.experiment" if experimental_client else "local.remoteandroid.phoneprobe"
    directory = "/data/user/0/" + package + "/files/"
    return {"client_package": package, "probe_package": probe,
            "component": probe + "/local.remoteandroid.direct.CodecFileProbe",
            "report_file": directory + REPORT_BASENAME,
            "fixture_file": directory + "codec-test.h264framed"}


def verify_instrumentation_target(output, experimental_client=False):
    """Fail before private report cleanup unless the exact installed target matches."""
    if isinstance(output, bytes):
        if len(output) > 65536:
            raise ValueError("Instrumentation listing outside bounds")
        try:
            output = output.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Instrumentation listing is not UTF-8") from None
    if not isinstance(output, str) or len(output) > 65536:
        raise ValueError("Instrumentation listing outside bounds")
    client = client_configuration(experimental_client)
    prefix = "instrumentation:" + client["component"]
    targets = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("instrumentation:"):
            continue
        words = line[len("instrumentation:"):].split(None, 1)
        if not words or words[0] != client["component"]:
            continue
        match = re.fullmatch(re.escape(prefix) + r"[ \t]+\(target=([A-Za-z0-9_.]+)\)", line)
        targets.append(match.group(1) if match else None)
    if targets != [client["client_package"]]:
        raise RuntimeError("Installed codec instrumentation target mismatch, ambiguous or unavailable")
    return True


def verify_codec_report(report, experimental_client, fps, buffer_ms, release, arrival_clock, display_hz):
    """Require phone-side identity and requested settings; never infer new APK features."""
    client = client_configuration(experimental_client)
    if not isinstance(report, dict):
        raise ValueError("Missing codec report readback")
    expected = {"client_package": client["client_package"], "probe_package": client["probe_package"],
                "experimental_client": experimental_client, "fps_limit": fps, "buffer_ms": buffer_ms,
                "video_release_mode": release, "decoder_reanchor_enabled": not arrival_clock,
                "requested_display_hz": fps if display_hz is None else display_hz,
                "display_hz_explicit": display_hz is not None}
    if any(type(report.get(key)) is not type(value) or report[key] != value for key, value in expected.items()):
        raise ValueError("Codec package or requested setting readback mismatch")
    if display_hz is not None:
        for phase in ("start", "end"):
            mode = report.get("display_mode_" + phase)
            hz = mode.get("refresh_hz") if isinstance(mode, dict) else None
            if (report.get("display_mode_" + phase + "_matches_requested") is not True
                    or not isinstance(mode, dict) or mode.get("available") is not True
                    or type(hz) not in (int, float) or not math.isfinite(hz)
                    or abs(hz-display_hz) > 1.0):
                raise ValueError("Explicit codec display mode readback mismatch")
    return True


def read_report(stdout, read_fixed_private_report):
    """The reader receives no supplied path; only this fixed basename is accepted."""
    basename = expected = inline = None
    for line in stdout.decode(errors="replace").splitlines():
        if line.startswith("INSTRUMENTATION_RESULT: report_file="):
            basename = line.split("report_file=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_RESULT: report_bytes="):
            expected = line.split("report_bytes=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_RESULT: report="):
            inline = json.loads(line.split("report=", 1)[1])
    if basename is not None:
        if basename != REPORT_BASENAME:
            raise ValueError("Unexpected codec report basename")
        if expected is None or not expected.isdecimal() or not 0 < int(expected) <= 64 * 1024 * 1024:
            raise ValueError("Invalid codec report byte count")
        payload = read_fixed_private_report()
        if len(payload) != int(expected):
            raise ValueError("Codec report byte count mismatch")
        report = json.loads(payload.decode("utf-8"))
    elif inline is not None:
        report = inline
    else:
        # Only a short instrumentation error, never an app log or payload dump.
        raise RuntimeError("Codec component probe returned no report pointer")
    if not isinstance(report, dict):
        raise ValueError("Codec report must be a JSON object")
    return report


def summarize(report):
    """Every rate here is input or codec-callback throughput, not display FPS."""
    fields = ("test_scope", "profile", "fixture_sha256", "fixture_bytes", "source_geometry",
              "source_media_frames", "source_duration_ms", "source_pts_fps", "fps_limit",
              "buffer_ms", "video_release_mode", "decoder", "hardware", "configure_elapsed_ms",
              "queued_media_frames", "queued_config_records", "late_discarded_count",
              "codec_callback_count", "last_queued_pts_us", "last_codec_callback_pts_us",
              "last_completed_stage", "running_at_end", "failure_class", "client_package", "probe_package",
              "experimental_client", "instrumentation_target_verified", "package_report_verified",
              "valid_codec_component_test", "report_validation_failure_class", "decoder_reanchor_enabled",
              "requested_display_hz", "display_hz_explicit", "display_mode_start", "display_mode_end",
              "display_mode_start_matches_requested", "display_mode_end_matches_requested")
    summary = {name: report[name] for name in fields if name in report}
    summary.update(actual_display_fps_measured=False, actual_audio_video_skew_measured=False,
                   streaming_network_measured=False, codec_timestamp_validity=codec_timestamp_validity(report))
    for name in ("input_queue_to_decoder_ready_ms", "receive_to_input_queue_ms", "requested_target_lead_ms",
                 "decoder_ready_to_callback_receipt_ms", "feed_lateness_ms", "source_access_unit_bytes"):
        summary[name] = distribution(report.get(name, []))
    frames = report.get("presentation_frames", [])
    callbacks = [frame["callback_ns"] for frame in frames if isinstance(frame.get("callback_ns"), int)]
    if len(callbacks) > 1 and callbacks[-1] > callbacks[0]:
        summary["codec_callback_interval_fps"] = round((len(callbacks) - 1) * 1e9 / (callbacks[-1] - callbacks[0]), 3)
        summary["codec_callback_receipt_gaps_ms"] = distribution([(b - a) / 1e6 for a, b in zip(callbacks, callbacks[1:])])
    feeds = report.get("feed_observations", [])
    arrivals = [frame["received_ns"] for frame in feeds if isinstance(frame.get("received_ns"), int)]
    if len(arrivals) > 1 and arrivals[-1] > arrivals[0]:
        summary["component_feed_interval_fps"] = round((len(arrivals) - 1) * 1e9 / (arrivals[-1] - arrivals[0]), 3)
        summary["component_feed_gaps_ms"] = distribution([(b - a) / 1e6 for a, b in zip(arrivals, arrivals[1:])])
    queued = report.get("queued_media_frames", 0)
    summary["codec_callback_records_fraction_of_queued"] = len(frames) / queued if queued else None
    # Preserve full raw data and whole-run summaries above. A separately labelled
    # steady-window view excludes initial codec startup and the final drain tail.
    start = report.get("feed_start_ns", 0) + 1_000_000_000
    end = report.get("feed_end_ns", 0)
    stable = [frame for frame in frames if start <= frame.get("received_ns", 0) <= end]
    summary["steady_window_definition"] = "Source receive time >= paced feed start + 1 s, <= last feed completion; no frame data removed"
    summary["steady_input_queue_to_decoder_ready_ms"] = distribution([
        (frame["decoder_ready_ns"] - frame["input_queued_ns"]) / 1e6 for frame in stable
        if isinstance(frame.get("decoder_ready_ns"), int) and isinstance(frame.get("input_queued_ns"), int)])
    summary["metric_definitions"] = {
        "codec_callback_interval_fps": "OnFrameRenderedListener receipt count and wall interval; NOT physical display FPS",
        "component_feed_interval_fps": "File replay receipt count and wall interval; includes decoder input backpressure; no network",
        "input_queue_to_decoder_ready_ms": "Input submitted -> output dequeued, including codec buffering/queue; not GPU execution time",
        "receive_to_input_queue_ms": "Paced file receipt -> decoder input submission, including input-slot waiting",
        "vendor_render_ns": "Unverified vendor callback timestamp; exact requested-target echoes do not prove display presentation",
        "actual_audio_video_skew_measured": "False: this isolated probe creates no AudioTrack",
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="3B15AL00M9U00000")
    parser.add_argument("--fps", type=int, choices=(60, 120), default=60)
    parser.add_argument("--experimental-client", action="store_true",
                        help="Use only the fixed isolated experiment app/probe pair")
    parser.add_argument("--buffer", type=int, default=80)
    parser.add_argument("--video-release", choices=("scheduled", "immediate"), default="scheduled")
    parser.add_argument("--arrival-clock", action="store_true",
                        help="Disable only decoder-driven shared-clock reanchoring; default preserves existing behavior")
    parser.add_argument("--display-hz", type=int, choices=(60, 90, 120), default=None,
                        help="Explicit same-resolution window mode; omitted preserves the legacy fps refresh hint")
    parser.add_argument("--profile", required=True, help="Caller label for encoder variant; never interpreted as a configuration")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 30 <= args.buffer <= 100:
        parser.error("Buffer must respect the 30..100 ms experiment bound")
    if len(args.profile) > 160:
        parser.error("Profile label exceeds 160 characters")
    adb = [str(Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk"))) / "platform-tools/adb"),
           "-s", args.serial]
    client = client_configuration(args.experimental_client)
    installed = subprocess.run(adb + ["shell", "pm", "list", "instrumentation"],
                               stdin=subprocess.DEVNULL, check=True, capture_output=True, timeout=5)
    target_verified = verify_instrumentation_target(installed.stdout, args.experimental_client)

    def root(command, **kwargs):
        return subprocess.run(adb + ["shell", "su -c " + shlex.quote(command)], check=True, capture_output=True, **kwargs)

    try:
        root("rm -f " + client["report_file"])
        command = ["am", "instrument", "-w", "-e", "fps", str(args.fps), "-e", "buffer_ms", str(args.buffer),
                   "-e", "video_release", args.video_release, "-e", "profile", args.profile,
                   "-e", "experimental_client", str(args.experimental_client).lower(),
                   "-e", "arrival_clock", str(args.arrival_clock).lower()]
        if args.display_hz is not None:
            command.extend(["-e", "display_hz", str(args.display_hz)])
        command.append(client["component"])
        result = root(shlex.join(command), timeout=70)
        report = read_report(result.stdout, lambda: root("cat " + client["report_file"]).stdout)
        report["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        report["instrumentation_target_verified"] = target_verified
        try:
            report["package_report_verified"] = verify_codec_report(
                report, args.experimental_client, args.fps, args.buffer, args.video_release,
                args.arrival_clock, args.display_hz)
        except ValueError as failure:
            report["package_report_verified"] = False
            report["report_validation_failure_class"] = type(failure).__name__
        report["valid_codec_component_test"] = (report["package_report_verified"]
            and "failure_class" not in report and report.get("running_at_end") is True)
        summary = summarize(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        args.output.with_name(args.output.stem + "-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False))
        if not report["valid_codec_component_test"]:
            raise SystemExit("Codec component or readback failed; partial bounded report retained")
    finally:
        # This one private output only. Retain input bytes for same-hash A/B replay.
        root("rm -f " + client["report_file"])


if __name__ == "__main__":
    main()
