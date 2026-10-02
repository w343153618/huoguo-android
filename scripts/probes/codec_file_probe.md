# Isolated phone decoder replay

`CodecFileProbe` reuses the installed app's decoder selection, output dequeue and
release policy, but creates only a `FrameLayout` and a `SurfaceView`. It calls
`MainActivity.configure`, not `show`: there are no stream sockets, emulator
requests, accounts, login credentials, or audio packets in this component test.
It uses the existing `MediaPresentationMetrics` hooks for per-frame metadata.

The parent experiment must separately collect actual SurfaceFlinger presentation
metadata if physical display cadence is needed. `OnFrameRenderedListener` callback
counts and its vendor timestamp do not prove display FPS. Qualcomm callback
timestamps that equal a requested future release target remain raw observations;
strict causality and target-echo checks mark those estimates unusable.

## Build and prerequisite

Build the matching app and probe from the canonical repository root:

```sh
./gradlew :app:assembleRelease
python3 scripts/probes/build_phone_transport_probe.py
```

The instrumentation APK at `experiments/nps-transport/phone/build/phoneprobe.apk`
contains both the unchanged `PhoneProbe` streaming component and `CodecFileProbe`.
The installed app must include the `huoguo_codec_component_probe` intent guards in
`MainActivity.onCreate`, `login` and `onResume`; these suppress ordinary account
and updater initialization during replay. App and probe signing must match.
Installing or running a probe is a separate live action; compiling establishes
no phone decoder, real-video, LAN, WAN, or V50 performance result.

`--experimental-client` selects only `local.remoteandroid.direct.experiment`
and `local.remoteandroid.phoneprobe.experiment`. The matching independent App
must already be built/installed by the experiment owner; build its matching
probe with `build_phone_transport_probe.py --experimental-client`. The runner
checks the exact installed `CodecFileProbe` instrumentation target before
removing or reading any private report. The phone separately reports both
package names, and missing/mismatched package or setting readback preserves a
failed report and makes the runner return nonzero. Omission keeps the original
formal package pair; the probe's package/setting readbacks require matching
current probe classes in either pair.

## Fixed input format and bounds

Provision one synthetic fixture at this exact app-private path, readable by the
target app UID:

```text
/data/user/0/local.remoteandroid.direct/files/codec-test.h264framed
```

The opt-in experimental pair uses only the same fixed basename beneath
`/data/user/0/local.remoteandroid.direct.experiment/files/`. There is no arbitrary
package, fixture-path or private-report-path option. Provisioning the synthetic
fixture remains the experiment owner's separate action; the runner neither
writes nor removes the fixture.

The parser accepts 16–33,554,432 bytes, geometry from 16 to 4096 pixels per axis,
at most 4096 records / 3601 media frames, and a 1–30 second source timeline
(1 ms rounding tolerance). Each access unit is at most 8 MiB and must begin with
an Annex B start code. Source media PTS must increase strictly. Config records
may precede media; an in-stream reconfiguration is rejected. The fixture keeps
complete original bytes and is hashed before replay. It is never a real user's
screen recording or an arbitrary private file.

All fields are big endian:

```text
uint32 0x68323634       # ASCII h264
uint32 0x80000000       # geometry marker
uint32 width
uint32 height
repeat:
    uint64 pts_flags   # config bit 62, keyframe bit 61, PTS in low 61 bits
    uint32 au_length
    bytes  annex_b_access_unit
```

The first media PTS maps to `feed_start_ns`. Each frame is fed at its relative
PTS deadline, using `System.nanoTime()`, with the source bytes and PTS unchanged.
The source can contain 60 FPS even when the display/release cap argument is 120;
`source_pts_fps` and `fps_limit` are recorded separately. Input slot waiting and
pacing lateness are preserved rather than dropping samples to meet an output
target. A source-duration-plus-eight-second hard feed deadline and a three-second
final drain bound prevent an unresponsive codec from running indefinitely.
No end-of-stream packet is injected, matching normal continuous stream behavior;
any frames retained by the codec at the bound stay visible as uncompleted input.

Fixtures can be generated locally using
[`measure_vt_codec_fixture.md`](measure_vt_codec_fixture.md). Choose a fresh
private temporary output for each encoder variant. Do not commit bitstreams or
APKs. When comparing fixtures, inspect their profile/SPS/reordering metadata as
well as the common synthetic source hash.

## Live invocation for the experiment owner

With the fixed synthetic fixture already provisioned and the phone idle, the
rooted USB runner performs one component test:

