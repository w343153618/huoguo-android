# Explicit M1 LAN native-event selection candidate

The registry and worker now accept a trusted local startup selection for a
separate bounded M1 LAN diagnostic. The default is `None`; ordinary LAN,
Tailnet, NPS and persistent gateways retain their existing descriptor value
`diagnostic_events=false`. No CLI, environment variable or HTTP field enables
this candidate. No coordinator has been run, and no phone artifact is installed
or released by this source change.

The selection uses the prior closed `Plan` in
`scripts/probes/owner_native_diagnostic_preflight.py`. It binds the M1 emulator
`emulator-5556` / `RemoteAndroid17Compare`, LAN media port45963, original signer,
and exact expected experiment App/helper/JNI/package bytes. This is expected
metadata only. It does not establish fresh device reads, operator permission,
private evidence, current App Attempt, server guest lease or actual admission.
The planned HTTPS45560 endpoint still needs the separate coordinator.

`owner_native_diagnostic_policy.py` selects only30FPS/max_size960/4M VBR/80ms,
lead0, audio and touch. Requested session seconds are shortened to the smaller
of the existing1–120 request and the Plan's1–30 sample ceiling. Guest backend
bindings are passed to the owned factory and are absent from the App descriptor.
Only the existing huoguo/wyw credential closure is accepted for this candidate;
neither account identifies the operator. Changed media settings are refused
before the factory. The worker must receive the same trusted Plan explicitly,
with exact guest/descriptor bindings, default FIFO/sessionFPS, no host raw trace,
raw-budget override or ENOBUFS retry. A descriptor requesting events without that
worker selection fails before capability files, sockets or background resources.

An explicit registry construction anchors a process lease ceiling on its existing
Python monotonic clock. READY and active leases cannot extend beyond it. Existing
reap revokes expired ownership and retains factory/start/stop responsibility;
the existing shutdown barrier still fails on incomplete or failed cleanup.
This is an admission and lease bound. A future finite process supervisor must
still call reap, own its actual Popen, close and wait, and prove actual children
quiescent before releasing the continuous LAN reserve. Descriptor seconds start
at READY, before first video; a30-second descriptor does not prove30 seconds of
sampler coverage. Startup and an earlier absolute deadline can shorten it.

The existing App flag also enables its Java Inbox event history. The native256
and Java8192 event rings,64 remaining-prefix numeric rows,64KiB whole-report
rejection, RX/drain/80ms assembly and cleanup algorithms are unchanged. This
candidate does not force host raw tracing or packetizer events. App stage,
startup and PCM queue preferences need separate actual helper readback. No
overhead or ART/JNI report acceptance has been measured; missing/OFF history is
unavailable, not zero faults. Expected artifacts remain the uninstalled exact3d
App f97b3e37/JNI447f4928/helper ec805aed. Prior installed22f d043/JNI4bf and the
unchanged reviewed f0 frame-only freeze stay separate.

Fourteen new checks and232 affected checks passed in0.600s. Validation includes
in-memory descriptor/default/scoping tests, fake worker resources, cleanup
failure, and an actual host thread held inside an owned pending factory while
expiry and shutdown race. That reservation remained unreleased until the
factory returned and its one owned stop completed. The initial fixture used
the invalid existing `close_and_wait(0)` argument; after correcting it to0.01,
the complete focused set passed. The earlier failed invocation is not accepted
as a passing run or an App failure. No full-repository test was repeated.

Dedicated OP12 still returned device-not-found on this turn's single get-state.
There was no helper installation, phone authentication, media, source input,
UiAutomation, framebuffer RPC or service signal. The independently read exact
preceding2e8b97a/run37196597375 finished overall/build/UDPsuccess; that CI result
does not apply to this later selection source. Exact hashes and evidence limits
are in `owner-native-diagnostic-selection-candidate-20261004.json`.

Next implement the independent finite LAN coordinator and trusted worker factory
using a NEW full source freeze. It must preserve continuous45965 reserve,
authenticated live original-registry idle witness, full roles/formal checks,
mark before Popen and actual owned quiescence before release. The ordinary public
worker must not be sampled under that reserve. When the phone becomes available,
first fresh-qualify the unchanged reviewed f0/d043 frame-only candidate; this
selection alone is never permission to upgrade or collect native events.
