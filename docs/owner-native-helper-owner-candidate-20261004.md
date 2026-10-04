# M1 native helper owner candidate — 2026-10-04

Default OFF. A partial native library now creates its own protected scope,
accepts a finite pinned APK upload through its own file descriptor, closes that
writer, and waits only for its actual fork child. Sixteen new actual host checks
pass within 77 affected checks (9.422s). A separate locked NDK29/API30 arm64
build succeeds. This is host ownership and Android compile evidence; no Android
stage, PM, ART, authentication, media, source input or UI session ran.

The NEW scope is `/data/local/huoguo-helper-owner-<24-lowercase-hex>/stage/owned.apk`.
Its parent and stage stay root:root0700 and its APK root:root0600 from creation.
The captured existing root/nonowner-nonwritable base is never modified. This
avoids granting shell ownership merely to upload the file and then attempting to
revoke writable FDs. The earlier shell-owned flat tmp scope, root:shell0710
finalizer and private protected metadata contract are preserved and incompatible.
They cannot retire this new layout. Matching same-owner retirement is next.

Creation is exclusive and never adopts an existing path. No-follow FDs and
named-node brackets require captured dev/inode, UID/GID, mode, link count and
closed entries. Refresh after this process's one new child/file cannot adopt
changed identity or permissions. A narrow fixture initially refused APFS because
a regular-file entry increments directory nlink from2 to3; Linux commonly keeps
that count. The corrected one-entry creation bracket allows only unchanged or
plus-one nlink and still checks exact entries. Eleven follow-on host fixture
failures were preserved and corrected before the final pass; no device or App
failed. An enumeration error always closes its duplicated directory FD.

Upload requires exact length and actual EOF, with a cooperative1500ms budget.
The native parent creates the sole write FD, neither duplicates nor forks with
it, then closes it before independent readonly hashing and metadata readback.
Truncated, extra, wrong-hash or no-EOF input keeps scope uncertainty and cannot
start a child. Competing privileged actors remain an independent trusted-caller
boundary; hashing or modes do not make an atomic content hold. Filesystem calls
and process creation are not guaranteed hard wallclock operations.

The only compiled production child command is fixed `/system/bin/pm install
--user 0 -r` for the exact pinned helper path and bytes. It is not executable
through this partial Android CLI. The parent requires default SIGCHLD with no
auto-reap and must be single-threaded and the child's exclusive waiter. Only its
actual unreaped fork child may receive bounded TERM/KILL; no process group,
ps-discovered PID, JSON PID adoption or arbitrary command exists. Cancellation
before fork refuses. Ignored TERM, parent cancellation, child failure, overflow
and inherited pipe writers are actual host fixtures. Signals, child exit and
pipe EOF are distinct; forced/nonzero/missing-EOF outcomes cannot pass. Even
natural `Success`/exit0/EOF is a PM client result, not proof that Android's PM
server, helper package, App or lease is quiescent.

The library retains FDs on an unknown unreaped child, and its production close
path refuses every possible created scope until matching retirement exists.
The Android executable is inert without arguments and rejects all activation
arguments. A complete isolated environment/operator/source/phone/admission
supervisor plus owned helper install/uninstall, driver, package, input and scope
cleanup is still required. No valid JSON, receipt or local ADB success can grant
that authority or release the existing admission reservation.

The final Android build is19976bytes, SHA
`a0435aed5b8bb5cc6dc075f0451c0345f64e281714a078c95db8e7238b2ec27d`;
its actual source/header/compiler pins and earlier build are preserved privately.
It was not staged or executed. No App, helper APK, JNI, production default or
release manifest changed. One phone availability query returned device-not-found;
there was no repoll. On actual return, the unchanged reviewed f0/d043/helper82f
normal saved-UI/current-Attempt frame-only qualification remains first.

The preceding bc4/run37212308330 independently passed overall/build/UDP with
2041 Linux tests/85.523s/11 existing skips. It cannot cover this new source.
Read the new SHA's own CI after the meaningful source push, and continue with
matching same-object root-only retirement and bounded fixed PM/helper lifecycle;
never execute a partial live installer.

Exact dca400bec225b5637836a31fb30339f90d392521/run37215217607 is now independently
completed/overall/build/UDPsuccess; actual Linux2057tests/93.735s/11existing skips.
No rerun or old green. This cloud append is local pending the next meaningful
source push and does not cover the private follow-on.

NEW private matching owner-object pre-PM retirement passes11actual host checks
(1.134s; preceding pass1.491s preserved). It removes only the verified upload
scope owned by that actual process's held FDs; no old receipt/PID adoption. Any
PM child ever started blocks this pre-PM path even with natural0/EOF. Wrong mode,
changed contents/replaced nodes/unknown upload/partial foreign entries retain;
a later scope at the same name cannot be reclaimed. All Android PM/permission/
lease/release claims remain false, and old contracts remain incompatible. No
Android binary was built or run for this private follow-on. Fixed uninstall and
full driver/package/helper cleanup still require implementation. The current
upload consumes stdin to EOF, so a NEW bounded native-owned control transport is
needed before later driver/uninstall phases; closed stdin cannot carry commands.
Do not use JSON/PIDs or local ADB EOF to bridge that gap or execute partial live
code. Actual Android DAC/SELinux/PM and full supervisor qualification remain open.
