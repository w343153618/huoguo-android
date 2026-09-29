#!/usr/bin/env python3
"""Bounded local emulator framebuffer probe; never persist or print pixels.

Generate emulator_controller_pb2.py and emulator_controller_pb2_grpc.py from
the installed SDK's lib/emulator_controller.proto into a validation directory.
Place that directory on PYTHONPATH, with grpcio and grpcio-reflection installed.
The caller must already have the synthetic DiagnosticSourceActivity running.
This script does not launch activities, change emulator settings, or use server
accounts. It measures local raw capture, not phone/WAN/encoding/touch latency.

Example:
  PYTHONPATH=/private/tmp/validation-proto python3 emulator_grpc_probe.py \
    --discovery /path/to/running/pid_123.ini --duration 10
"""

import argparse
import importlib
import json
import math
import pathlib
import re
import subprocess
import sys
import time


SCENE = "local.remoteandroid.benchmark/.DiagnosticSourceActivity"
SCENE_LONG_COMPONENT = "local.remoteandroid.benchmark/local.remoteandroid.benchmark.DiagnosticSourceActivity"
REQUEST_WIDTH = 540
REQUEST_HEIGHT = 1200
MAX_FRAMES = 2000
RPC_TIMEOUT = 4.0


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    location = (len(ordered) - 1) * quantile
    lower = int(math.floor(location))
    upper = int(math.ceil(location))
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (location - lower)
    return round(value, 3)


def read_discovery(path):
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("discovery must be an existing file smaller than 64 KiB")
    properties = {}
    for row in path.read_text(encoding="utf-8").splitlines():
        row = row.strip()
        if not row or row.startswith(("#", ";")) or "=" not in row:
            continue
        key, value = row.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def loopback_target(target):
    """No DNS lookups, public hosts, wildcard bind addresses, or IPv6 targets."""
    match = re.fullmatch(r"(127\.0\.0\.1|localhost):([0-9]{1,5})", target.strip())
    if not match or not 1 <= int(match.group(2)) <= 65535:
        raise ValueError("target must be 127.0.0.1:<port> or localhost:<port>")
    return "127.0.0.1:" + str(int(match.group(2)))


def target_from_discovery(properties, override):
    endpoint = properties.get("grpc.endpoint")
    discovered = loopback_target(endpoint) if endpoint else None
    port = properties.get("grpc.port")
    if port:
        port_target = loopback_target("127.0.0.1:" + port)
        if discovered and port_target != discovered:
            raise ValueError("discovery gRPC endpoint and port disagree")
        discovered = port_target
    selected = loopback_target(override) if override else discovered
    if not selected:
        raise ValueError("discovery lacks grpc.port; provide a localhost --target")
    if discovered and selected != discovered:
        raise ValueError("--target must match the discovery endpoint")
    return selected


