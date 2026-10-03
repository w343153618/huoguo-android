# M1 source pipeline and native public-video selection readback

This is owner-only, nonisolated M1 source preparation. It does not install an
App, modify its preferences, restart a guest/gateway/NPS, or operate M5. The
published phone APK remains alpha8/code39. Raw pixels and restricted receipts
are private; the numeric projection is in
[the associated record](source-native-layout-readback-20261004.json).

## Actual source launch and pixels

The reviewed source launcher `3fe97d29` passed root and independent 40-case
inert checks. Root's single explicit execution completed in 4.225 seconds,
exit0, with 80 local clients started/reaped and no timeout termination. It
launched only the resolved installed Morphe MAIN component for user0.

Fresh PID3470 / UID10235 / start1952 stayed identical before and after. The
actual source collector read `Skia(Vulkan)` with HWUI `skiavk`; RenderEngine
remained `skiaglthreaded`, physical1080×1920, density480, rotation0, 30Hz. The
existing emulator48576, boot identity, configuration, original owner gateway
identity and all four measured media-role zeros remained unchanged. The
continuous45965 reservation was explicitly released only after those checks
and local child reaping. The formal TCP check is not an atomic admission gate.

The first ADB capture showed the startup screen; the subsequent authenticated
loopback gRPC single frame showed the loading home layout. A later read-only
ADB capture showed the loaded home page upright, including its thumbnails,
title text and navigation. These are distinct startup/loading moments, not a
simultaneous ADB/gRPC image comparison or a frame-rate measurement. The gRPC
client disabled HTTP proxy and did not call streamScreenshot. Password and
discovery token were used only in memory and were not printed or copied.

## Native search and first result

Root derived two fresh, bounded private candidates from the reviewed launcher;
the inherited protection and cleanup stayed intact. The search candidate
`9f533d69` performed only an observed search-icon tap, entered the fixed public
ID `aqz-KE-bpKQ`, and submitted the native search. It completed in 4.631 seconds,
exit0, 102/102 local clients, no timeout termination. The loaded result visibly
showed the official Blender title, “Big Buck Bunny60fps4K.” Search spelling
suggestions and that title alone are not an actual Video ID or codec readback.

The open candidate `afb509c0` tapped that observed first result, completed in
3.993 seconds, exit0, 81/81 local clients and no timeout termination. Its first
two captures showed loading; a later read-only ADB frame showed the movie in
the upright watch layout. A separate bounded numeric collector verified the
same PID/UID/start and matching MediaSession owner in playing state at both
ends. Its short bracket reported no position change; this is not continuous
visual-motion or frame-cadence evidence.

All three protected preparations preserved guest boot/resources, original
owner identities, role-zero readbacks, formal-session checks, and explicit
reservation release. They did not start scrcpy, a phone helper or a remote App.
The subsequent phone experiment is recorded separately and cannot be inferred
from these source-only results.

## Limits and next experiment

The actual Stats Video ID, codec, itag, current decoded dimensions and content
FPS are still unknown. Neither a thumbnail labelled60fps nor the guest's30Hz
proves current rendition or30 independent video frames. A future same-format
comparison requires those fields before and after, plus fixed motion position.
No optical/acoustic latency, UDP throughput, public/cellular/V50 experience or
host/LAN isolation acceptance is supplied here.

The next bounded real-phone LAN source-supply/parent-teardown experiment may
use the explicitly unknown format with fresh identity and playing witnesses.
It must retain the original admission reservation, exact live503 witness,
complete role checks and actual owned shutdown. An unknown format does not
authorize filling fields from historical itag299 or claiming a controlled
renderer comparison.
