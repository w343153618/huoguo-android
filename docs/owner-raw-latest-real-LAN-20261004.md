# First explicit latest-policy real-phone LAN observation

One NEW protected owner-only LAN run completed using the unchanged installed alpha8 App, limited OnePlus12, and nonisolated M1 RAM8. The source/runtime freeze was307ea8f, with the same hardware core, VideoToolbox encoder59264ab7, packetizer567231ae, JAR and matching gold39 helper. Only the explicit local raw-selection experiment chose `latest`; default/deployed FIFO, wait/guard, native slots, resources and phone settings remained unchanged. No APK was released. Exact307ea8f/run37157030913 was independently read as overall success; subsequent evidence commits need their own CI readback.

Existing live original-registry idle witness, continuous45965 reservation, complete role checks and formal TCP checks protected the session. New gateway CLI and host cleanup reports both read back requested `latest`; actual hardware phase summaries also reported `latest`. The new candidate/driver were frozen in a NEW private directory, separate from the previous FIFO run. Neither M5, NPS, other NPCs, Headscale nodes nor the resident M1 owner gateway received a signal or change.

The same player identity3470/10235/start1952 had fresh paused endpoints before and after the actual30-second sampler window. Both endpoints match BBB `aqz-KE-bpKQ`, video299/avc1/1920×1080 with a60 FPS descriptor and audio251/Opus. Position426301→469021ms covers the wider play/pause sequence. Stats overlay remained on; overhead and full-window content continuity are unverified. Critically, the source player's cumulative drop count rose0→98 while its total rose12779→14106. This is not zero content-frame loss. The increments cannot be assigned to the exact sampler window or attributed to raw policy without another qualified comparison.

Settings remained540×960,30cap,4M VBR,80ms,lead0,AAC on and PCM queue/startup/stage off. Both actual samplers naturally returned0; the driver paused the same source before the post-Stats readback and before long natural shutdown. Normal App UI login/V50/continue-leave/reconnect passed.

| Independent observed SF measure | Source | Phone |
|---|---:|---:|
| Cadence in the observed poll chain |29.963/s|29.934/s|
| Count divided by requested window |29.423/s|28.932/s|
| Maximum observed presentation gap |92.023ms|66.299ms|
| Observed gaps greater than100ms |0|0|
| Unknown trailing host interval |442.074ms|842.550ms|

Poll-ring chains remained overlapping, without observed disjoint/changed/missing-layer errors. Unknown tails remain excluded. SF measures surface presentation cadence, not unique decoded video frames, cross-device optical latency or actual sound. App callbacks again echo requested target times. This is same-home Wi-Fi physical LAN, not public NPS, cellular, Northeast Wi-Fi or actual MTK V50 acceptance.

Within the explicitly bounded host MONOTONIC sampling interval,914 matching enqueue/dequeue pairs have **queue age median0.089ms/P99 0.359ms/max0.663ms**, compared with the previous FIFO observation's median28.358ms. This is a large change in that one host queue region; it does not measure28ms of physical touch-to-display improvement. Raw anchor rates are capture29.963/submit29.963, feed29.9622; steady replacement and idle-repeat deltas are0. No budget-wait interval was observed in that steady subset. Raw write/flush max8.282ms; feed publication max24.559ms. Regions are not additive and the anchor windows are not an exactly identical frame cohort.

The first whole hardware session counted1113 capture/1076 submit/40 pre-encode supersessions/3 idle repeats. Its steady counters already had40 supersessions at the first anchor and did not increase through the measured window. Thus this run did not keep skipping steady frames to obtain the small queue age. One plausible mechanism is that latest selection cleared initial raw backlog and changed the pacing phase for subsequent frames. This remains a hypothesis: one run cannot isolate startup scheduling, content position or concurrent host load from the explicit selection policy. The prior fixed-time counterfactual deliberately did not predict the observed dynamic phase change.

The capture sink's closed record count and clock bracket are accepted, but its diagnostic producer-quiescent field isfalse. The native encoder still has no accepted final clock/quiescence footer, and strict complete cross-stage joins remain0. These stage observations are bounded subsets; full pipeline coverage remainsfalse. Root did not override the analyzer or reinterpret packetizer natural0 as a Swift encoder final.

Whole App counters include two Inbox overflows clearing8 frames, one input timeout and one late discard. No event-window attribution places them in the steady interval. FEC expiry/recovery/reference/dependency/mapping-rejection counters are0. Existing audio cleanup reports success, but acoustic/lip-sync latency was not measured.

Phone maximum CPU caps before the driver were672000/960000/960000/902400kHz; a read after supervisor cleanup showed the same caps. Current frequencies differed, and neither the interval nor host load was continuously pinned. The post read is not immediately after sampling. No CPU/global display setting was written; global120Hz remains. Similar endpoints cannot establish a controlled AB or V50 equivalence.

Driver0, owned gateway natural0, supervisor0 and the unique shutdown receipt confirmed actual owned quiescence with stop_failures0 and no signals. Root then independently checked original gateway26875/start/source215a/runtimeb708 and all four role counts0; helper package/PIDs and private input were removed. Final source remains paused469021ms. [Closed numeric results](owner-raw-latest-real-LAN-20261004.json) retain all boundaries; private traces/logs and credentials are not committed.

The next immediate iteration is a protected **FIFO reversal** on the same new gateway source and same actual dependencies, without changing buffer, bitrate, cap or device settings. Check whether the host age/phase difference repeats and whether the source content-drop increment persists. Keep FIFO as the published default pending that layered comparison. Public-path admission remains separate; do not copy the45965 LAN blocking reservation into a test of the same original public worker.
