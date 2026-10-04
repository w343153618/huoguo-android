# Active-source snapshot candidate: build and inert deployment, not live sampling

The explicit standalone legacy runner candidate is implemented. It has not been
run against an active source UI. The actual installed-platform idle-wait audit
is in `source-active-snapshot-design-20261004.md`; the old final NOTimplemented
sentence describes that earlier stage. There is no new phone performance result
or App release in this iteration.

`experiments/moonlight-v2/source-snapshot/SourceSnapshot.java` only calls legacy
`UiDevice.dumpWindowHierarchy` after rejecting aliases, existing files, foreign
UID/mode and an invalid closed relative namespace. It does not call input, menus,
watchers, idle-timeout setters, source-App attachment or restart. The caller
creates an unpredictable owned0700 directory under `/data/local/tmp`; the file
must be regular0600, one link and strictly under1MiB. A closed numeric receipt
contains runner PID/UID/start ticks, parent/file device/inode and byte count.
That receipt proves neither source identity nor runner exit.

`scripts/probes/source_snapshot_reader.py` is an explicit factory adapter for
the existing source Stats/native target collectors. It never deploys the JAR,
authenticates, reads credentials or sends input. Its trusted caller must hold
source admission or its current authenticated App guest lease before opening a
UiAutomation session. The program's exact owned directory/file metadata and SHA
are checked before and after runner execution. Default collector/driver behavior
has not changed; no new CLI or HTTP setting enables this reader automatically.

The adapter requires bounded final one-test runner success and serialization
receipt, then queries the receipt's exact PID/start. Missing process or a different
start proves only that prior runner is gone. An unreadable stat is a failure,
not an absent process. After matching actual file metadata, it removes only three
closed file names and the owned directory. Directory identity excludes its
changing size/link count. Metadata shell checks are conservative observations,
not an atomic filesystem transaction. Source identity/focus/MediaSession owner,
state and physical dimensions/rotation still use the existing independent bracket.

On timeout or unconfirmed remote completion, no trap or old collector fallback
deletes the possible live runner's directory. Closed UI-command fields report
serialization/runner-exit status and possible retained scope. The trusted caller
must keep the private scope journal and finish actual remote quiescence before
releasing its owned protection; a local ADB reap alone is insufficient. The
library deliberately does not guess a remote PID, send a signal or extend a
timeout to conceal that failure. Actual failure/cleanup behavior on the guest
remains a live gate.

The existing3s per-command,15s collector and1MiB per-read/4MiB total limits remain.
Raw XML and framework log stay in transient bounded buffers. Only the previous
closed Stats/target fields and numeric completion flags are exported. Legacy
runner setup, accessibility suppression, root retrieval and serialization can
still affect the source or exceed the deadline despite removing the CLI's explicit
idle wait. Costs and side effects have not yet been measured.

Validation completed:

-57 focused checks, including actual SDK37 compilation and JVM closed-path
  execution, complete source bracket integration, live/reused/unreadable runner
  fixtures, foreign metadata, timeout retention and removal failure boundaries.
  Initial integrated fixture ordering was wrong at the final identity/size read;
  it was corrected, with all57 checks subsequently passing.
-Actual existing JDK21/SDK37/build-tools36 compile and DEX build produced a5888B
  standalone JAR, SHA
  `a19c50f5e21803958bb059e000f05152e572421166fd826f3d91328981ed903f`.
  Build source/dependency pins and JAR are in a restricted private directory;
  this is not a signed APK and no artifact is committed.
-Three bounded scoped file-staging ADB operations created one owned0700 directory
  and verified this exact0600 JAR on M1. All local staging commands reaped. No
  `runtest`, UiAutomation session, source input, App authentication or media began.
  The staged namespace/inode journal is referenced by the private iteration state.
  It is intentionally retained for the next qualified reader; rollback is limited
  to that exact owned JAR/directory after confirming no reader still uses it.
-Independent readonly postcheck retained original gateway26875/start/source215a/
  runtimeb708/four roles zero. Phone target/helper absent; exact source3470/10235/
  start1952 still playing. This state readback is not a new format, videoID or
  motion qualifier. M5, NPS, phone settings and App artifacts were not changed.

Next use a NEW explicitly frozen saved-UI owner/current-Attempt reader qualification,
or full protected source-only admission. Do not start this UI session solely from
a socket snapshot. Requalify a fresh native Pause target from the currently-playing
source and independently confirm its authenticated transition before preparing
the next moving-video window. Do not reuse paused-first scripts or historical
coordinates. The existing80ms/FIFO/session-FPS/default flags remain.
