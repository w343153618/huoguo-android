# Isolated VideoToolbox low-latency experiment

`emulator_hardware_encoder.swift` reads complete RGBA frames from stdin and writes
the existing H.264 framing to stdout. It never opens a capture or emulator session
on its own. JSON configuration observations and timing summaries go to stderr.

The optional `--low-latency-mode true|false` defaults to `false`. With `false`, the
encoder specification and Baseline profile remain unchanged. With `true`, the
encoder specification contains both
`kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder` and
`kVTVideoEncoderSpecification_EnableLowLatencyRateControl`. The experiment also
requests `kVTProfileLevel_H264_ConstrainedHigh_AutoLevel`, because the current SDK
describes the dedicated low-latency mode as requiring High profiles. This profile
selection requires macOS 12 or later; older systems fail explicitly.

`RealTime`, disabled frame reordering, `ExpectedFrameRate`, the requested FPS,
existing CBR/VBR logic and runtime bitrate-update logic stay in place. Creation,
required-property, preparation and CBR-setter errors remain fatal. The executable
does not retry without low-latency mode, select a software encoder, switch bitrate
mode, or lower FPS after a failure. The existing optional `MaxFrameDelayCount` and
VBR burst-limit observations retain their previous behavior and report status.

## Build without changing a deployed executable

From the project root:

```sh
xcrun swiftc -O -warnings-as-errors \
  -module-cache-path /private/tmp/huoguo-vt-lowlatency-module-cache \
  scripts/probes/emulator_hardware_encoder.swift \
  -o /private/tmp/huoguo-vt-lowlatency
```

The external runtime remains
`~/Documents/ChatGPT/others/android-remote/m1-compare`. Do not copy the experiment
over its `hardware/macos-h264`. A caller may explicitly select
`/private/tmp/huoguo-vt-lowlatency` for one private session and add
`--low-latency-mode true`. Host-session plumbing is outside this Swift file.

## Read configuration evidence

Ready events and final timing summaries include:

- `low_latency_mode_requested`: whether the dedicated mode was requested.
- `low_latency_mode_read_status` / `low_latency_mode_readback`: the exact result
  from querying the creation-specification key as a session property. Some
  implementations may not expose this key for readback; an error is retained with
  `null`, rather than claiming a confirmed value. A readable `false` contradicting
  a requested `true` fails explicitly.
- `profile_level_requested`, `profile_level_read_status` /
  `profile_level_readback`: requested and observed profile.
- `frame_reordering_read_status` / `frame_reordering_readback`, and
  `expected_frame_rate_read_status` / `expected_frame_rate_readback`.
- `hardware_property_read_status` / `hardware_property_readback`: retain the
  actual hardware property result. The normal path requires a readable true.
- `hardware_verification_method`: normally `session_property`. Only for the
  dedicated mode, if that property is explicitly unsupported, the independent
  experiment permits `required_hardware_and_selected_encoder_registry`: creation
  with RequireHardware must succeed and the exact selected EncoderID must match
  an Apple registry entry whose hardware flag is true. A missing registry entry,
  missing hardware flag, readable false or other error still fails. This is a
  distinct evidence path, not a fabricated successful property readback.
- `using_hardware`: the selected path's mandatory verification passed at both
  startup and finish. Read the method and original property observations with it.
  `encoder_id` identifies the selected encoder when available.

The SDK's creation-specification contract requires selecting a supporting encoder
and enables the requested mode. A successful creation and a successful property
readback are distinct observations. Neither replaces checking real SPS/slice
ordering, encoded cadence, phone queue-to-output timing and actual presentation.
The current SDK also describes temporal layers and an infinite-GOP preference for
the dedicated mode, so preserve and inspect actual keyframe counts and parameter
sets. Existing keyframe properties are still requested; setter rejection fails.

For a useful A/B, keep the verified real-video rendition, source display, emulator
resources, capture policy, bitrate, requested FPS, phone decoder settings and
output-release policy fixed. Stop the other hardware session before starting an
experiment. Do not combine an encoder-mode change with a phone-release change in
the same comparison. This flag and a successful compilation alone do not prove a
latency or smoothness improvement.

The Apple SDK documents the creation key in
`VideoToolbox.framework/Headers/VTCompressionProperties.h`. Public explanations:
[Apple low-latency encoding talk](https://developer.apple.com/videos/play/wwdc2021/10158/)
and [low-latency conferencing documentation](https://developer.apple.com/documentation/videotoolbox/encoding-video-for-low-latency-conferencing).

## Independent source pixel pool experiment

`--pixel-pool manual|session` defaults to `manual`, which keeps the separately
created BGRA IOSurface pool. `session` uses
`VTCompressionSessionGetPixelBufferPool(session)` after encoder preparation.
This changes the pool origin only: input remains complete RGBA, conversion remains
the same vImage RGBA-to-BGRA permutation, and the three in-flight slots, encoder
properties, timestamps and output protocol remain the same.

Before emitting `ready`, both modes verify the pool's pixel-format, width and
height attributes and allocate one unsubmitted buffer to read its actual packed
BGRA format, geometry and row stride. Missing attributes, a missing session pool,
or mismatched attributes/buffers fail the experiment; session mode never falls
back to the manual pool or another pixel format. Every submitted buffer is checked
again before vImage writes to it. `ready` and the final summary carry
`pixel_pool_mode`, `pixel_pool_attributes_verified`,
`pixel_pool_pixel_format_readback`, `pixel_pool_width_readback`,
`pixel_pool_height_readback`, `pixel_pool_buffer_verified`, and the
`pixel_pool_probe_pixel_format`, `pixel_pool_probe_width`,
`pixel_pool_probe_height`, `pixel_pool_probe_bytes_per_row` observations.

Choose the flag only at an independent experimental encoder entry point. Existing
host-wrapper arguments and deployed runtime defaults are unchanged. Compare the
two modes at the same resolution, FPS, bitrate, source rendition, raw queue policy
and phone settings. A lower submit-to-callback time supports a pool-origin effect
only if repeated steady-window comparisons also improve complete-RGBA-to-callback
or captured-frame-to-egress time without a cadence regression. Pool selection and
successful format checks do not establish that VideoToolbox avoids an internal
copy or color conversion.

## Independent burst-window experiment

The optional pair `--burst-bytes 100000 --burst-seconds 0.08` adds a second
`DataRateLimits` window while preserving the original one-second limit. Defaults
remain omitted. The pair is allowed only with VBR, with 32768–2000000 bytes and
0.02–1.0 finite seconds. The host wrapper accepts it only when a caller selects
an explicit experimental encoder binary, leaving the deployed executable alone.

For a 4 Mbps target, this requests `[750000, 1.0, 100000, 0.08]`. Initial `ready`
and dynamic `bitrate` events retain setter status, requested values and actual
`VTSessionCopyProperty` readback. A target update retains the configured short
window. These are compressed-data constraints; they do not include Reed-Solomon
parity, packet headers, AES-GCM, UDP/IP or a Tailnet outer tunnel.

Apple describes this property as hard limits over decode-time windows, while
also documenting timing and encoder-support requirements. Setting and reading
it back is not enough to prove the emitted stream honors the requested window.
In the retained real M1 experiments, a 64000-byte/80ms request was accepted but
the maximum emitted AU was over 100000 bytes. The reason for that discrepancy
has not been established. Do not rename the API a soft average limiter or treat
it as a proven single-IDR cap. Keep actual AU/wire-byte measurements and whole
frame admission as independent checks.

See [current real-video iterations](../../docs/udp-burst-and-ingress-results-20261001.md)
and [Apple DataRateLimits documentation](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_dataratelimits).
