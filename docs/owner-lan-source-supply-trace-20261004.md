# RAM8 owner LAN source-supply trace

The first automatically admitted real-phone trace completed. This round did
not reproduce the historical sustained capture30 → submit19 deficit. Its
source and phone SurfaceFlinger cadence were both near25FPS; that is not a
stable30 result or evidence that the historical problem is fixed.

The machine was the owner's nonisolated M1 environment, RAM8/6 cores,
1080×1920/30Hz, with the CPU-limited OnePlus12 on the same home Wi-Fi. The
actual path was trusted LAN HTTPS45560 authentication and UDP45963 media over
M1 en7; continuous reservation45965 protected the original owner registry.
Neither public NPS, cellular, the actual V50 nor an optical/acoustic setup was
tested. The requested stream was540×960/30cap/4M VBR/80ms, lead0, PCM queue,
codec-startup and stage diagnostics off, FIFO2/native slots3; AAC remained on.

## Actual source and admission

Morphe PID3553/UID10235/start2521 was stable with matching active MediaSession
state3/speed1. The automatic guard and first-capture qualification passed.
The first grpc return was4.414185s after the source observation in host
CLOCK_MONOTONIC. This measures observation-to-capture startup, not media or
touch latency. It qualifies only the first attempt, not the later reconnect.

The actual video format, itag, coded dimensions, content FPS and unique-frame
identity remain unknown. Private UI inspection showed a public cartoon, not
the intended BBB reference. A later VIEW/explicit UrlActivity launch returned
success but still displayed that cartoon. Successful intent dispatch and the
desired URL cannot establish actual source selection. Current HWUI was
skiagl/Skia OpenGL, with the known ADB capture layout anomaly; the earlier
Vulkan/itag299 observation is historical and cannot fill this round's fields.

An actual phone OS usage restriction was also found: Digital Wellbeing showed
the experimental App as disabled. After verifying the exact experimental App
and its OS confirmation, one calibrated native contact at each button removed
that App's restriction. Contacts were released; no global security or CPU
settings were changed. The login UI returned. This explains a test-availability
block, not the earlier video FPS deficit. A just-opened idle experimental App
was stopped before the driver absent-target check; user data was retained.

## Host steady state and initialization

The long attempt had raw write durations1797.489ms and163.942ms on its first
two iterations. The short reconnect had118.700ms on its second iteration.
These are initialization observations, not steady-state losses.

The selected steady window is first successful raw-loop end+2s to capture
end−1s, all in host CLOCK_MONOTONIC. Its counter-anchor interval was33.669793s:

| Observation | Result |
| --- | --- |
| Capture and submit counter deltas |846 /846; each25.1264/s |
| Additional pending replacements |0 |
| Raw header/RGBA write p99 / maximum |1.6418ms /4.116ms |
| Condition wait p99 / maximum |70.1723ms /104.782ms |
| Budget waits |0 observations |
| Encoded socket write maximum |0.750ms |
| Feed media publish maximum |0.402ms |

This window's main long waits were condition/upstream-feed waits. It does not
show sustained raw-write, budget, encoded-socket or feed-publication backpressure.
The old sampler-lock hypothesis is not proved by this round. Counter anchors
are a time subset, not an exact end-to-end frame cohort; these figures do not
measure accepted native VT latency.

## Independent phone and source cadence

| Selected SurfaceFlinger layer | Source | Phone |
| --- | --- | --- |
| Request window |30.010s |30.004s |
| Observed frames |741 |730 |
| Observed cadence |24.973FPS |25.139FPS |
| Full request-window rate |24.692FPS |24.330FPS |
| p95 frame gap |67.525ms |66.305ms |
| Maximum frame gap |104.542ms |107.738ms |
| Gaps>100ms |1 |1 |
| Unknown requested tail |254.762ms |805.069ms |

Continuous overlapping poll chains passed, with no observed ADB failure,
layer change or missing-layer poll. The unknown tails remain explicit; one
phone tail poll was skipped by its budget. These are independent layer cadence
observations, not unique video-content frames, optical display acceptance or
cross-device latency. SF/phone/host clocks are not subtracted. App callback
timestamps still echo the requested target and are not independent presentation.

The first App report contains922 received/903 queued/901 callbacks, two whole-
session Inbox overflows clearing8 frames, and audio_cleanup_confirmed1. These
counters include startup and lack a correlated steady event cohort; they cannot
be used to attribute the single steady SF gap. They do not measure audible sync.
The two reports and real UI confirmed exit/continue, cancellation and re-login.
No guest touch, multi-contact or optical/acoustic acceptance was exercised.

## Coverage, dependencies and shutdown

Both native files contain one CLOCK_MONOTONIC start header and2770/286
schema-accepted records, but no end/footer. The frozen analyzer correctly
rejects complete native timing/producer coverage: accepted complete frame
chains0, whole-pipeline coveragefalse. Capture/feed clean and quiescent sinks
cannot substitute for a native producer final. No footer was fabricated and
the analyzer contract was not relaxed.

The actual frozen driver wasd887421d, not the new cleanup-diagnostic5e5247bd.
The host used the existing f08 core with encoder59264ab7 and UPTIME
packetizer567231ae; the latter cannot be subtracted from MONOTONIC diagnostics.
Installed alpha8/code39 and its JNI matched their exact pins, with gold39
matching helper83722157. The helper and private inputs were cleaned up.

Supervisor/driver returned0, the gateway ended naturally, and actual owned
quiescence was verified before releasing reservation. Original owner PID26875
and source/runtime identity were rechecked; the supervisor dispatched zero
signals to original or owned jobs. This does not count inner worker cleanup:
source review found direct worker-group/encoder termination paths, and this
round has no separate encoder exit/status receipt. Packetizer graceful shutdown
cannot prove Swift encoder completion. There was no M5, NPS, original gateway
or guest restart, account change or APK release. CPU maximum
limits before/after matched; current-frequency movement remained OS-controlled.

Whitelisted numeric evidence and exact pins are in
[the derived record](owner-lan-source-supply-trace-20261004.json). Raw traces,
credentials, screenshots, APKs and phone reports remain private. The source-gate
commit2ea7ef9 additionally passed actual GitHub
[run37139764770](https://github.com/w343153618/huoguo-android/actions/runs/37139764770):
Linux1527 tests/84.159s/OK,11 existing platform skips, both Android jobs successful.
That run does not validate later unpushed cleanup-diagnostic bytes.

Next: independently fix/verify real source selection and inspect the native
producer final contract. Keep parameters unchanged for a bounded same-pipeline
source-supply observation once those gates are ready. Do not repeat this unknown
source and call it a same-format A/B, or enlarge buffers/queues to hide the result.
