# M1 bounded unary frame reader and actual helper driver candidate

The explicit `frame-only` driver now connects the existing matching helper to
one authenticated emulator framebuffer read. It sends only `READ <nonce>` on
the private phase1 command, not a touch/key/source command, and starts no
UiAutomation runner. Defaults remain OFF. The normal App restores the saved
existing account, authenticates through public M1, receives media and captures
its current Attempt/receiver/Surface. The helper checks that same owner before
and after the external read. This is a bracket, not an atomic hold or an
independent server-lease proof; the standalone descriptor/JSON never grants
permission. The previous failed accessibility-retirement gate remains separate.

The reader constructor is inert. An explicit private0600/single-link deployment
descriptor binds the current emulator PID, UID501, Mac process start and argv
hash, plus a private0700 PNG destination. The live SDK process must be the exact
M1 AVD and grpc8556. Only the two closed SDK qemu executable paths and the
observed `-avd RemoteAndroid17Compare`/`@RemoteAndroid17Compare` forms are allowed.
The first narrow inventory assumed the non-headless form and found zero. A
readonly inventory identified the actual SDK headless/@AVD form; its numeric
process parser subsequently passed. No media was started for either read.

The client independently verifies both installed generated-protobuf file
hashes before executing their exact bytes, refuses preloaded replacements,
then uses the existing process-specific discovery token in memory. It connects
only to literal127.0.0.1:8556 with HTTP proxy and retries disabled, one unary
getScreenshot and zero streams. Ready wait is at most1s; RPC at most3s; the
whole observer retains the6s phase budget. Discovery bytes/inode and current
PID/UID/start/argv must match before/after. RPC/close exceptions export only a
fixed failure label, never transport details or credentials. The channel closes
before any PNG write. Failure is sticky, with no retry or verified marker.

The response must actually be RGBA8888/1080x1920 with the exact byte count. PNG
encoding preserves every row/pixel, without rotation/crop. Raw pixels remain
private. The destination is exclusive0600 in a held0700 directory descriptor;
existing files/symlinks are rejected and never overwritten. Inode, pathname,
size and final6s budget are checked. A failed write or final overrun may leave
that exact private artifact, explicitly recorded, and cannot confirm the phase.
The numeric report admits only dimensions, byte count, hash, timings, one RPC,
zero streams and channel close. It does not verify source-App identity, video
format, visual content, motion, FPS, latency or friend/public isolation.

The driver requires M1/nps_owner/media-only/saved-UI and the new82f46146 helper
pin against installed code40/d043 App bytes. It accepts a frame descriptor only
in that explicit mode, rejects cached Morphe/target identities and the old JAR
descriptor, and skips all SF/steady/reconnect paths even if an old marker is
present. Success needs the typed matching helper receipt, zero instrumentation
exit, normal first-session exit confirmation, actual network/profile readback,
zero audio threads and the actual accepted App audio-close report. On failure,
the bounded helper cancels only its captured Attempt; the driver does not
force-stop a later UI session. Cleanup still checks owned marker inodes.

Seventeen new checks cover real local private files, descriptor/duplicate/link
bounds, exact process forms, token-error redaction, one proxy-disabled fake RPC,
changed discovery/process/pixels, channel-close failure, existing destinations,
post-write timeout, exact PNG row/CRC roundtrip, mode/helper-pin isolation,
driver failure and actual audio-report rejection. Affected coordinator, marker,
driver, cleanup and actual API37/JVM fixtures total79 PASS/3.293s. Fake transport
and phone commands are not live permission, capture or media acceptance.

No Java/helper/App bytes changed in this iteration. The already built original
signer helper remains82f4614679e828bdd2738838270a97f188bed11dc19d04ecd2dc2fa0203968d7,
82323 bytes, uninstalled. Public alpha8/code39 and stable1.31/code32 remain
unchanged; installed private code40/d043 remains separate. No source input,
UiAutomation, gRPC, phone media, service signal or new APK release occurred.

