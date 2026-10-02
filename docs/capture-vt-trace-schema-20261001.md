# Capture → VideoToolbox trace schema

Date: 2026-10-01. This is opt-in host instrumentation for isolated experiments, not a production service change or a performance result.

## Activation and safety

Build the experimental Swift source in `scripts/probes/emulator_hardware_encoder.swift`, then pass `capture_trace=Path(...)` together with `native_encoder=Path(...)` to `HostHardwareSession`. The worker accepts the equivalent `--capture-trace PATH` only when an explicit experimental encoder is selected. Existing deployed native binaries never receive the new flag by default. No media framing, payload, timestamp or keyframe bit is changed by tracing.

Two private JSONL files are created exclusively, with mode `0600` and `O_NOFOLLOW`:

- `PATH`: Python capture/queue/pipe metadata.
- `PATH.native.jsonl`: native raw-read/VideoToolbox/output metadata.

Use a restricted temporary directory outside tracked source; the runner should retain only validated, sanitized performance fields as evidence and remove private files afterward. Neither trace contains pixels, encoded media, credentials, audio data or device account contents. Both asynchronous writers cap pending records at 256, accepted records at 24,000, and output at 16 MiB per file. Saturation drops metadata instead of waiting for disk I/O in the media hot path. File creation errors fail explicit trace startup; runtime write failure does not block media. Traces are an observer and still require an enabled/disabled comparison before assuming zero timing effect.

`trace_summary` records `accepted_records`, `written_records`, `dropped_records`, `byte_capped`, `record_limit`, `byte_limit` and `clean_close`. A missing summary means abrupt termination or writer failure; missing records must not be interpreted as media drops. A native process terminated by the worker can leave a valid prefix without a summary. File bytes are bounded even in service mode; the trace does not grow forever.

## Clock and correlation

Every event has `schema=capture-vt-trace-v1`, `process=python|swift` and a fixed `event` name. All event fields ending in `_ns` use the same host's explicit `clock_gettime(CLOCK_MONOTONIC)` clock: Python uses `time.clock_gettime_ns(time.CLOCK_MONOTONIC)` and Swift uses Darwin `clock_gettime(CLOCK_MONOTONIC, ...)`. They are not device timestamps and are not assumed to equal DispatchTime's epoch. Nanoseconds may be subtracted across these two host processes only when they ran on the same host and clock-domain readback agrees.

`trace_clock` at start/end brackets a Unix-clock sample with `clock_before_ns` and `clock_after_ns`. It identifies `clock_domain=host_clock_gettime_CLOCK_MONOTONIC_ns` and permits estimated translation from emulator Unix screenshot timestamps. Bracket width is observation uncertainty; Swift records an additional conservative `wall_clock_precision_ns=1024` because Foundation wall-clock sampling uses floating point. Compare start/end offset to detect clock steps or drift. Do not use Unix-to-monotonic translation as exact guest timing.

`source_pts_us` is emulator-estimated screenshot-generation Unix microseconds, **not** video-file MediaCodec PTS, guest SurfaceFlinger time, or packetizer entry time. Submitted screenshot PTS must increase strictly. Python `capture_seq` identifies valid captured RGBA images; join `capture_enqueue` → `raw_submit` by `(capture_seq, source_pts_us)`. Join Python submission → native read/VT/output by the strictly increasing submitted `source_pts_us`. Native `native_input_seq` orders input frames but differs from capture sequence when pending raw frames are replaced. Neither sequence is a UDP frame id. Join packetizer events through their preserved source PTS before calculating downstream relationships.

`capture_seq=0` and `idle_repeat=true` identify an intentional repeat with a newly generated host timestamp. It has no fresh capture event and must be excluded from source cadence measurements. A raw frame with nonincreasing PTS gets `raw_drop` and never becomes a native input. Capture event file order need not be timeline order: collector and pipe threads emit independently, so sort by explicit timestamps and join keys, not adjacent JSONL lines.

