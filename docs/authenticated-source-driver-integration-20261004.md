# Authenticated owner source-driver integration

Scope: default-off M1 ownerpublic test tooling, not a new App release or a
performance result. Public alpha8/code39 and installed owner code40/d043 remain
distinct. The final matching a575 helper is still uninstalled at this checkpoint.

`run_authenticated_lan_ui.py --source-input native` now accepts only explicit
media-only M1/nps_owner/saved-UI experiments with an exact source PID/UID/start
tuple. It pins the installed helper and App bytes before instrumentation and
rechecks target-process absence after that preflight. Phase1 waits for the
matching helper's captured Attempt/geometry/nonce, obtains a fresh source native
Play target from the same qualified Stats snapshot, publishes one bounded
numeric command, and independently reads playing state before starting samplers.
Phase2 requires both actual sampler Popens to finish, obtains a fresh native
Pause target, independently verifies paused state and endpoint format, and
confirms the matching nonce. No guest input command, credential-store read,
cached coordinate, host control lease or retry fallback is used.

Readonly source probes bracket identity/focus/MediaSession, actual effective
display dimensions and display0 rotation0. These are brackets rather than an
atomic guest-input lock. Actual dispatch ownership belongs to the existing
matching helper's main-thread checks of Attempt, generation, receiver, native
touch sender and Surface. Local DOWN/UP receipts and independent source state
are required separately; a nonce echo alone cannot establish playback.

The driver reads only eight closed test-marker names. Numeric publication uses
an unpredictable owned0600 inode followed by a non-replacing hard link; cleanup
checks the actual inode and retains foreign replacements. The shell metadata
checks are not atomic filesystem ownership. On native qualification failure,
the driver waits for the helper's bounded cleanup and reaps its local
instrumentation client; it does not force-stop a potentially newer App session.
The existing native-off behavior is unchanged.

111 focused checks passed in6.201s, including the existing API37 full-helper
compile and actual JVM fixtures. Two additional fixtures verify main-driver
failure cleanup without force-stop/samplers and fatal restorecon failure with
already-owned empty-inode cleanup; targeted29 checks passed in0.011s. This is
113 distinct focused checks, not a claim that a new full-repository suite ran.

Actual rooted owner-phone test-marker filesystem validation passed in2.796s:
installed App d043, named markers initially absent, non-replacing publication,
same UID/regular0600 inode brackets, exact bounded payload readback, and tracked
marker cleanup. No helper installation, touch, guest control, media or service
operation occurred. The first fixture failed because Android `restorecon`
returned0 while writing SELinux context-loading diagnostics to stderr. The
fixed implementation accepts the creation receipt before labeling, suppresses
only that command's diagnostics while requiring exit0, and rechecks the same
inode. The initial untracked empty stage was conservatively retained and is
registered privately; the later tracked-cleanup receipt does not cover it.

Next: NEW pinned coordinator/freeze, safe saved-UI M1 public authenticated native
play→30s samplers→fresh native pause, actual helper cleanup and original-service
readback. Playing UI timeout or hidden controls remain explicit failures; do
not extend deadlines, reuse historical coordinates or send guest ADB controls.
Default80ms/FIFO/sessionFPS/lead0, M5 daily sessions and all NPS identities remain.
Actual phone/public/V50, outer routing, optical/audio timing and friend isolation
acceptance are separate pending gates. Numeric evidence is in the adjacent JSON.