def synthetic_foreground(adb_path, serial):
    """Inspect only focus lines; never include dumpsys contents in the report."""
    result = subprocess.run(
        # Android 17 puts focus in the display/global section, which the
        # narrower "window windows" command omits.
        [str(adb_path), "-s", serial, "shell", "dumpsys", "window"],
        capture_output=True,
        text=True,
        timeout=RPC_TIMEOUT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("could not verify synthetic activity through local ADB")
    focus_rows = [row for row in result.stdout.splitlines() if "mCurrentFocus=" in row]
    if not focus_rows:
        raise RuntimeError("Android did not return a current focus line")
    return any(SCENE in row or SCENE_LONG_COMPONENT in row for row in focus_rows)


def rpc_error(grpc, exc, token):
    if isinstance(exc, grpc.RpcError):
        details = str(exc.details() or "")
        if token:
            details = details.replace(token, "[redacted]")
        return {"code": exc.code().name, "details": " ".join(details.split())[:240]}
    # Unexpected exceptions may contain request metadata; only expose their type.
    return {"code": type(exc).__name__, "details": "local probe operation failed"}


def reflection_services(grpc, channel, metadata, token):
    try:
        reflection_pb2 = importlib.import_module("grpc_reflection.v1alpha.reflection_pb2")
        reflection_grpc = importlib.import_module("grpc_reflection.v1alpha.reflection_pb2_grpc")
    except ImportError:
        return {"available": False, "services": None, "native_rtc_v2_present": None,
                "error": {"code": "DEPENDENCY_MISSING", "details": "grpcio-reflection unavailable"}}
    call = None
    try:
        stub = reflection_grpc.ServerReflectionStub(channel)
        request = reflection_pb2.ServerReflectionRequest(list_services="")
        call = stub.ServerReflectionInfo(iter([request]), timeout=RPC_TIMEOUT, metadata=metadata)
        response = next(call)
        if response.HasField("error_response"):
            # Error text is not required to establish availability and may contain
            # sensitive request metadata on unusual server implementations.
            return {"available": False, "services": None, "native_rtc_v2_present": None,
                    "error": {"code": response.error_response.error_code,
                              "details": "reflection server returned an error"}}
        if not response.HasField("list_services_response"):
            return {"available": False, "services": None, "native_rtc_v2_present": None,
                    "error": {"code": "UNEXPECTED_RESPONSE", "details": "no service list"}}
        services = sorted(service.name for service in response.list_services_response.service)
        return {"available": True, "services": services,
                "native_rtc_v2_present": "android.emulation.control.v2.Rtc" in services}
    except Exception as exc:
        return {"available": False, "services": None, "native_rtc_v2_present": None,
                "error": rpc_error(grpc, exc, token)}
    finally:
        if call is not None:
            call.cancel()


def frame_info(image):
    width = int(image.format.width or image.width)
    height = int(image.format.height or image.height)
    size = len(image.image)
    timestamp_us = int(image.timestampUs)
    age_ms = (time.time_ns() // 1000 - timestamp_us) / 1000 if timestamp_us else None
    return width, height, size, int(image.seq), timestamp_us, age_ms


def screenshot_probe(stub, request, metadata):
    start = time.monotonic()
    image = stub.getScreenshot(request, timeout=RPC_TIMEOUT, metadata=metadata)
    width, height, size, sequence, timestamp_us, age_ms = frame_info(image)
    return {
        "rpc_ms": round((time.monotonic() - start) * 1000, 3),
        "size": [width, height], "pixel_bytes": size, "sequence": sequence,
        "timestamp_present": bool(timestamp_us),
        "timestamp_age_ms": round(age_ms, 3) if age_ms is not None else None,
        "rgba_bytes_match_dimensions": size == width * height * 4 and width > 0 and height > 0
                                      and image.format.format == request.format,
    }


def stream_probe(grpc, stub, request, metadata, duration, token):
    start = time.monotonic()
    call = stub.streamScreenshot(request, timeout=duration, metadata=metadata)
    frames = total_bytes = empty_frames = invalid_frames = 0
    sequence_gaps = sequence_missing = sequence_duplicates = sequence_resets = 0
    first_sequence = last_sequence = None
    last_arrival = last_timestamp = None
    arrival_gaps = []
    timestamp_gaps = []
    ages = []
    dimensions = set()
    negative_ages = 0
    first_frame_ms = None
    end_reason = "deadline"
    errors = []
    try:
        for image in call:
            arrival = time.monotonic()
            if arrival - start >= duration:
                break
            width, height, size, sequence, timestamp_us, age_ms = frame_info(image)
            if width <= 0 or height <= 0 or not size:
                empty_frames += 1
                continue
            if size != width * height * 4 or image.format.format != request.format:
                invalid_frames += 1
                continue
            if first_frame_ms is None:
                first_frame_ms = round((arrival - start) * 1000, 3)
                first_sequence = sequence
            frames += 1
            total_bytes += size
            dimensions.add((width, height))
            if last_arrival is not None:
                arrival_gaps.append((arrival - last_arrival) * 1000)
            last_arrival = arrival
            if age_ms is not None:
                ages.append(age_ms)
                negative_ages += int(age_ms < 0)
                if last_timestamp is not None:
                    timestamp_gaps.append((timestamp_us - last_timestamp) / 1000)
                last_timestamp = timestamp_us
            if last_sequence is not None:
                delta = (sequence - last_sequence) & 0xFFFFFFFF
                if delta == 0:
                    sequence_duplicates += 1
                elif delta >= 0x80000000:
                    sequence_resets += 1
                elif delta > 1:
                    sequence_gaps += 1
                    sequence_missing += delta - 1
            last_sequence = sequence
            if frames + empty_frames + invalid_frames >= MAX_FRAMES:
                end_reason = "frame_limit"
                break
        else:
            end_reason = "server_closed"
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            end_reason = "rpc_error"
            errors.append(rpc_error(grpc, exc, token))
    finally:
        call.cancel()
    wall_elapsed = time.monotonic() - start
    observed = min(duration, wall_elapsed)
    return {
        "requested_duration_seconds": duration,
        "observation_seconds": round(observed, 6),
        "wall_seconds": round(wall_elapsed, 6), "end_reason": end_reason,
        "frames": frames, "capture_fps": round(frames / observed, 3) if observed else None,
        "first_frame_ms": first_frame_ms, "sizes": [list(size) for size in sorted(dimensions)],
        "pixel_bytes": total_bytes,
        "pixel_megabytes_per_second": round(total_bytes / observed / 1e6, 3) if observed else None,
        "empty_frames": empty_frames, "invalid_rgba_frames": invalid_frames,
        "timestamp_age_ms": {"p50": percentile(ages, .5), "p95": percentile(ages, .95),
                             "max": round(max(ages), 3) if ages else None,
                             "samples": len(ages), "negative_samples": negative_ages},
        "arrival_gap_ms": {"p50": percentile(arrival_gaps, .5),
                           "p95": percentile(arrival_gaps, .95),
                           "max": round(max(arrival_gaps), 3) if arrival_gaps else None},
        "source_timestamp_gap_ms": {"p50": percentile(timestamp_gaps, .5),
                                    "p95": percentile(timestamp_gaps, .95)},
        "sequence": {"first": first_sequence, "last": last_sequence,
                     "gap_events": sequence_gaps, "missing_numbers": sequence_missing,
                     "duplicates": sequence_duplicates, "resets": sequence_resets},
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--discovery", required=True, type=pathlib.Path)
    parser.add_argument("--target", help="optional localhost endpoint; must match discovery")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--adb", type=pathlib.Path,
                        default=pathlib.Path.home() / "Library/Android/sdk/platform-tools/adb")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or not 3 <= args.duration <= 20:
        parser.error("duration must be 3-20 seconds")
    if not re.fullmatch(r"emulator-[0-9]+", args.serial):
        parser.error("serial must identify a local Android emulator")
    report = {
        "schema": 1, "probe": "emulator_grpc_raw_capture",
        "scope": "localhost capture only; no encoder, WAN, phone decoder, or touch measurement",
        "scene": SCENE, "scene_target_fps": 30,
        "requested_size": [REQUEST_WIDTH, REQUEST_HEIGHT], "format": "RGBA8888",
        "pixel_persistence": False,
        "timestamp_age_note": "host wall-clock minus emulator estimated production timestamp; not end-to-end latency",
        "sequence_note": "gaps are emulator screenshot sequence gaps, not measured Internet packet loss",
    }
    token = ""
    channel = None
    try:
        properties = read_discovery(args.discovery)
        target = target_from_discovery(properties, args.target)
        token = properties.get("grpc.token", "")
        if any(ord(char) < 32 or ord(char) > 126 for char in token):
            raise ValueError("discovery contains invalid gRPC token characters")
        grpc = importlib.import_module("grpc")
        proto = importlib.import_module("emulator_controller_pb2")
        proto_grpc = importlib.import_module("emulator_controller_pb2_grpc")
        report["target"] = target
        report["authorization"] = "discovery_bearer" if token else "local_endpoint_without_token"
        if not synthetic_foreground(args.adb, args.serial):
            raise RuntimeError("synthetic DiagnosticSourceActivity must already be the focused window")
        report["synthetic_foreground_before"] = True
        metadata = (("authorization", "Bearer " + token),) if token else ()
        channel = grpc.insecure_channel(target, options=[("grpc.max_receive_message_length", 64 * 1024 * 1024)])
        grpc.channel_ready_future(channel).result(timeout=RPC_TIMEOUT)
        report["reflection"] = reflection_services(grpc, channel, metadata, token)
        stub = proto_grpc.EmulatorControllerStub(channel)
        request = proto.ImageFormat(format=proto.ImageFormat.RGBA8888,
                                    width=REQUEST_WIDTH, height=REQUEST_HEIGHT, display=0)
        report["screenshot"] = screenshot_probe(stub, request, metadata)
        report["stream"] = stream_probe(grpc, stub, request, metadata, args.duration, token)
        report["synthetic_foreground_after"] = synthetic_foreground(args.adb, args.serial)
        report["ok"] = bool(
            report["screenshot"]["rgba_bytes_match_dimensions"]
            and report["stream"]["frames"] > 0
            and not report["stream"]["errors"]
            and not report["stream"]["invalid_rgba_frames"]
            and report["stream"]["end_reason"] == "deadline"
            and report["stream"]["observation_seconds"] >= args.duration * .95
            and report["synthetic_foreground_after"]
        )
    except ImportError:
        report["ok"] = False
        report["error"] = {"code": "DEPENDENCY_MISSING",
                           "details": "grpcio or generated emulator_controller proto modules unavailable"}
    except Exception as exc:
        report["ok"] = False
        if "grpc" in locals() and isinstance(exc, grpc.RpcError):
            report["error"] = rpc_error(grpc, exc, token)
        elif isinstance(exc, (ValueError, RuntimeError)):
            # All such exceptions above use fixed messages and never include file
            # contents, credentials, or untrusted discovery values.
            details = str(exc).replace(token, "[redacted]") if token else str(exc)
            report["error"] = {"code": type(exc).__name__, "details": details[:240]}
        else:
            report["error"] = {"code": type(exc).__name__, "details": "local preflight or capture failed"}
    finally:
        if channel is not None:
            channel.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
