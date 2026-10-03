# Bounded numeric Morphe source readback review

Status: read-only repository and upstream review on 2026-10-03. No ADB command,
phone operation, player mutation, media session, supervisor run, or installed
framework readback was performed for this review. This is a design for the next
small diagnostic read, not a fresh source-format result or implemented collector.

## Decision

Reuse the existing bounded MediaSession reader immediately for reported playback
state and position. Try one bounded codec-metrics observation for decoder format,
with explicit `unknown` when there is no reliable current-codec association.
Do not repeat the 180-second Stats UI preparation sequence, restart Morphe to
flush metrics, or turn a recent historical record into a live-format witness.

The latest failed preparation selected and read back a 1080p60 quality menu,
after first observing 720p60. Neither observation proved a fresh itag, MIME or
decoded dimensions. The earlier itag299/AVC observation remains historical; see
[the failed preparation receipt](host-trace-source-prep-deadline-20261003.md) and
[the earlier independent format evidence](source-BBB-format-readback-20261003.json).
Future readback may retain `itag_known=false`; a decoder MIME or quality menu
does not identify a YouTube rendition number.

## Existing repository capability

[source_playback_state.py](../scripts/probes/source_playback_state.py), lines
38–87, accepts the numeric state of exactly one active
`app.morphe.android.youtube` session beneath its anchored owner/package block.
It rejects ambiguous, malformed and foreign-session fields. Lines 90–140 keep
the raw dump in bounded memory, use a deadline, reap only the collector's own ADB
process, and emit fixed numeric fields and error codes. Existing tests cover
symbolic `PLAYING(3)`, numeric states, nested metadata, ambiguity and bounds.

