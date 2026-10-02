# M1 local pipeline stage measurement

`measure_pipeline_stages.py` samples an already running M1 Android Emulator through
the canonical `hardware_stream.HostHardwareSession`. It explicitly chooses
`fifo` or `latest` raw queue policy as the eighth constructor argument. It neither
edits the live gateway nor changes its default queue policy.

The probe opens only three private socketpairs: video, audio and control. It drains
all channels so one ignored channel cannot backpressure the worker. H.264/AAC data
is consumed and discarded; only frame timing, size, keyframe/NAL header types and
whitelisted runtime metrics are saved. It uses no Android-remote account and
creates no public listener or new credential.

## Runtime and sequencing

The default external runtime is:

`~/Documents/ChatGPT/others/android-remote/m1-compare`

The required existing files are `hardware/venv/bin/python`, `hardware/macos-h264`,
`hardware/scrcpy-audio-control` and the worker's already configured `hardware/proto`
modules. `HostHardwareSession` runs canonical `hardware_stream.py` using this
external venv; the top-level probe itself needs only Python's standard library.
Use `--runtime` or `DIRECT_STATE_DIR` to point at an explicit existing runtime.
Credential/discovery handling stays inside the established worker. External
`hardware/worker.log` is read only from the offset immediately before the probe;
unredacted log bytes are never copied into the source tree.

Target defaults are `emulator-5556`, `RemoteAndroid17Compare`, display 0 through the
existing worker, and max size 1200. The script does not launch a player, tap the
screen, wake/sleep Android, change virtual displays or alter resolution. The caller
must arrange the real-video source beforehand. When a secondary Android display
exists, use the correct explicit display for caller input (for example `input -d 0`);
this probe sends no input at all.

Stop phone streaming before starting. The probe refuses an existing
`hardware_stream.py` worker for the same emulator, preserving it rather than
disconnecting it. The user-owned scrcpy preview is preserved and its guest-server
count is recorded. Keep its presence identical in both experiments. The process
check and external runtime/socket access can require an authorized run outside a
restricted execution sandbox; that is unrelated to Android-root or remote-login
account permission.

## Run

From the project root, with the verified real-video source already playing:

```sh
python3 scripts/probes/measure_pipeline_stages.py \
  --fps 60 --bitrate 4000000 --mode VBR --duration 30 --warmup 2 \
  --raw-queue-policy fifo \
  --source-kind real-video \
  --source-label 'YouTube BBB 720p60; display0; independent SF cadence verified' \
  --verified-content-fps 60 \
  --output docs/evidence/gemini-review-20260930/host-bbb60-fifo.json

python3 scripts/probes/measure_pipeline_stages.py \
  --fps 60 --bitrate 4000000 --mode VBR --duration 30 --warmup 2 \
  --raw-queue-policy latest \
  --source-kind real-video \
  --source-label 'YouTube BBB 720p60; display0; independent SF cadence verified' \
  --verified-content-fps 60 \
  --output docs/evidence/gemini-review-20260930/host-bbb60-latest.json
```

`--verified-content-fps` is an explicitly caller-verified annotation. The probe
does not infer content FPS from requested FPS, packet FPS, increasing PTS or a
YouTube title. Source SurfaceFlinger presentation cadence is measured by the
separate `measure_surface_cadence.py` probe, not by another capture started here.

Measurement starts after the first H.264 media frame plus the requested warmup.
Duration is bounded to 5–120 seconds, warmup to 0–10 seconds, bitrate to 0.5–40 Mbps,
FPS caps to 30/60/120. Cleanup closes only this session and joins its readers.

Use repeated runs over the same video segment, verified rendition, emulator
resources, bitrate, preview state and requested FPS. Alternate A/B ordering where
possible. A later part of a movie can differ in decode/render complexity from an
earlier part, even at the same content FPS.

## Read the report

- `encoded_received_fps` is local compressed-video delivery, not phone display FPS.
- `output_source_pts_gap_ms` measures the emulator's estimated source timestamp
  cadence; `output_arrival_gap_ms` measures local H.264 delivery cadence.
- `estimated_emulator_timestamp_to_local_H264_receipt_ms` includes source copy,
  capture, queue, pipe, conversion/encoding and local socket receipt. The emulator
  timestamp is an estimate before copy/transform; this is not an external
  camera-measured latency and is not network RTT.
- `video_frames` preserves each measured frame's source PTS, local arrival time,
  encoded bytes, key flag and NAL types. Use `--frame-detail none` for a compact
  report. Pixels and payload are not retained in either mode.
- `worker_events` preserves numeric `pipeline_sample` and `phase_sample` only:
  capture timestamp age, raw callback gap, source PTS gap, raw queue residence and
  full RGBA pipe write time. Ready events include actual hardware readback.
- Windows that begin before steady measurement or end after it are marked
  `fully_inside_measurement: false`. The phase window start is inferred from its
  matching pipeline sample. Do not merge per-window p95s into a global p95 or
  subtract unrelated percentiles to invent per-frame encoder times.
- Native encoder finish distributions are included only if actually written to
  the new log section. Bounded session close can terminate the native process
  before its final summary; `native_finish_timing_summary_status` states whether
  that summary exists. Missing timings are never reported as zero.
- Drained AAC timestamp age does not prove playback A/V synchronization: there is
  no real audio device or phone display in this probe.

## Offline validation

```sh
python3 scripts/probes/measure_pipeline_stages.py --self-test
python3 -c 'import ast,pathlib; ast.parse(pathlib.Path("scripts/probes/measure_pipeline_stages.py").read_text())'
```

The self-test exercises fragmented framing, geometry/config/key flags, Annex B NAL
headers, quantiles and strict log whitelisting. It starts no hardware session,
uses no Android command and needs no account or network. No live measurement was
performed merely by creating and checking this script.
