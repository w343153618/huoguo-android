# Playback metric definitions and source review — 2026-10-01

This change adds observations. It does not change the requested 30/60/120 FPS,
the user's saved settings, native touch handling, or the 80 ms recommended
buffer limit. JVM checks are source-level validation, not a phone performance result.

## Why the old audio queue number needs correction

`AudioTrack.getTimestamp()` reports a frame position together with the time
associated with that position. Subtracting that position directly from all
written frames includes the timestamp's age. It cannot be interpreted as the
PCM queue remaining **now**.

For example, at 48 kHz, 5,760 frames written and a timestamp for frame 0 from
100 ms ago give a raw difference of 120 ms. If playback continued at normal
speed, approximately 4,800 frames have since played, leaving an estimated
20 ms. The old reports near 130 ms need a timestamp-age observation before
deciding how much was real queueing. No earlier report is retroactively
declared to have 20 ms queueing.

The probe preserves `audio_queued_ms` for historical compatibility and adds:

- `audio_timestamp_age_ms`: signed timestamp age; a committed hardware
  presentation timestamp may be slightly in the future.
- `audio_queued_estimate_ms`: normal-speed projection, clipped to written PCM.
- `audio_queue_estimate_valid`: false for missing timestamp, impossible frame
  position, or timestamp/observation more than 500 ms away.
- Underrun count, allocated buffer size/capacity, and requested performance mode.

Projection stops when the track is paused. Clipping at all written PCM means
the estimate has no active PCM at that position; it does not invent audio past
the end of the available samples. Missing/stale timestamps do not produce
fabricated A/V estimates.

## Surface scheduling and audio/video comparison

The old `render_vs_current_deadline_ms` used a deadline recalculated when the
callback ran. `PlaybackClock` may have changed its anchor or extra hold since
the frame was submitted. This value cannot identify the actual scheduling
error for that frame.

`MediaPresentationMetrics` freezes the target immediately before
`releaseOutputBuffer`, records decoder-ready and receive timestamps, and
matches the codec's **unverified vendor callback timestamp** by source PTS. The Java callback's
receipt time is separate because callbacks can be delayed and batched.

The input submission call is also recorded. Receipt to input submission
includes waiting for an available input buffer. Input submission to output
dequeue includes codec work and thread scheduling; it is not a measurement of
hardware execution alone. App receipt means `readFully` completed, not kernel
packet arrival, so decoder backpressure must not be mislabeled as network
jitter.

Successful PCM writes also record their AudioTrack frame ranges and source
PTS, including partial-write offsets. This permits an estimated PCM source
PTS at a video frame's reported time, only if that time passes causal checks
and does not echo the requested target. `audio_minus_video_pts_estimate_ms`
is positive when estimated audio is ahead. It is a timestamp estimate; it does
not measure physical lip sync, acoustic routing delay, screen scanout, or
network one-way latency.

## Real-phone vendor timestamp failure found in this iteration

The 45-second NPS real-video report `phone-nps60-buffer80.json` contained 2,002
codec callbacks. Every reported `surface_ns` exactly equals the requested
`scheduled_ns`. Of these, 1,386 reported a time **after** Java had already
received the callback; 602 reported a time **before** the application called
release. A total of 1,988 fail causal ordering. The remaining 14 are still part
of an all-target-echo series; they cannot certify independently observed display
presentation either.

This invalidates conclusions drawn from the apparent zero scheduling error or
the old -111 ms A/V estimate. The source report and its raw observations are
preserved unchanged. An independently derived strict summary is stored in
`docs/evidence/native-iteration-20261001/phone-nps60-buffer80-strict-review.json`.

Strict reports retain all raw vendor timestamps and flags, count future,
before-release and exact-target-echo observations, and separate callback counts
from actual display FPS. The summary places invalid vendor-derived timing and
A/V numbers under `vendor_callback_observations`; it does not publish them as
actual presentation measurements. SurfaceFlinger actual-present fence evidence
must be collected separately. Unknown/zero/sentinel fence timestamps must not be
treated as presentation.

The independent System.nanoTime observations remain useful: App receipt to
input submission averages 0.249 ms, whereas input submission to output dequeue
averages 172.533 ms. That interval includes decoder queues, output-buffer
availability and thread scheduling, not just chip execution. Requested release
targets lead the release call by 40.771 ms on average and up to 233.593 ms.
Holding future buffers in the Surface/consumer pipeline is therefore a plausible
source of decoder backpressure. A scheduled-release versus immediate-release
experiment with all other settings equal is required before attributing the
172 ms to that cause. Clock re-anchoring may move subsequent deadlines and
amplify this interaction; it has not been established as the sole cause.

The audio observation also limits an earlier hypothesis: this report has a
2.421 ms timestamp age, so corrected queue remains 132.102 ms, close to the
legacy 134.524 ms. Allocated/current AudioTrack buffer is 5,766 frames, or
120.125 ms at 48 kHz. Underruns increased from 0 to 9. A shorter audio buffer
must therefore be tested for both queue reduction and underruns, especially
over the public relay; low-latency performance mode alone did not produce a
small queue in this case.

The isolated probe now accepts `--video-release scheduled|immediate` and
`--audio-buffer-frames 0|2048|3072|4096`. Defaults preserve existing behavior,
settings are restored afterwards, and requested and observed audio sizes are
recorded. These switches are experimental, not new user-facing defaults.

