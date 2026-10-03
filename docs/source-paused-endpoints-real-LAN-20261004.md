# Real BBB playback, bounded paused descriptors, and the 30 FPS LAN baseline

One new protected run completed with the installed alpha8 App and the limited OnePlus 12, using the original nonisolated M1 owner guest (6 cores, 8 GiB, physical 1080×1920 at 30 Hz). The phone was on the same home Wi-Fi and connected to the dedicated physical LAN authenticated UDP candidate. This is not the public NPS, cellular, remote Northeast Wi-Fi, actual MTK V50, touch-latency or acoustic acceptance. Neither a new APK nor a media default was released.

The source now has independently qualified current descriptors around the sampling window. Both paused endpoints match exact PID3470 / UID10235 / start1952, foreground, active MediaSession, public BBB video `aqz-KE-bpKQ`, video itag299 / avc1 / 1920×1080 with a 60 FPS descriptor, and audio251 / Opus. Position progressed from385062 to426301ms across the complete preparation/play/pause sequence; the cumulative drop counter stayed0, total11552→12779. This progress is not a precise 30-second decoded-frame measurement. Two matching endpoints do not prove continuous content through every instant. Actual source gfxinfo afterward reports Vulkan. Stats overlay remained on and its overhead is unmeasured.

The candidate explicitly resumed only after a fresh paused descriptor inside the owned driver. After both actual samplers naturally returned0, it paused first, read the second descriptor, and published the existing helper marker. Resume/pause each had verified state and full identity/focus/owner brackets; input exit0 alone was insufficient. Pause took0.319s, post Stats2.299s. The helper's existing deadline did not change. Normal actual App login, V50 button, exit-confirmation continue/leave, and reconnect passed; original account/signature/data and CPU settings were preserved.

Parameters remained 540×960 actual H.264 size, 30 FPS cap, 4 Mbit/s VBR, 80ms buffer, lead0, AAC on, PCM queue/startup/stage experiments off, existing FIFO2/native defaults and socket wait/guard retained. This is one observation, not an AB improvement claim.

| Observation | Source | Phone |
|---|---:|---:|
| Observed SurfaceFlinger cadence |29.965/s|29.953/s|
| Count divided by full requested window |29.191/s|29.158/s|
| Maximum observed gap |59.240ms|66.301ms|
| Observed gaps greater than100ms |0|0|
| P99 observed gap |39.714ms|49.725ms|
| Unobserved trailing interval |714.026ms|620.842ms|

The SF poll chains had overlapping rings, one selected layer, no observed missing-layer/ADB/regression failure, and no disjoint-ring risk. The trailing intervals remain unknown, so this is not a complete guaranteed 30-second zero-stall claim. SF timestamps are independently collected presentation-cadence evidence; cross-device clock alignment, unique video frames, physical latency and actual sound are not established. App callbacks again echo requested targets and are not an independent display measure.

Within the host-native-clock sampler dispatch/completion bounds, non-atomic raw anchors advanced917 captures and917 submissions over30.574025s (29.9928/s), with no replacement or idle-repeat delta. Feed anchors advanced917 media records over30.574571s (29.9922/s). These are separate anchors, not an exactly shared frame cohort. Complete raw RGBA arrival through native input submission did not lose the old30→19 supply in this window. This does not explain or retroactively repair that historical failure.

A remaining latency region worth investigating is complete capture enqueue→raw dequeue: median28.358ms, P99 38.374ms, max39.558ms in917 observed intervals. Raw write/flush P99 3.970ms and max6.052ms; feed publish max1.624ms. Budget waiting occurred in580 observed loops with max39.503ms. These regions overlap and are not serialized CPU costs. A frame can wait in the small FIFO while pacing; these numbers alone do not establish one root cause or justify disabling waiting. No queue/buffer/default was enlarged or changed.

The first App session's whole counters include one Inbox overflow (four cleared frames), one decoder input timeout and two late discards. There is no event-window correlation in this report, so those counts cannot be assigned to the measured steady window. FEC/reference/dependency loss counters stayed0. The host packetizers both completed with final summaries and natural0, no TERM/KILL; the Swift encoder-parent receipts still observe normal group cleanup−15 and its diagnostic native traces lack an accepted final clock/quiescence footer. Strict full pipeline coverage remains false and complete cross-stage joins remain0. Missing native timing is unknown, not zero.

Phone CPU limits changed between the numeric pre/post brackets: policy0 maximum556800→672000, policies2/5 maximum614400→960000, policy7 maximum672000→902400 kHz. The test did not write these settings. Limits/frequencies were not continuously pinned, so this run cannot be a controlled comparison with earlier slower results or an acceptance for a V50. App display readback stayed120 Hz; the sampled surface reported a16.67ms cadence period. Those observations describe different layers and do not authorize a global display-setting change.

All owned processes completed: driver0, gateway natural0, supervisor0, unique shutdown quiescent=true and stop_failures0, zero supervisor/original-service signals. The final source was paused, helper package/input removed, target/helper PIDs absent, and original gateway26875/start/source/runtime retained with all four role counts0. M5, NPS, other NPCs, Headscale identities and domestic exit rules were unchanged.

The prior playing-UI candidate failed before instrumentation; a separate protected source-only diagnostic reproduced its three-second UI dump timeout while numeric playing identity remained valid. It explains a measurement setup failure, not a media stall. Paused descriptors fixed the experiment workflow without increasing a timeout, clearing data or installing hooks. Initial helper staging and unresolved receipt-path refusals are preserved in the JSON as pre-media failures.

Evidence: [typed results](source-paused-endpoints-real-LAN-20261004.json), [opt-in source-window contract](source-stats-window-contract-20261004.md). Raw private logs, source UI XML and credentials are not in Git. Next action is a bounded analysis of the observed enqueue/pacing age and a separate public-path sampling design; neither repeated menu preparation nor a default change is warranted by this one run.
