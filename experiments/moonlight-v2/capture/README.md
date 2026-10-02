# Window capture timing experiment

This is an independent M1 experiment. It does not change the emulator, gateway,
NPC/NPS, primary display, resolution, process ownership, or user accounts. Pixels
and compressed H.264 are discarded. It captures only an explicitly selected
emulator/scrcpy window; it cannot capture a desktop or an arbitrary application.

## Build and run

From the project root:

```sh
xcrun swiftc -parse-as-library -O -warnings-as-errors \
  -module-cache-path /private/tmp/huoguo-sck-module-cache \
  -framework AppKit -framework ScreenCaptureKit -framework CoreMedia \
  -framework CoreVideo -framework VideoToolbox -framework IOSurface \
  experiments/moonlight-v2/capture/window_capture_probe.swift \
  -o /private/tmp/huoguo-window-capture-probe

/private/tmp/huoguo-window-capture-probe --list
```

Screen-recording access must already be available to the executing application.
The probe never requests or modifies TCC access. A sandbox can make
`CGPreflightScreenCaptureAccess()` return false even when an authorized run outside
that sandbox succeeds; therefore `screen_recording_preflight_false` is a boundary
observation, not proof that the user's macOS permission is missing.

Capture requires both a listed window ID and owner PID. The owner must be an
emulator/scrcpy executable, verified through its application name or
`proc_pidpath()` basename. A scrcpy process launched from Terminal can appear to
ScreenCaptureKit as application name `Terminal`; the executable validation avoids
misidentifying it as the Terminal application itself. The PID is still mandatory.
Review the actual listed window rather than selecting any auxiliary/menu window.

For a **freshly verified** 540×1232-point scrcpy window whose 32-point title bar
leaves a 540×1200 Android area, the invocation is:

```sh
/private/tmp/huoguo-window-capture-probe \
  --window-id WINDOW_ID --owner-pid SCRCPY_PID \
  --crop 0,32,540,1200 --width 540 --fps 60 \
  --duration 10 --warmup 2 --encode --fingerprint
```

Do not reuse recorded window IDs after a restart. `--crop` is in points relative
to the selected window, constrained to that window. The probe bounds measurement
to 3–30 seconds plus 0–5 seconds warmup, output width to 160–1440 even pixels and
output height to 2–4096 pixels. It never crops from the desktop. A crop should be
validated with known diagnostic patterns before using its geometry in a product.

`--encode` requires and reads back the actual Apple hardware H.264 encoder. The
captured IOSurface-backed `CVPixelBuffer` is passed directly to VideoToolbox,
without an application RGBA pipe or application pixel copy. This does **not**
prove that WindowServer and the hardware encoder perform no internal conversion
or copies. The three-frame in-flight bound drops capture submissions rather than
blocking indefinitely when the encoder is overloaded.

`--fingerprint` locks the pixel buffer for a CPU read and hashes a 64×128 grid of
RGB pixels. Its cost is reported separately. Equal sampled hashes mean the grid
matched; they can miss changes outside the grid, and are not proof that every
pixel is identical. No hash value or pixel is emitted. Compare runs with and
without this optional readback before judging the fastest capture path.

## Initial local validation, 2026-10-01

`initial-scrcpy-window-timing.json` records a six-second observation of the existing
scrcpy preview window, with 60 FPS requested, width 540, 1 second warmup, and both
hardware encoding and sampled hashing enabled. The Android content and its media
frame rate were not independently confirmed in this sample. No Android action was
performed by this experiment.

| Observation | Result |
| --- | ---: |
| Complete ScreenCaptureKit frames | 341 / 6 s = 56.83 FPS |
| IOSurface-backed complete frames | 341 |
| Hardware H.264 output frames | 341 |
| Sampled image transitions | 0 changed, 340 repeated |
| Repeated/nonmonotonic PTS transitions | 0 / 0 |
| WindowServer display time → SCK callback, p95 | 4.887 ms |
| VT submit → compression callback, p95 | 10.977 ms |
| SCK callback → compressed output, p95 | 11.262 ms |
| Optional grid readback/hash, p95 | 0.133 ms |
| VT dropped/failed/backpressure drops | 0 / 0 / 0 |

This is a useful counterexample: **complete frames and increasing PTS can report
almost 60 FPS while sampled content stays unchanged**. It is not evidence of a
60 FPS video source, improved phone playback, or improved WAN performance.

The current qemu PID 760 had no window in the shareable-content inventory. The
existing scrcpy preview PID 11170 had the selected Android window. Capturing that
preview adds a guest video encoder, local transport, scrcpy decoder, window
presentation, and host recapture before the new VideoToolbox encoder. It therefore
adds a second encoding step, and is **not** the recommended default performance
architecture. It is useful for checking the window/IOSurface/VT part independently.