External runtime paths are intentionally fixed: the installed generated proto
files under `/Users/wyw/Documents/ChatGPT/others/android-remote/m1-compare/hardware/proto`,
the SDK qemu binaries under `/Users/wyw/Library/Android/sdk/emulator/qemu/darwin-aarch64`,
and process-specific discovery under `/Users/wyw/Library/Caches/TemporaryItems/avd/running`.
Two proto hashes are locked in `scripts/probes/source_grpc_frame.py`; tokens,
deployment descriptor and PNG paths stay in restricted evidence outside Git.

Next execute a NEW frozen candidate after fresh phone/artifact/formal checks,
using the normal saved-UI App Attempt and one independently reviewed private
frame. No old source PID3470/BBB299, coordinates, source input, UiAutomation,
service replacement or UDP45965 reservation during original public media.
Default80ms/FIFO/sessionFPS/lead0/off and all domestic/NPS protections remain.
Exact preceding85619e49610196c093f937fa5a9934ea93f4d236/run37190843471 is
independently completed with overall/build/UDP success; this later source
requires its own CI.

The following actual local runtime check found no grpc/protobuf in the default
Python3.14.7 interpreter. An initial unguarded dependency-find command also
raised a missing-google import; it started no device operation. The existing
hardware venv, also Python3.14.7, successfully loads both pinned proto snapshots
with grpc1.84.0/protobuf7.36.2 and passes the actual explicit reader preflight.
That preflight opens no discovery/token/channel and makes zero RPCs. The driver
now performs it before instrumentation, so an unavailable dependency cannot
consume a phone Attempt while waiting for a guaranteed failing observer.
Two new dependency-failure checks plus69 affected checks passed0.084s; no Java
or signed helper changed after the prior79/API37/JVM run. No global packages or
import paths were changed. Real execution should use the existing qualified
`/Users/wyw/Documents/ChatGPT/others/android-remote/m1-compare/hardware/venv/bin/python`.

A fresh availability read preserved the original gateway identity and full
role-zero state, with formal TCP clear. Both phone process-absence probes were
unavailable; an independent exact device-state read confirmed that the dedicated
OP12 ADB device is currently not found. This is not evidence that an App or
helper process is absent, and did not authorize installation or instrumentation.
No helper, media or gRPC session was started. Next get fresh full pins and
actual absence when that dedicated device is available; do not reuse these
snapshots as permission or cached source/playback qualification.

Exact dependency-gate source f0e508f58aa667fb61017f7a7c4b5ea0a2224c5b/
run37192379073 is now independently completed with overall/build/UDP success.
The dedicated OP12 still reports device-not-found; there is no new helper,
media or framebuffer attempt. This CI result does not validate later source.

An independent root review of the prepared private controller found that its
execute-only import gate compared the frame-reader hash with the old source-
observation module's manifest key. The preparation dry run had never reached
that branch. The old controller and freeze remain unchanged. A NEW private
controller uses the correct key and separately checks the imported admission
module's frozen bytes. It also preserves the primary unavailable-device error:
unknown phone process/helper/input state cannot become a cleanup success.
Three execute-branch fixtures with entirely fake device boundaries passed0.091s;
actual local signature/frozen334 Python-byte checks passed. One initial fixture
mutated the inventory instead of the later import read and was corrected; that
first failing assertion is retained in the private review receipt. No device
installation, instrumentation, framebuffer, UiAutomation or media was executed.
Current candidate and receipt pointers stay in the private iteration-state.

The parallel source-only contract review found that the existing native frame
ring is256 records, with64 per drain and a Java8192 detail ring. Native event
collection is a separate descriptor diagnostic switch; enabling decoder-stage
metrics does not enable it. The current App numeric summary omits that detail
ring, so existing FEC poll deltas cannot supply frame identities or actual expiry
times. An80ms grant starts at the first successfully admitted mapping shard,
not the first rejected shard or a cross-host capture time. Quorum events mark
mathematical shard sufficiency, not logical delivery or codec readiness. These
are source facts, not a new live FEC observation, cause or performance result.
Keep defaults OFF and do not enable collection/promote buffering while the
fresh real phone/source qualification is unavailable.
