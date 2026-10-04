# Authenticated source-input helper and read-only target preflight

The test helper now has a default-off native source-input path bound to its own
authenticated App Attempt. A matching original-signer helper built against the
frozen22f/code40 classes passed Java, DEX and signature verification. It has **not
been installed or used to send input**. No App APK, public manifest, gateway,
guest setting, M5 or NPS service changed. This is a helper/observer preflight,
not playback performance acceptance. [Selected evidence](authenticated-source-input-preflight-20261004.json)
contains the actual source and artifact hashes; restricted artifacts remain
outside Git and their paths are in the private iteration checkpoint.

## Implemented boundary

`OwnerSourceTap.java` executes one DOWN/UP transaction through caller-supplied
native dispatch hooks. It validates phase, per-phase nonce, bounded integer
coordinates and source/image aspect. It maps through the actual displayed image
rectangle. A35ms tap interval runs outside the UI, Attempt and touch monitors.
Interrupted or partially failed gestures attempt CANCEL only while the same
owner remains; replacement owners receive neither UP nor CANCEL. Cleanup failure
is suppressed onto the original exception instead of replacing it.

The `LanUiAcceptance` adapter accepts `source_input=native` only with explicit
M1 public owner scope, saved-UI authentication and media-only testing; its default
is off. Main-thread dispatch holds the existing UI and native-touch monitors,
rechecks the captured Attempt/generations/receiver/Surface/geometry, requires an
empty contact set before DOWN and observes the existing touch listener's event
counter/contact state after dispatch. It does not replace a sender or create a
media key. Local contact observations do not prove a guest playback transition.

Each source phase exports bounded numeric ready metadata and waits at most10s/
251 polls for a same-nonce command, then for an external-observer confirmation.
The command is an exact closed ASCII six-integer row, <=128bytes. Actual descriptor
checks require no-follow regular0600 target-UID files, bounded size and unchanged
inode before use. Ready/dispatched markers belong to the helper; the driver must
track and clean its own command/verification files. Failed helper-owned marker
cleanup makes the result fail. No observer/driver integration currently exists,
so enabling this opt-in without that driver would safely time out.

## Same-snapshot target collector and actual readback

`source_remote_observation.py` explicitly wraps the existing bounded collector.
It extracts a target from the same owned UI pipe before raw bytes are cleared,
without a second UI query or host XML file. Existing PID/cmdline/UID/start,
foreground and MediaSession checks must qualify independently; valid coordinates
cannot override a restarted or foreign source. It still declares authenticated
App Attempt verification, input execution and playback transition verification
false.

The first actual readback completed in2425.988ms, with exact source3470/UID10235/
start1952 and all local children reaped, but rejected the target. A bounded
boolean-only follow-up showed that the real Morphe button is an enabled/clickable
native **ImageView**, rather than the ImageButton assumed by the initial fixture.
That rejection is retained. The parser now permits only those two closed native
classes with the same exact resource, package, Play/Pause label and bounded
geometry; arbitrary Button/TextView/custom widgets still fail.

Final actual readback took2369.742ms/169211bytes, with the same source identity,
foreground/active owner and paused Stats format299avc1/1920x1080 descriptor60,
audio251Opus. The qualified target bounds were[456,348][624,516] in1080x1920,
normalized32768/14745, class code2. Owned UI removal and all local child reaping
completed. **These are historical coordinates, already unsuitable for a later
input without a new fresh qualification.** Descriptor60 is not observed FPS;
no motion, touch, phone decode, audio or network performance was accepted.

## Validation and next action

51 focused checks passed in1.561s:10 tap transaction/adapter checks,9 target
checks,6 same-snapshot collector checks,16 existing source-observer checks and10
existing callback/ownership checks. The actual Java transaction is executed in
the JVM fixtures; the Android adapter is compiled against the frozen22f106-class
ABI but has not been exercised on the phone. The final helper is78227bytes,
SHAa575abeda09383b247043fe5bb8c90c1d90229f7cd117ca22d6522f60bfe67b0.
The earlier165f717c helper build predates the cleanup-result fix and is kept
separately; do not install or attribute its SHA to the final source.

Next implement the explicit bounded ownerpublic driver and helper-pin handling,
collect a fresh paused target only after the owned attempt is ready, dispatch
through that attempt, independently confirm playing before real samplers and
pause through a freshly qualified control afterwards. The playing UI collector
has previously exceeded its3s command budget; failure must remain failure, with
owned cancellation/cleanup, without a stale coordinate, longer timeout, guest
ADB input or extracted source-control password. Keep original public lease
ownership; do not reserve45965 during original public media. Preserve80ms/FIFO/
sessionFPS and all prior release gates.

Exact preceding2ec2bf7/run37166743386 was independently read as overall/build/
udp_candidate success. The new helper/collector source revision needs its own
CI; that earlier success is not acceptance of this revision or a new APK.
