# Real-video capture-only isolation

`measure_real_capture_only.py` reads the already playing, foreground Morphe
YouTube instance on the local M1 AVD. It refuses a concurrent hardware worker;
it never launches a player, changes display/VM settings, reads user accounts,
starts VT, starts guest audio capture, or transmits media to a phone. Pixels are
discarded immediately after extracting bounded numerical metadata. Discovery
bearer tokens remain in process memory and never appear in reports/errors.

The request matches `hardware_stream.py`: `ImageFormat.RGBA8888`, display 0 and
`width=height=min(max-size, physical-longest-side)`. A 720×1280 guest with
`--max-size 960` normally returns 540×960. `--fps 60` labels the comparison
target only: the emulator's `streamScreenshot` API has no corresponding FPS
limit, and this does not establish the video's content-file frame rate.

Run using the **existing external hardware venv** so the pinned gRPC modules
are available. The generated protobuf modules are read from the selected
runtime's `hardware/proto/`. The helper discovers the single matching AVD's
existing `pid_*.ini`; `--discovery` can select one exact file without copying it.

```sh
"$HOME/Documents/ChatGPT/others/android-remote/m1-compare/hardware/venv/bin/python" \
  scripts/probes/measure_real_capture_only.py \
  --runtime "$HOME/Documents/ChatGPT/others/android-remote/m1-compare" \
  --serial emulator-5556 --avd RemoteAndroid17Compare \
  --seconds 35 --max-size 960 --fps 60 \
  --output docs/evidence/<new-experiment>/capture-only-01.json
```

Choose a fresh output path: existing evidence is not overwritten. In a second
process, collect the source layer using the same SF sampler as a full pipeline:

```sh
python3 scripts/probes/measure_surface_cadence.py \
  --serial emulator-5556 --package app.morphe.android.youtube \
  --seconds 32 --wait-layer 3 \
  --output docs/evidence/<new-experiment>/capture-only-01-source-surface.json
```

Read returned screenshot `seq`, production-estimate `source_pts_us`, host gRPC
return monotonic/wall clocks, dimensions and numerical summaries. Use timestamps
to compare a common steady interval; startup frame counts are not steady FPS.
Source screenshot PTS is not media decoder/file PTS. Missing screenshot sequence
numbers are not Internet loss. Wall-clock screenshot age is not end-to-end
latency. SF latency headers alone are not panel refresh proof.

Compare bounded repeated, interleaved runs against real-video full UDP capture
using the same public video segment, capture cap and SF polling. Capture-only
removes VT **and** audio/transport/decoder workload; an improvement localizes
downstream load/backpressure as a candidate but does not prove VT alone caused
the original gap. A source may change playback quality or stall mid-run, so the
before/after numeric media-session gates and SF cadence must both be retained.

Offline checks exercise metadata validation and separate the clock domains:

```sh
python3 -m unittest discover -s tests -p test_real_capture_only.py
```