```sh
python3 scripts/probes/run_codec_file_probe.py \
  --fps 60 --buffer 80 --video-release immediate \
  --profile dedicated-vt-low-latency \
  --output docs/evidence/native-iteration-20261001/codec-lowlat-immediate.json
```

Use the same fixture hash for `scheduled` versus `immediate`, then repeat with the
separately generated encoder variants. `--fps` permits 60 or 120; `--buffer`
permits 30 through 100 ms. `--profile` is only a report label, never a decoder or
encoder configuration. There is no IP, port or account argument. The runner
does not push, delete or alter the input fixture, and the probe does not write
user connection preferences. Its final cleanup stops the temporary decoder and
returns the app to the connection page.

The instrumentation result is a small `report_file=codec-test-report.json` plus
`report_bytes` pointer. The full UTF-8 JSON is atomically written to the fixed
private `files/codec-test-report.json` path to avoid Binder's transaction limit.
The runner reads only that exact path, verifies its byte count and JSON object,
writes the raw and summary evidence files, then removes only that private report
in `finally`. The input fixture remains available for repeated same-hash tests.

The report retains every media input observation and every captured callback,
access-unit sizes, source PTS, receipt/input/ready/release/target/callback times,
strict vendor timestamp validity counters, evictions, last queued/callback PTS,
and the last completed experiment stage. Exception reports contain only a class
name and bounded metadata, not payloads, secrets, screenshots or full logs.
`codec_callback_interval_fps` is Java callback throughput; `component_feed_interval_fps`
is paced file input throughput including backpressure. Neither is physical
display FPS. Input-to-ready includes decoder buffering and queueing; it is not
an isolated measure of GPU execution. Whole-run data remain complete; a separate
steady-window summary excludes the first second by source receipt time.

## Matched phone controls

Buffers permit 30 through 100 ms, reflecting the user's explicit 100 ms
experiment allowance. `--arrival-clock` constructs `PlaybackClock(buffer, 0,
false)` and disables only decoder-driven shared-clock reanchoring. Its omission
preserves the original true mode. `--video-release immediate` bypasses
`videoDeadline`; compare scheduled/immediate with arrival-clock enabled when
isolating release rather than changing both clock feedback and release.

`--display-hz 60|90|120` is independent of `--fps`. Explicit requests choose an
available mode at the current physical resolution, then require start and end
readbacks within 1 Hz; unsupported or unapplied modes fail closed. Omitting the
option keeps the original preferred-refresh-rate hint of `--fps` and records
actual start/end modes without certifying that the hint applied. These are
window-only requests and restoration, not global display-setting changes.
`MainActivity.configure` still uses `--fps` for its Surface content hint; the
fixture's actual source PTS rate remains a third, separately reported rate.
Neither mode readback nor vendor callback certifies actual SF cadence.

For an owner-provisioned same-hash fixture and installed matched experiment pair:

```sh
python3 scripts/probes/run_codec_file_probe.py \
  --serial <phone-serial> --experimental-client \
  --fps 120 --display-hz 120 --buffer 80 --arrival-clock \
  --video-release scheduled --profile same-fixture-scheduled-arrival \
  --output docs/evidence/<new-experiment>/codec-scheduled-80.json
```

Repeat using the same private fixture hash with `--video-release immediate`,
then reverse the pair. A separate 80/100 scheduled comparison changes only
`--buffer`. A 60 FPS fixture with fps/display arguments 120 is supported;
`source_pts_fps` determines source delivery, not the requested cap/hint/panel.
Use a common window inside actual fixture duration, exclude final drain, and
collect independent phone SurfaceFlinger data if comparing display smoothness.
There is no UDP inbox, FEC, AudioTrack, or audio traffic in this probe, and it
does not expose the probe-only 8/16 ms Surface submission lead. This component
test can isolate phone output scheduling; it cannot validate real UDP, shared
audio/video playback, network loss, physical latency, or V50 performance.

## Offline checks

```sh
python3 -m unittest discover -s tests -p test_codec_file_probe.py
javac -d /private/tmp/huoguo-framed-codec-test \
  experiments/nps-transport/phone/FramedH264Fixture.java \
  tests/java/local/remoteandroid/direct/FramedH264FixtureProbe.java
java -cp /private/tmp/huoguo-framed-codec-test \
  local.remoteandroid.direct.FramedH264FixtureProbe
```

These check parser bounds, strict timestamp semantics, fixed-path private report
retrieval, large report preservation and summary labels. They do not start ADB,
an Android codec, a phone session or a network connection.