The JSON's `observation_start_unix_ms` is the clock origin immediately after
ScreenCaptureKit starts. Warmup begins there. `displayTime` is converted from mach
absolute ticks; the SDK describes it as the time WindowServer displayed the frame.
It is not the time Android produced the video image. Callback and PTS gap
distributions are both retained because delivery jitter can differ from source
timestamp spacing.

## Existing evidence and the next discriminating measurements

The current real YouTube evidence contains approximately 25–26 raw FPS and similar
encoded/phone receive rates. The BBB title says 60 FPS, but that run used automatic
360p and did not establish the frame rate of its selected rendition. Therefore it
cannot establish that half of the frames were lost in capture.

The historical M1 Android 17 controlled 60 FPS source at 720×1280 produced 594 raw
frames and 594 encoded frames in ten seconds, with 594 unique full-image hashes.
That different configuration proves the M1 gRPC/VideoToolbox design is not
inherently limited to 25–30 FPS; it is not a same-configuration comparison with the
current real-video test. See
[controlled M1 Android 17 evidence](../../../docs/evidence/m1-android16-comparison-20260930/android17-60-hardware.json)
and [real YouTube evidence](../../../docs/evidence/gemini-review-20260930/README.md).

Use two separate sources: a numbered 60 FPS diagnostic scene to audit the pipeline,
and an actual video whose selected rendition FPS and decoder are established.
Record source-to-output counts independently. SurfaceFlinger layer presentation
timestamps or a guest player callback can help confirm actual media output;
YouTube's title alone cannot. UI animation and video content can have different
frame rates in the same screen.

For per-frame attribution, retain one join key (`capture_seq` and original media
PTS) and bounded timing records at these stages:

1. Guest source frame number/presentation time, if observable. Distinguish media
   frames from UI composition. Calibrate guest monotonic time before cross-host
   comparisons; wall clocks can drift.
2. Emulator frame timestamp and sequence, local gRPC arrival monotonic time,
   payload byte count, full-image or optional sampled transition count. The SDK
   specifies `timestampUs` as estimated generation time before copy/transform.
3. Arrival → dequeue queue residence and oldest/newest queue age in
   `hardware_stream.py`; track drops separately from received raw frames.
4. Native pipe write start/end and native complete-payload receipt. This identifies
   Python/pipe blocking. The existing native `rawReadyNS` begins **after** receipt
   of the complete payload and cannot measure the earlier wait.
5. Pixel conversion start/end, VT submit/callback, H.264 write start/end. These
   already have aggregate distributions; join the same bounded per-frame IDs.
6. Gateway send, phone receive, decoder queue/output, Surface scheduled presentation
   and actual rendered callbacks. Use calibrated clock uncertainty and local
   durations, rather than subtracting unrelated host/phone uptime clocks.

Log queue residence and frame age distributions as well as FPS. A 60 FPS pipeline
can still carry old frames. The raw two-frame queue currently uses `popleft()`, so
it can retain one older frame; a latest-only dequeue is a suitable isolated A/B
experiment with explicit skipped-raw counters, not a reason to drop encoded
reference frames. Hold source, bitrate, requested FPS, network path, preview
presence and phone settings fixed when comparing that change.

## Headless capture alternatives and ownership requirements

The installed `emulator_controller.proto` already exposes `ImageTransport.MMAP`,
with an explicit warning that the mmap can result in tearing. MMAP avoids large
RGBA bytes inside protobuf, but supplies CPU-visible memory rather than an
IOSurface GPU texture and does not establish stable ownership while the producer
overwrites it. Existing project notes also record an Apple Silicon MMAP failure
report; its fix/version has not been revalidated in this experiment. See
[earlier capture analysis](../../../docs/display-540-hardware-encoding-20260929.md).
Do not enable MMAP for the live instance just to claim zero copy.

A genuinely direct headless GPU path requires a renderer/provider interface that
exports an IOSurface-backed frame with pixel format, stride, dimensions, sequence,
presentation timestamp and producer-completion synchronization. The consumer must
retain ownership until VideoToolbox finishes, and the producer must not overwrite
the surface in that interval. Rotation, resizing, display sleep and reconnect
must release/rebuild surface state safely. The installed screenshot API does not
provide such an IOSurface/fence interface. This would require a supported native
renderer hook or an emulator/gfxstream adaptation, followed by actual correctness
and timing tests.

Before such a renderer fork, a native gRPC consumer feeding an IOSurface pixel pool
can remove Python/protobuf object handling and the RGBA pipe while preserving
complete-frame safety. It still reads/copies pixels and must be described as a
shorter path, not GPU zero copy. At 540×1200×4, 60 FPS carries 155.52 MB/s locally;
120 FPS doubles that. Measure its queue/CPU/jitter benefit before replacing the
working host, because prior RGBA→BGRA conversion itself was already sub-millisecond.