Each AudioTrack has an independent timeline. `resetAudio()` returns an epoch
token; old decoder threads' observations are ignored after reset. Video
history is preserved across an audio restart. AudioTrack's wrapping 32-bit
position is unwrapped within that timeline.

Records are bounded: 8,192 rendered video samples, 4,096 pending frames, and
4,096 PCM segments. Reports include eviction counts and unmatched callbacks.
On Android versions older than Android 14, platform callbacks may omit some
rendered frames; they cannot certify a physical-display FPS count.

## Source checks

The standalone JVM probe checks timestamp-age correction, paused/stale/future
observations, unchanged scheduling targets, source PTS mapping, delayed
callbacks, timestamp invalidation, 32-bit wrap and audio restart isolation.

```sh
mkdir -p /private/tmp/huoguo-playback-review-classes
javac -d /private/tmp/huoguo-playback-review-classes \
  app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java \
  tests/java/local/remoteandroid/direct/MediaPresentationMetricsProbe.java
java -cp /private/tmp/huoguo-playback-review-classes \
  local.remoteandroid.direct.MediaPresentationMetricsProbe
```

Result: `MediaPresentationMetricsProbe PASS`. The helper and PhoneProbe also
compile with the Android 37 SDK classpath. A real-phone run is still required
to measure the corrected queue, scheduling and audio/video values.

Primary API references:

- [AudioTimestamp position and time](https://developer.android.com/reference/android/media/AudioTimestamp)
- [AudioTrack timestamp and buffer APIs](https://developer.android.com/reference/android/media/AudioTrack)
- [MediaCodec rendered-frame callback and its limitations](https://developer.android.com/reference/android/media/MediaCodec.OnFrameRenderedListener)

## Foreground diagnostic report contract

`DiagnosticRunner` now measures Java `OnFrameRenderedListener` **receipt time**
with `System.nanoTime()`. It does not feed unverified vendor timestamps, which
can echo future release targets, into callback cadence or client pipeline
statistics. The UI labels the number “解码回调 FPS”. No count in this report is
an independent physical-display FPS measurement, and callback gaps are not a
direct optical-stutter or tearing observation. Its uniform synthetic scene
can screen candidate settings; it does not establish real-video acceptance.

Schema 1 retains the historical `rendered_frames`, `rendered_fps`,
`render_gap_count`, `render_interval_jitter_ms`, `last_render_ago_ms` and
`client_pipeline_*` keys. New reports add the explicit `codec_callback_frames`
and `codec_callback_fps` aliases, `codec_timing_basis=java_codec_callback_receipt`,
`vendor_timestamp_status=not_used_for_diagnostic_timing`,
`actual_display_fps_measured=false` and `actual_audio_video_skew_measured=false`.
Here `client_pipeline_*` ends at Java callback receipt and includes callback
delivery delay; it does not reach physical display presentation. Older reports
without the basis fields remain historical vendor-timestamp observations and
must not silently be interpreted as newly corrected receipt-time measurements.

The gateway's strict schema validator accepts these optional bounded fields and
rejects unknown timing bases or physical-display/A/V claims. The matching
`diagnostics_reports.py` must be deployed before a newly built client sends the
extended report; the previous live validator would reject the new fields.
Offline validation of the new contract preserves old report compatibility.

## Audio pre-submission arithmetic candidate

The current reviewed `drainAudio` source already estimates the queued PCM tail
before waiting to submit a decoded chunk. Waiting all the way to an audio media
deadline before writing would add the AudioTrack queue after the deadline;
pre-submission instead targets when that chunk's first sample should reach the
hardware timestamp timeline. The candidate does not change the shared playback
buffer or claim a new acoustic delay measurement.

`AudioSubmissionClock.timestampQueueTailNs(now, timestampNs, writtenFrames,
unwrappedTimestampFramePosition, sampleRate)` computes:

```text
tail = max(now, timestampNs + (writtenFrames - timestampFramePosition) / sampleRate)
```

The `max(now, ...)` prevents an empty or underrun queue's old timestamp from
making the caller wait beyond the intended deadline. A 50 ms old timestamp with
100 ms of subsequently written PCM predicts a tail 50 ms ahead of now; timestamp
age is already in that arithmetic and must not be added a second time. Invalid,
future or more than 500 ms old timestamps, inconsistent frame positions, invalid
rates and arithmetic overflow return `UNAVAILABLE`. Per-track positions must
already be unwrapped, and the caller must verify timestamp validity and active
playback. This helper has no scheduling or AudioTrack side effects.

When the helper returns `UNAVAILABLE`, the existing playback-head plus 25 ms
route allowance remains an **unverified fallback estimate**. AudioTrack buffer
capacity (for example, 5766 frames ≈120.125 ms at 48 kHz) is separate from its
estimated currently queued samples. Smaller requested buffers such as 3072 or
4096 frames require readback, corrected queue and underrun comparisons with the
same source/transport. They are experiments rather than deployed recommendations;
lower queueing accompanied by frequent underruns is not a pass. Audible A/V sync
still needs a controlled real-phone sound/picture observation.

The pure JVM `AudioSubmissionClockProbe` checks timestamp-age accounting,
underrun clamping, fractional durations, unwrapped positions and invalid/overflow
bounds. The candidate has not been integrated or run on a device by this review.
