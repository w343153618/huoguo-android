# Default-OFF same-parent native instrumentation driver candidate

The native helper owner can now create its own fixed instrumentation client,
retain that exact fork result and drain both pipes across the five-second
window. This addresses the ownership mismatch between a native Android parent
and the Mac coordinator's Python child: an Android parent cannot `waitpid` a Mac
Popen. The new component owns a separate Android-side `am instrument` client.
The existing Mac coordinator and saved-UI driver are unchanged and have not yet
been connected to this component. No production activation is available.

`helper_owner_read_gate.c` creates its own lifecycle/owner/query objects. The
independent same-process callback must qualify the current finite phase and
exact owner pointer before native App/helper package reads. Missing callbacks,
unknown reads, later packages or query timeouts retain the actual objects.
Requests, JSON, a nonce, a Plan, an account and native package metadata do not
supply operator, source, server admission or current Attempt permission.

`helper_owner_native_driver.c` creates the read-gate object itself and accepts
no adopted owner, PID, old scope or serialized driver receipt. After a naturally
completed install and fresh independent driver qualification plus native package
reads, it forks one fixed user0 instrumentation client. The arguments select
M1 LAN, media-only, normal saved UI, V50 preset, one window1..10s/default5s,
PCM/startup/stage diagnostics OFF, lead0, source input OFF and no reconnect.
The matching helper retains its normal route/account/nonempty restore gate.
No password or store is read by this component. The production child receives
only fixed PATH/LANG, `/dev/null` stdin and its two owned output pipes; all other
inherited FDs, including high FDs and owner/control handles, are closed in the
child. The retained native parent keeps those actual owner/control objects.

Each poll/phase remains at most3s. The driver spans multiple polls and keeps the
constructor's original30..3600s process ceiling, including upload/install time.
An earlier driver deadline reserves8s first-media allowance, the selected window
and8s close margin from driver start; preparation counts inside it. Insufficient
time fails instead of extending a deadline. A slow fork is only cooperatively
checked before/after: failure after fork still retains its actual child/pipes and
possible-start flag. Timeout/cancel/interrupt does not signal the driver. A live
supervisor must keep pumping retained objects; process exit does not preserve an
OS reservation. No unknown destructor, uninstall/retire wrapper or release path
is added.

Only the actual sole child's natural0, both EOFs, bounded retained output and
one final instrumentation code can establish local client closure. Overflow,
stderr, nonzero, missing/duplicate footer and inherited open output pipe reject.
The numeric-result payload is opaque here; this is not App-report validation.
`DRIVER_DONE` additionally requires the independent current captured Attempt,
receiver/Surface, codec/audio/input cleanup callback and fresh native package
reads. All trailing control readiness, including queued later requests or EOF,
rejects. A valid local footer cannot grant those gates or prove Android PM/App
server quiescence. Single-threaded exclusive wait/default SIGCHLD is required;
the component has no driver signal, PID setter or adoption method.

An independent narrow review found a real host counterexample after the initial
119 affected checks passed46.714s. If the first driver collection happened after
its deadline, an already exited child with both EOFs could take the successful
branch before the deadline check. A new actual five-second host child fixture
shortened only its own deadline, first collected after expiry and reproduced
the mistaken acceptance:1 check/1 failure7.247s. The component now checks the
original driver/process ceilings before every successful closure and bounds
`DRIVER_DONE` by that same driver deadline. Expired/unknown objects can still be
drained and reaped, but cannot become qualified. This was a host candidate bug;
the candidate had not been staged or executed on Android.

The final28 new checks passed32.223s:20 native-driver checks plus8 previously
private same-process read-gate checks now exercised from canonical source.
Two final constructor-refusal/high-FD checks also passed1.667s. The refusal
fixture deliberately lets its actual child exit before writing the payload;
BrokenPipe remains a local transport outcome, and the same child's closed
refusal record plus both EOFs must still be collected.
The92 unchanged owner/channel/lifecycle/idle/readonly checks passed in the
preceding119-check run. Host scope FDs, PM stand-ins, child creation/wait, pipe
drains, real five-second waits, inherited high-FD closure and cancellation/late
collection are actual host work. Android output, operator/server/Attempt/normal
cleanup callbacks remain synthetic. Private exploratory19 checks25.240s and
their earlier17 checks19.453s are preserved; they do not validate the corrected
canonical deadline branch or real Android execution.

The final exact canonical sources compiled with existing NDK29/API30 into an
arm64 ELF50040B/SHA578d54c76270eeb3707ee5740f960c534aba9415718e3a7747ef0006b0677a0b.
All fixture macros were OFF, compiler and seven actual input hashes recorded.
The host build of the production CLI rejected activation arguments; the Android
ELF was never executed. This is compile-only evidence:
no Android staging, query, PM, instrumentation, authentication, media or input
occurred. The earlier private49712B and pre-review canonical49720B artifacts
remain separate and do not back this corrected source.

This round's single OP12 availability read was device-not-found; process/helper
absence and installed bytes remain unknown. Eight bounded readonly host clients
were reaped with0 signals: original26875/start/source215a/runtimeb708, full four
roles0, formal TCP15556/15558 clear, dedicated45560/45963 unoccupied. These are
snapshots, not permission or leases. A separate bounded M1 guest read bracketed
fresh Morphe PID3534/UID10235/start1913 and exact cmdline. It found no accepted
active target media session; focus and actual video format/geometry remain
unknown. This does not establish playing, motion or FPS and does not reuse the
invalid old3470 identity. All nine readonly guest clients were reaped with0
signals and no source input.

NewAppf97b/JNI447f and helper28db remain local, uninstalled/unpublished. Prior
installed22f/d043/JNI4bf, publicalpha8/code39 and stable1.31/code32 remain separate.
The first real phone return still uses unchanged reviewed f0/d043/frame-only;
this new component cannot substitute its helper or pins. UDP defaults, native
and Java ring/report bounds, original five-field admission/release guard, NPS,
Headscale, CPU, guest data and M5 sessions are unchanged.

Next bind an actual retained host controller to this native-owned client with
closed phase scheduling. Keep host operator/source/phone/server admission in
the actual host supervisor and native package/owner/driver qualification in
the native process; no host JSON boolean becomes a C callback or authority.
Normal saved-LAN credentials and actual current Attempt/codec/audio/input,
matching package/absence, privileged-writer exclusion and same-created-FD
retirement still require concrete independent qualification. The Mac controller
owns only its actual transport Popen, and cannot adopt a remote PID or infer
remote quiescence from local EOF. Only the existing complete gateway/reaper/
roles/original/formal plus independent phone/helper/input/scope boundary can
project the original five fields and explicitly release. Partial activation is
still prohibited. No new App/helper/JNI release or performance claim is made.

Exact sourcee3a7bd15d111d35bcf76900c93fab5d26b89838e/run37226978314 was
independently read back completed/overall/build/UDP success. Linux2161 tests
passed107.661s with11 existing skips. This validates this source and synthetic/
host layers only; no Android deployment or phone acceptance followed. Do not
poll this accepted run again or use it for a later SHA. This cloud appendix is
local for the next meaningful source push, not another doc-only CI.
