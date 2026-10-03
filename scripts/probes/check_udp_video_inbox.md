# Offline experimental UDP video inbox checks

From the canonical project root:

```sh
python3 scripts/probes/check_udp_video_inbox.py
```

The script compiles the actual `UdpVideoProbe.VideoInbox` and runs the 27 checks
in `tests/java/local/remoteandroid/direct/AsyncVideoInboxProbe.java`. Checks cover
initial IDR/config requirements, FIFO ordering, staging P frames behind an IDR,
frame/byte limits, overflow clearing the dependent chain, stale epoch invalidation,
stale timeout not discarding a newer recovery IDR, successful IDR recovery,
oversized admission, timeout recovery state, bounded close/drain, and the actual
optional metrics hooks for same-RX burst offer cadence, take counts and frame age.

Required external paths are the existing Android 37 SDK jar under
`~/Library/Android/sdk`, JDK under `/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home`,
and previously built app Java classes in `app/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes`.
Flags `--sdk`, `--java-home`, and `--app-classes` override these paths. Temporary
class files are created under `/private/tmp` and deleted after the check.

Frame bytes are placeholders used only for dependency/queue state. No codec,
phone, ADB, network, real media, or service is used. Passing does not prove device
concurrency, actual codec cancellation behavior, FPS, touch, AV sync, or WAN
performance; those require the same-APK real-phone matrix.
