# M1 non-input authenticated frame observation candidate

The default-OFF test helper now has a distinct `frame-only` phase. It can keep
one normal saved-UI M1 public App Attempt running while a bounded external
observer reads a framebuffer. It starts no UiAutomation runner and sends no
touch, key or source-App command. This separates source pixels from the older
accessibility snapshot route whose failed remote retirement is still gated.
The published App, installed code40 App and existing driver defaults are unchanged.

The helper opt-in remains M1 / nps_owner / saved-UI / media-only. It starts through
the normal App UI, requires authenticated media and reads the actual receiver
tuple, then captures the current Attempt, generation, receiver, native control,
valid Surface and geometry on the main thread under the existing monitors.
It publishes the existing private phase1 ready marker. The only accepted command
is `READ <nonce>\n`, at most32 bytes; coordinate/key commands, wrong nonce,
multiple commands or another phase are rejected. There is no retry.

Before acknowledging the request and again after the external confirmation,
the helper checks the same captured Attempt and Surface. Replacement or
cancellation fails the phase. It does not cancel a later Attempt. The external
read occurs outside the UI/Attempt/touch locks: these checks are a bounded
bracket, not an atomic hold or an independent server-lease proof. The helper
does not verify remote pixels, source identity, playback or format. Its existing
bounded marker waits are unchanged. After a successful phase it leaves through
the normal exit confirmation and existing report/audio cleanup, with no steady
marker, sampler or reconnect. That lifecycle has not been executed for this mode.

`OwnerSourceFrame` has no input, network, device or cancellation interface.
It preserves primary publication/confirmation failures and publishes a copy of
the immutable expected nonce body. Marker reads also require a private regular
single-link descriptor in addition to the existing no-follow/UID/inode checks.
The normal native Play/Pause transaction remains separate.

The standalone Python coordinator publishes the non-input request, waits for
the exact dispatched nonce, then calls an injected observer once. It requires
a closed numeric/hash projection, one unary RPC, zero streams, channel close,
matching image aspect, a12MiB PNG bound and the existing6-second phase budget.
Raw pixels, arbitrary keys and promoted authority are rejected. Publication
ambiguity, observation error, clock reversal or timeout become sticky failure;
no verified marker or retry follows. A later helper report must match the nonce
and all typed before/after checks without input or steady fields. Valid reports
still do not establish execution ownership, server lease, source identity or FPS.
The actual gRPC reader and runtime driver are not integrated yet.

Fourteen new JVM/coordinator checks plus existing affected source/driver checks
passed:69 checks/6.755s, including the actual full API37 Java compilation fixture.
The final typed-helper schema tightening passed its7 targeted checks/0.003s.
Cases cover owner replacement, malformed/replayed commands, foreign confirmation,
publication Error, interruption, observer failure, clock bounds, input/steady
contamination and raw-data/authority promotion. Remote marker reads are fake;
Java fixtures exercise the actual helper-only class. There was no device UI,
input, gRPC screenshot, phone session or performance measurement.

An actual javac/D8/link/align/sign/verify build passed in6.816s against the existing
exact106 frozen22f/code40 App classes, all unchanged before/after. The new private
helper is82323 bytes, SHA256
`82f4614679e828bdd2738838270a97f188bed11dc19d04ecd2dc2fa0203968d7`, original
signer `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`.
It is not installed. The earlier `b1667b18` build preceded the immutable-body
change and is historical, not the final artifact. The builder now accepts an
explicit compiled-App directory so helper rebuilds do not alter that App.
APK paths and detailed class pins remain private, not in Git.

Next integrate one independently pinned, loopback-only, proxy-disabled unary
gRPC reader and this phase into a new bounded saved-UI coordinator. Obtain a
fresh post-maintenance emulator/source context; do not reuse PID3470, cached
coordinates or BBB299. Before real execution, recheck phone/artifact/formal
availability and actual current-App provenance. This mode is source observation
only, not source control or moving-video acceptance. No UiAutomation process is
started, so it must not pretend to complete the old failed-retirement gate.
Keep normal80ms/FIFO/sessionFPS/lead0/off defaults and all domestic/NPS protections.

Exact preceding2607f10/run37189631859 is independently completed with overall,
build and udp_candidate success. This later candidate still needs its own CI.
