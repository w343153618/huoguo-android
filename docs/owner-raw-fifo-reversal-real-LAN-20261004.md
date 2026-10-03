# FIFO reversal on the same frozen real-phone LAN testbed

The protected FIFO reversal completed using frozen307ea8f, installed alpha8/code39, the limited OnePlus12, and nonisolated M1 RAM8. Gateway, driver, encoder59264ab7, packetizer567231ae, JAR and helper pins match the preceding explicit latest observation. Only the raw-policy opt-in was changed back to FIFO; published defaults and persistent services were not changed. Physical LAN,540×960/30cap/4M VBR/80ms/lead0/AAC on, stage/startup/PCM queue off and existing wait/guard remain. This is not a public, cellular, Northeast Wi-Fi, MTK V50, optical or acoustic acceptance.

The result **does not restore the original28.358ms median queue age**. FIFO itself now has a0.101ms median. This directly limits the preceding hypothesis that selecting latest alone caused the much smaller median: startup pacing, timing phase and scheduling remain unresolved factors. It would be incorrect to promote latest as a proven28ms end-to-end latency improvement.

| Observed measure | Earlier FIFO | First latest | FIFO reversal |
|---|---:|---:|---:|
| Capture enqueue→raw dequeue median |28.358ms|0.089ms|0.101ms|
| Capture enqueue→raw dequeue P99 |38.374ms|0.359ms|28.237ms|
| Maximum queue age |39.558ms|0.663ms|52.905ms|
| Phone independent SF cadence |29.953/s|29.934/s|29.814/s|
| Maximum observed phone SF gap |66.301ms|66.299ms|124.331ms|
| Observed phone gaps greater than100ms |0|0|1|
| Source cumulative dropped-frame increment across endpoints |0|98|70|

These are separate windows at different content positions. CPU maximum-cap endpoints match between the latest and reversal runs, but current frequency, host load and sampling phase are not fixed. Different unknown SF tails are retained. The table is an observation series, not a fully controlled ABBA or a causal performance ranking.

Strict capture-only FIFO reconstruction accepts1123 enqueues,1078 dequeues,45 capacity replacements and a final empty queue. All916 eligible steady decisions have **one pending frame**; none has two. The fixed-time newer-frame alternative is identical at all916 decisions. Thus changing which frame is selected cannot avoid their observed tail waits when only one complete frame exists. The matched token-budget wait covers almost all of that age tail: age P99 28.237ms, overlap P99 28.180ms, outside-wait P99 0.267ms/max0.942ms. These overlapping regions are not summed into CPU cost or physical latency.

Raw anchor deltas are916 captured and916 submitted in30.579144s,29.9551/s; feed delta916/30.579256s,29.9549/s. No steady replacements or idle repeats were recorded.145 observed budget waits have median21.983ms/max52.830ms. Whole-session45 capacity replacements occur before the first steady anchor, so there is again a startup backlog/pacing question. The hardware `pending_frames_replaced` counter combines collector capacity replacements and explicit latest-selection skips; the earlier latest count40 must not be described as40 explicit selection skips. The FIFO reconstruction independently assigns this reversal's45 to capacity replacement. Whole FIFO session is1123 capture/1081 submit/45 replacements/3 idle repeats.

The source remains the same fresh identity3470/UID10235/start1952, foreground and active MediaSession at paused endpoints469021→512097ms. Both describe BBB `aqz-KE-bpKQ`,299/avc1/1920×1080@60 and251/Opus. Source drop98→168 and total14106→15422 cover the wider43.076-second play/pause sequence, not exactly the sampler interval. Format60 is not unique decoded/presented60, overlay cost and full-window content continuity remain unknown. The same source was paused before long gateway shutdown.

Phone SF cadence29.814/full requested-window29.165, maximum gap124.331ms and one observed gap above100ms have a481.095ms unknown trailing host interval. Source SF cadence29.936/full29.365/max95.716ms/>100ms0 with504.998ms trailing unknown. All observed ring comparisons overlap without missing/changed-layer or disjoint-ring errors. App callbacks still echo target timestamps; they are not independently presented timestamps.

Capture and feed diagnostic sink/producer closure are accepted in this reversal. Native encoder final clock/footer remains unaccepted, whole pipeline coverage false and complete joins0. This is better diagnostic closure for two streams, not proof of a runtime improvement or complete native coverage. Packetizer natural0 and the encoder parent's normal group cleanup remain distinct from a Swift final receipt.

Whole App counters include one Inbox overflow, seven cleared frames, one decoder input timeout and the retained recovery counters; no event-window attribution assigns them to steady playback. FEC expiry/reference/dependency loss and mapping rejection are0. No acoustic synchronization or physical touch latency was measured.

Driver0, owned gateway natural0 and supervisor0 confirmed unique actual owned quiescence/stop_failures0, without signals. Root separately verified original gateway26875/start/source215a/runtimeb708 and four owned-role counts0, experiment/helper PIDs absent, helper uninstalled and private input removed. M5, NPS, other NPCs, nodes, CPU settings and original services were not changed. [Closed numeric evidence](owner-raw-fifo-reversal-real-LAN-20261004.json) includes all acceptance boundaries.

Exact previous evidence78492fe/run37157621744 now independently reads overall/build/udp_candidate success. This document is not an APK release. The immediate next bounded iteration is one latest replication on the same frozen dependencies to check the tail/phase association, without a broad matrix, buffer enlargement, wait removal or promotion of defaults. Public-path admission remains separate from the LAN reservation that blocks the original public worker.
