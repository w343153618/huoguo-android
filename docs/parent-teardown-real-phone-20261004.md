# Actual parent-teardown observation on the owner LAN phone

This bounded observation used the nonisolated owner M1 and authorized,
CPU-limited OnePlus12 on the same home Wi-Fi. Authentication used existing
huoguo credentials and the trusted LAN certificate. App media used UDP to the
dedicated LAN endpoint; this was not a public NPS, cellular, remote V50,
Tailnet direct/relay or physical latency test. Numeric evidence is in
[the result projection](parent-teardown-real-phone-20261004.json).

## Installation failure and scoped recovery

The first supervisor `e31d0605` failed during matching helper installation:
ordinary ADB install reached its45-second timeout. No driver, READY or media
started. Its already-owned LAN listener finished at its existing300-second
natural limit; actual shutdown confirmed quiescence, zero supervisor signals,
and explicit admission release. This failure is retained, not counted as a
media result.

The matching original-signed helper83722157 was absent. Root then pushed only
that pinned APK to a unique owned phone path, verified its bytes, used a
scoped root `pm install` for user0, verified the installed APK SHA, and removed
the staged file. Global security settings were not changed. Its restricted
ownership receipt was transferred to a fresh private supervisor directory;
the same supervisor and frozen source bytes were reused. The second attempt
rechecked the actual installed hash before accepting that receipt.

## Actual phone and protected shutdown

The fresh attempt completed the normal UI login, V50 preset readback,
30-second media observation, exit-confirmation/continue/leave and reconnect.
Driver exit was0. Both sessions read back540×960, 30FPS cap, 80ms buffer and
LAN UDP. Target bitrate remained4M VBR, surface lead0, stage/startup/PCM queue
off, AAC enabled, raw FIFO2 and native slots3. Phone decoder reported hardware
video1. The phone's CPU limits and global120Hz setting were not written.

The owned listener later exited naturally0 and reported the sole successful
candidate shutdown with quiescence true / stop failures0. The supervisor
verified original owner identity and all measured media-role zeros before
releasing admission. No original service or owned supervisor signal was sent.
The helper and target App processes were absent afterward; the helper was
uninstalled, the private input was absent, and45560/45963/45965 were clear.
M5, formal NPS/NPC identities, guest boot/resources and public manifests stayed
unchanged. Native child signals below are a separate, inner cleanup layer.

## Measured cadence and coverage

| Independent selected SF layer | Full requested-window FPS | Active presentation cadence | Maximum observed gap | >100ms gaps | Unknown host tail |
|---|---:|---:|---:|---:|---:|
| Source |22.260|29.956|67.260ms|0|181.627ms|
| Phone |22.997|23.937|1027.696ms|7|818.741ms|

The source's last poll with new presentations was at23.392s. Its final poll
at29.827s still showed the same ring endpoint:6.435 seconds of observed
unchanged source output. The29.956 active cadence excludes that inactivity;
it must not be described as stable30FPS across the requested window. Ring
overlap passed for the observed poll chains, with their unknown tails retained.
SF timestamps are not content-frame hashes or optical delay, and cross-device
clock domains were not independently verified. App callbacks echo scheduled
targets and are not substitute presentation measurements.

The long first host session's raw counter interval was36.784045s:872 captured,
869 submitted, five newly replaced, zero idle-repeat increments. These are
counter-anchor deltas with different boundaries from the SF30-second window,
not a same-frame cohort or a steady-only causal comparison. Whole-session
condition wait reached260.042ms, raw pipe write1311.217ms, encoded socket
write1.361ms and feed publish0.618ms. Startup and steady contributions have not
been separated here; the1311ms value must not be called a steady bottleneck.

The movie visibly selected before this sequence was the official BBB title.
The source-only frame after the phone test showed a different Russian cartoon.
Autoplay/content transition occurred somewhere in this preparation/test
sequence; its exact overlap with the measured window is not known. Actual
Stats Video ID/codec/itag/dimensions/content FPS were never independently read.
This is explicitly unknown-format, potentially changing content. It cannot
support a renderer A/B improvement or a “BBB remained60fps” claim. The next
performance comparison must fix content identity and playback position and
check both afterward.

## Parent observation closes one diagnostic question

Both unique owned attempt directories published worker-parent and
encoder-parent receipts from the opt-in `a62d8539` source. Their actual recorded
parent/child/PGID links form two chains: worker child→encoder parent, with the
encoder child sharing that worker process group. Observers recorded returned
cleanup, zero observation errors and zero dropped operation rows. Worker
group cleanup waits returned0; encoder-parent poll observed native exit−15 in
both attempts. The exact opcode11 result is an exit observation; unrelated
operation return values are not treated as exit codes.

The existing cleanup path sends TERM to the owned worker process group, which
includes the native encoder. Together with those actual links and poll results,
this supports that normal session cleanup ended the native process by SIGTERM.
It does not show a native crash during playback and does not explain the phone
long gaps. The native traces again have a start but no end/footer; therefore
the strict analyzer still rejects native clock/producer coverage and all
complete pipeline chains. The capture sink closing cleanly cannot repair that
missing native evidence. A separate graceful native-stop diagnostic can be
designed next; no cleanup policy was changed in this observation.

The result is successful UI/shutdown observation with incomplete pipeline
coverage and visible long pauses. It is not stable30FPS, improved optical
touch/acoustic synchronization, a V50 acceptance or a new App release.
