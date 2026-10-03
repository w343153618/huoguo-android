# Alpha8 actual public UDP30 follow-up after M1 source restoration

The new helper successfully used the App's normal encrypted saved-credential restoration for the existing account, authenticated public M1 UDP media, confirmed exit/continue, disconnected and authenticated a second session. No private login input was accessed and no secret was exported. The independently observed video performance did **not** pass stable30FPS: source SF cadence was30.002FPS while phone SF cadence was17.262FPS.

This was the original nonisolated owner M1 plus CPU-limited OnePlus12 on same-home Wi-Fi using public NPS, not a V50, cellular, P2P, isolation, optical or acoustic acceptance. Published alpha8/code39 APK SHA was read from the actual installed target and matched `a663c4d0…`; it was not replaced by the local alpha9/code40 candidate. Matching helper `83722157…` was compiled against frozen gold39 source and later uninstalled. M5, formal NPS and unrelated NPCs were not changed.

## Actual settings and measurement

The actual V50 button selected540×960,30FPS,4Mbps VBR and80ms; stage/startup/PCM queue candidates remained off, Surface lead0. The requested public control endpoint was HTTPS49556 and App media peer UDP15556. App route/lease/socket-bind readback passed, but is not packet-level outer/domestic/P2P validation. NPS internal QUIC reliable streams remain a separate layer.

| Evidence | Observed result | Boundary |
|---|---:|---|
| Source SF45s | cadence30.002; whole requested window29.639 | observed ring chain; last458.478ms unknown |
| Source long gap | max75.213ms; >100ms0 | selected source layer, not content hashes |
| Phone SF45s | cadence17.262; whole window17.063 | observed ring chain; last371.827ms unknown |
| Phone long gap | max1094.041ms; >100ms105 | no optical or cross-device clock proof |
| Helper steady receive progress | 828 frames /46.312s =17.879/s | normal worker admitted frames |
| First-session Inbox | overflow0, clear4, recovery1 | cumulative; no per-event timing |
| First-session input timeout/late drop | 1 /5 | cannot attribute these to steady codec blocking |
| Host packetizer whole-session supply | 976AU /51.598s =18.915/s | includes startup; differs from SF45s window |
| Host output/deadline/dependency | 948 /7 /21 | whole-session packetizer counts |
| Phone FEC delivered/expired/dependency | 936 /9 /6 | whole-session native receiver counts |

All924 codec callbacks echoed requested targets; they are not independent presentation timestamps. Hardware decoder boolean was1. The absence of Inbox overflow does not prove a zero-delay decoder, but this sample does not support enlarging that queue as a remedy. The supply deficit needs investigation before treating the phone decoder or network alone as the cause. The additional raw capture/submit readback is tracked separately, with its own5s and wall-time boundaries.

The packetizer final, EOF and natural return0 were confirmed for both sessions, with no packetizer TERM/KILL. This is specifically the packetizer's lifecycle; it does not establish a corresponding VideoToolbox encoder final, since its separate host hardware close path can terminate its child. Unpublished4-byte partial tails were cancelled normally. Both App exits and the second normal authentication completed.

## Confounders and failed preparation preserved

The source still read physical1080×1920/density480/30.00Hz and actual `Skia(Vulkan)`/PID5212 after the run. Public BBB motion was visible. However, the attempted `t=90s` URI routed to Chrome, and a standard URI restored the player. Later attempts to open its settings exposed the Morphe queue action rather than a fresh Stats for nerds readback. The prior itag299/1920×1080@60 observation is therefore **not** independently assigned to this reload. The last visible position before preparation was6:21. There was no controlled same-content Vulkan/OpenGL A/B.

The phone still reported global120Hz, but CPU limits differed between the earlier preflight and post-run: policy0 maximum787200→672000kHz, and several minima changed. The test issued no CPU-setting commands, and those samples were not immediate paired measurements around this window. Neither fixed-frequency causality nor a performance improvement/regression from the earlier135s trial is established.

The first driver attempt failed with `FileNotFoundError` before instrumentation because the task PATH omitted `/usr/sbin` for `lsof`. It is retained as a failed attempt. The second used the complete system PATH and passed normal UI/authentication/exit/reconnect. That functional PASS does not mean its17FPS passed the streaming target.

## Capture-to-submission supply gap located

The additional numeric-only host review found complete gRPC raw delivery near30FPS, while completed raw submissions were14.18–22.92FPS across ten approximately5s rows. Eight selected interior intervals total41.144s: raw30.0163, submitted18.8362, replaced11.1560FPS. They are inside an SF request window inferred from its end wall time, rather than an exact synchronized frame cohort. Whole hardware close counters were1546 raw/979 submitted/567 replaced, without idle repeats. This narrows missing supply to the host path after complete RGBA delivery and before native input submission; it does not identify the unique wait or prove all phone long gaps have the same cause. See [raw readback](alpha8-raw-submit-readback-20261003.md).

The [clock/dependency review](alpha8-raw-clock-review-20261003.md) also rejects a specific earlier hypothesis: the token bucket consumes credit before writing, and write time replenishes credit; there is no fixed33ms sleep after each completed write. The running gateway selects persistent encoder SHA `59264ab7…`, confirmed by whitelisted active argv and LaunchAgent, while `3ad91b…` is an unselected fallback file. Recent native ready rows actually reported manual pool; low-latency property read failed with−12900, so no successful property readback exists. Current dependency selection and ordered ready rows are not an independently captured executable hash of the already-ended child.

## Next bounded experiment

Trace the frozen M1 pipeline from completed gRPC raw frame through stdin submission, native input/slot/pixel conversion, VT submission/callback and stdout consumption. Match timing segments and actual encoder parameters before changing a pool, limiter, decoder queue or network path. Do not assume a binary named `session-pool-encoder` is actually using a session pixel pool. First independently restore the current BBB format and fixed motion-position readback; record fresh CPU limits while preserving the user's constraints.

Whitelisted numeric results, coverage and dependency hashes are in [the observation JSON](alpha8-vulkan-public30-observation-20261003.json). Raw screenshots/logs, installed APKs and credentials remain outside Git.