Its `position_ms` is the position reported with that PlaybackState update;
`updated_elapsed_ms` is the Android elapsed-real-time update clock. Two reads
can return the same position while the player is still running. Preserve the
reported value and state rather than declaring playback frozen. An optional
projection needs a separately observed **guest** elapsedRealtime bracket, normal
speed and a suitable update age; it must be labeled an estimate and must not use
Mac MONOTONIC, host UPTIME or guest wall time. These fields describe player state,
not decoded-frame or physical presentation progress. [PlaybackState API](https://developer.android.com/reference/android/media/session/PlaybackState)

[source_player_quality.py](../scripts/probes/source_player_quality.py), lines
13–21 and 42–71, reads a quality-menu label and may tap the UI. Its existing
scope is `observed_player_quality_menu_not_decoded_frame_count`. It supplies
neither codec identity nor decoder MIME and should not be used for this new
read-only collector.

## What the framework can and cannot expose

The inspected AOSP MediaCodec source uses the metrics key `codec`. It records
MIME, dimensions, encoder flag and optional configured `frame-rate`; capture and
operating rates are separate. Render-tracker frame rates/counts are different
fields. `getMetrics()` addresses the calling codec instance: a separate helper
cannot query Morphe's live codec by creating its own instance. Metrics are
submitted on lifecycle/flush paths, and bitrate handling in the inspected branch
is encoder-specific. A missing decoder bitrate is unknown, not zero. [MediaCodec.cpp, inspected main blob 7b10d8a5500fdff5c1d4b26b4aaaea81d95a32fc](https://android.googlesource.com/platform/frameworks/av/+/refs/heads/main/media/libstagefright/MediaCodec.cpp)

The MediaMetrics service dumps a queue of submitted items. Its `--prefix` filter
selects component keys; `--since -15` selects records timestamped within the
preceding 15 seconds using REALTIME. Neither filter polls an active codec or
establishes its current rendition. `--clear` mutates the queue and must not be
used. [MediaMetricsService dump implementation](https://android.googlesource.com/platform/frameworks/av/+/9dbd8aa9a7/services/mediametrics/MediaMetricsService.cpp)

An item's textual header contains its component, formatted timestamp, package,
PID and UID. Treat schema/clock parsing as unknown until the installed build's
actual format is independently validated. Do not guess the formatted timestamp's
zone or precision. [MediaMetrics item formatter](https://android.googlesource.com/platform/frameworks/av/+/88e5f815b7001ee04a2d3357d9299787147d3d81/media/libmediametrics/MediaMetricsItem.cpp)

The inspected render-quality tracker derives content cadence from content-time
intervals, desired cadence from requested render times, and actual cadence from
render timestamps. Stable intervals are required for its detection; it is not a
container/itag parser or independent optical observer. Keep any returned rates
under these distinct names. [VideoRenderQualityTracker implementation](https://android.googlesource.com/platform/frameworks/av/+/44ac225821a24a7aa37711fda50974a6107d58d0/media/libstagefright/VideoRenderQualityTracker.cpp)

No read contract for the installed build's `dumpsys media.codec` has yet been
established. Its mere codec/component inventory cannot supply a per-Morphe live
format. An optional single read may determine whether this build exposes a
usable current-client association; absence is a fixed unknown result. Upstream
review is not proof of the installed Android17 image's dump schema.

| Observation | Accepted meaning | Must not become |
| --- | --- | --- |
| Quality menu `1080p60` | Current UI quality selection | itag299, live decoder dimensions or measured 60FPS |
| Decoder MIME `video/avc` | AVC decoder-format MIME in the identified record | Exact RFC6381 profile string or YouTube itag |
| Record width/height | Dimensions of that record's format, with provenance | Current output/crop dimensions without a live link |
| Configured frame-rate | Value supplied in that codec configuration | Actual frame arrival, render rate or unique content rate |
| Tracker content rate | Cadence estimate from content timestamps | Independently verified encoded-rendition FPS |
| Tracker actual rate/count | Framework render observation for its record interval | Optical FPS, exact phone SurfaceFlinger cohort or network rate |
| Source bitrate missing | Unknown | Zero, host 4Mbps target or measured network throughput |
| Guest physical30Hz | Guest display mode | Every video rendition is30FPS |

## One finite next diagnostic read

Recommended next implementation envelope: one overall 15-second monotonic
deadline, at most 1MiB for any dump, at most 4MiB combined raw dump bytes, and no
unbounded retries. Raw dumps stay in memory and are cleared; stderr/exception
text, titles, URLs, histories and arbitrary properties are not emitted. Each
child ADB process has its own smaller bound and is reaped before returning.
The following are proposed read commands, **not executed in this review**:

```text
adb -s emulator-5556 shell dumpsys -t 3 media_session
adb -s emulator-5556 shell dumpsys -t 3 media.metrics --prefix codec --since -15
adb -s emulator-5556 shell dumpsys -t 3 media.codec
```

The service-specific read bound must also be enforced by the host collector;
`dumpsys -t` alone does not bound a failed ADB connection or output allocation.
Service output varies with Android version. [Official dumpsys syntax and timeout](https://developer.android.com/tools/dumpsys)

Suggested sequence:

1. Read exact Morphe PID, UID and process-start identity with small numeric-only
   bounds; multiple candidate player processes remain ambiguous.
2. Collect one MediaSession snapshot with the existing parser. Require a unique
   active target session, state3 and speed1 for a playing-state witness. Retain
   position/update values, even when they are unchanged on a second read.
3. Read the recent `codec` queue once. Parse only a validated header and fixed
   MIME enum, decoder flag, bounded dimensions, optional finite rates and
   nonnegative frame counters. Reject encoders, audio records, foreign clients,
   malformed/ambiguous records and unvalidated timestamps. Do not select the
   newest decoder merely because its UID is correct.
4. If time remains, perform one `media.codec` read only to test whether an
   independently validated live-client identity exists. A component name, PID
   or UID alone does not prove that a queued record still belongs to the current
   rendition; a matching live codec/session identifier and its lifecycle must
   be established. A submitted closed record stays historical even if recent.
5. Take a second MediaSession snapshot and PID/start-identity read. A changed
   process, ambiguous session or state transition invalidates the identity
   bracket. Stop at the overall deadline, return fixed unknown reasons, and do
   not seek, pause, force-stop, set properties, clear metrics or run media auth.

Proposed numeric result fields should separate `playback_known`,
`process_identity_stable`, `decoder_record_known`, `record_freshness_known`,
`live_codec_link_known`, `mime_enum`, `width`, `height`, `dimensions_kind`,
`configured_fps`, `tracker_content_fps`, `tracker_actual_fps`, counters,
`itag_known=false`, `source_bitrate_known=false`, per-command error codes, and
host observation brackets. Known flags are independent; do not collapse them
into one success boolean. Missing properties produce null/unknown, not zeros.

## Gate and follow-on boundary

This design does not relax the frozen supervisor or fabricate its strict source
gate. The next gate schema should admit unknown itag while preserving separately
verified public source identity and actual format provenance. MediaSession alone
does not prove the public video ID. A metrics-only result without live association
may support a **source-supply observation** labeled format-unknown; it cannot
validate a controlled same-format A/B experiment.

If this single bounded probe cannot observe the live format, stop this branch of
the preparation attempt. The future reliable route is a deliberately scoped
player-side read of the selected video Format/current output Format and media
position, associated with that actual player/codec instance. That requires a
separate design and acceptance; it must not be obtained by silently installing
hooks, changing Morphe or recompiling the frozen App during this read-only task.

The next source test still needs pre/post observations for format and position,
separate source and stream dimensions, fresh CPU brackets, and an explicit event
coverage interval. None of this review certifies real media FPS, codec freshness,
sampling overhead or the cause of the observed raw30→submit19 supply deficit.

Offline validation for this review: the unchanged existing playback-state and
quality-menu parser suites ran together, 16 tests PASS. No new decoder parser or
fabricated dump fixture was added, because the installed dump contract has not
yet been observed.
