# Synthetic stream source

A separate guest-side app for repeatable streaming diagnostics. It has no Internet,
storage, media or runtime permissions and contains only generated Canvas graphics.
The client and gateway remain separate apps/processes.

Build with the repository's Java 21 / Android SDK 37 environment:

```sh
./gradlew :diagnostic-source:assembleDebug :diagnostic-source:lintDebug
```

APK: `diagnostic-source/build/outputs/apk/debug/diagnostic-source-debug.apk`.
Release builds also use the local debug signing key because this guest test app is
not a client update or a distributed production app.

After explicitly installing it on the intended test guest, start/reset and stop:

```sh
adb shell am start -S -n local.remoteandroid.benchmark/.DiagnosticSourceActivity --es run_id 00000000-0000-0000-0000-000000000001
adb shell am force-stop local.remoteandroid.benchmark
```

Add the intended `adb -s SERIAL` selection when multiple devices are connected.
The optional `run_id` must be a UUID; it appears only in synthetic lifecycle logs
tagged `DiagnosticSource` and does not affect rendered content. Unrecognized
extras, including stream resolution or bitrate, do not change the scene.

Every activity start/resume resets to frame 0. A 540 × 1200 logical portrait scene
scales uniformly to the guest window, respects cutouts and hides system bars.
Android may ignore orientation requests on large displays, so the scene retains
its portrait geometry within that window. BACK finishes the activity, using the
native back callback on Android 13+ and the legacy callback on older releases.
No client overlay or touch interception is involved.

Choreographer advances the source using fixed 1/30-second time steps. The identical
600-frame / 20-second cycle contains continuous scrolling cards, moving geometric
edges and waveforms, text details, and a changing source frame marker. Missed time
steps are skipped rather than caught up in a burst. Canvas uses normal hardware
acceleration, with a reused Paint and Path and no external assets/libraries.

30 FPS is a source scheduling target, not a measured encoded, received, displayed,
or end-to-end rate. Guest rendering load and display refresh can reduce source
draws; lifecycle logs report unique drawn scene frames, skipped source ticks and
whether the Canvas was hardware accelerated. This modest vector scene is a
controlled motion workload, not a substitute for video playback, audio sync or
touch-to-photon acceptance on a real phone.
