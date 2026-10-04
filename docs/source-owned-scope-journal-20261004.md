# Private possible-scope journal — local candidate

`scripts/probes/source_owned_scope_journal.py` has no I/O on construction.
Explicit registration writes a closed numeric/namespace record to a caller's
canonical UID-owned0700 private directory. The0600 single-link record is
created exclusively, file-fsynced and directory-fsynced before a ticket is
returned. The caller must successfully register and recheck that exact local
ticket before attempting a remote command. Name/inode replacement, unsafe
permissions, aliases, hard links, collisions and unconfirmed persistence fail.
Failed writes remain present; this component does not retry by overwriting or
delete a possibly-created scope. The directory is limited to128 records and
each record to2048 bytes, with bounded inventory/read loops.

A separate append-only observation accepts only the closed scalar output of
the C/Java binding. All actual ownership, UI quiescence, scope removal, source
qualification and permission fields must remain false. Raw XML, logs, passwords
and arbitrary report fields are not accepted. Even a successful observation
keeps `remote_scope_may_exist=true`; it is not a cleanup or release receipt.
There is no PID lookup, process signal, remote execution or deletion API.

Nine new actual **local filesystem** checks cover registration/permissions,
file and directory synchronization failure, collision/symlink handling,
mutations/aliases/hard links, bounded capacity and forbidden authority/secret
fields. Together with C parent, snapshot reader and binding checks:
43 PASS /2.942 seconds. An initial concrete `Path` factory-type check rejected
all eight local fixtures; it was corrected before acceptance. This was a new
source-candidate issue, not a live App/service error. No Android runner or
phone/media session was started, and no APK/JAR/binary changed.

Preceding binding commit cb258b8ab7cff295dfd67ccf116e2a489137dbbb has its own
verified run37184629915: overall/build/udp_candidate completed successfully.
This does not attest this subsequent journal source or an actual remote UI run.

Next integrate this journal and the five-file binding into a separately frozen
owned snapshot adapter. The adapter must independently associate the pinned
remote exec parent with its real command, retain exact scope before possible
creation, preserve failed/unknown remote lifecycle status, and avoid the old
three-file cleanup. A local ADB reap is not an actual remote parent wait; a
main-child wait is not framework/UI-service quiescence. Only current M1 source
identity and current authenticated App lease or full source-only admission can
qualify a later real readonly snapshot. Production settings remain unchanged.