New source investigations also record `screenshot_seq`, the gRPC `Image.seq`
field, independently from local `capture_seq`. Consecutive screenshot sequence
numbers may skip when the emulator screenshot stream omits images; this is not
proof of Internet packet loss or missing decoded video frames. The analysis
retains a modulo-2^32 delta for valid uint32 sequence values. Older traces that
lack this field remain usable and explicitly have unknown screenshot deltas.

## Events and meaningful phase differences

| Event | Principal fields | Meaning |
| --- | --- | --- |
| Python `capture_enqueue` | `capture_seq`, `source_pts_us`, `grpc_return_ns`, `enqueue_ns`, dimensions, `raw_bytes`, `pending_count`, `replaced_capture_seq` | Complete gRPC message delivered to the collector; enqueue includes local validation/condition-lock time. This does not expose upstream guest decode or compositor internals. |
| Python `raw_submit` | `dequeue_ns`, `pipe_write_begin_ns`, `pipe_write_end_ns`, `raw_bytes`, `skipped_frames`, `idle_repeat` | Complete raw image selected and written/flushed into the native stdin pipe. Queue time is enqueue → dequeue; budget/dispatch overhead after selection is dequeue → pipe begin. |
| Python `raw_drop` | `dequeue_ns`, `reason=nonincreasing_source_pts` | PTS admission rejection before encoding. |
| Swift `raw_read` | `native_input_seq`, `header_read_begin_ns`, `header_read_complete_ns`, `raw_read_begin_ns`, `raw_read_complete_ns`, dimensions/size | Header begin may wait for the next frame; it is not just pipe-copy cost. Raw complete means the entire RGBA payload is available in Swift. |
| Swift `vt_submit_return` | `vt_submit_return_ns`, `vt_submit_status` | Return of the encode API call. It may be later than an asynchronous callback; do not treat event-file order as call order. |
| Swift `vt_frame` | raw-read fields plus `slot_wait_begin_ns`, `slot_wait_end_ns`, `pixel_conversion_begin_ns`, `pixel_conversion_end_ns`, `vt_submit_ns`, `vt_callback_ns`, `vt_status`, `frame_dropped` | Separate semaphore wait, pool/allocation/channel conversion, encode request and callback. Failed/dropped callbacks may have no output fields. |
| Swift successful `vt_frame` output | `stdout_lock_wait_begin_ns`, `stdout_lock_acquired_ns`, `stdout_write_begin_ns`, `stdout_write_end_ns`, `au_bytes`, `keyframe` | Callback → output-lock-wait begin includes Annex B/config preparation; lock wait is separate. AU stdout timestamps exclude a preceding changed codec configuration write. |
| Python `encoded_egress` | `encoded_read_complete_ns`, `socket_write_end_ns`, `au_bytes` | Available only when the existing SPS-low-delay relay is active. Otherwise native writes directly to the socket and this Python stage is absent. |

For a source gap, inspect consecutive valid screenshot generation PTS and gRPC return time first, then join each selected frame through raw-pipe completion, Swift raw-read completion, slot wait, conversion, VT callback and stdout completion. Initial encoder creation and a resize can delay raw-read → first slot; separate startup/resize from steady state. A phase gap narrows location, but one correlated gap does not prove causal dominance. Source SurfaceFlinger still requires its own guest-to-host clock calibration. These records do not measure phone decode, physical presentation, WAN latency, acoustic audio/video synchronization or touch-to-photon latency.

## Source-level verification

`python3 -m unittest tests.test_capture_trace tests.test_hardware_stream tests.test_hardware_gateway` passes 22 checks, covering private exclusive files, symlink refusal, record/byte bounds, writer saturation, metadata-only validation, explicit clock samples and existing framing behavior. `xcrun swiftc -typecheck scripts/probes/emulator_hardware_encoder.swift -module-cache-path /private/tmp/huoguo-capture-trace-module-cache` passes. These are source-level checks; no live device, VM, service or streaming experiment was performed while adding this instrumentation.
