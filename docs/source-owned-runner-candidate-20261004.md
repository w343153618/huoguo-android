# Default-off owned source snapshot launcher candidate

This iteration implements the narrow failure-lifetime candidate from the prior
source Pause/direct-reader design. It does not run a UI automation session,
change the source App, or alter installed/published App artifacts. The user's
M1-primary instruction remains the scope for future device qualification.

The native parent forks exactly one closed child and persists its own and the
child's actual PID/start identity before releasing the exec gate. It uses
single-threaded waitpid ownership; until reaping, even a zombie retains its PID.
Signal handlers only set a flag. Cancellation/deadline handling signals only
that actual unreaped child. It never borrows a process from ps/a prior file,
uses a group kill or accepts arbitrary commands/paths. Android builds exec the
fixed uiautomator snapshot test with closed owned relative namespaces. Linux
parent-death handling is distinct from child/framework descendant acceptance.

Numeric started/waited receipts and output are bounded, no-follow/owned-mode
checked, and retain the exact scope after failure. Missing wait, retained pipe,
failed clock, cancellation, TERM/KILL, nonzero exec and serialization limits are
not natural success. Receipt parsing is inert: valid content alone establishes
neither execution ownership nor an App guest lease.

Actual host C fixtures cover natural0/7/exec127, deadline TERM, ignored TERM
then KILL, parent cancellation, bounded output, failed clock, a descendant
retaining the pipe, rejected/symlink scopes and an unrelated process remaining
unaffected.13 checks passed;28 focused snapshot/reader/UI-preference checks
passed in3.592s. These are host fixtures, not Android UI or phone acceptance.
The separate Android arm64/API30 build used the existing locked NDK29.0.14206865
without the host-fixture macro, producing a14976-byte private binary with the
hashes in the JSON record. The binary has not been staged or executed.

Before integrating/running it, retain exact binary/file/scope binding, the
actual parent exit/wait journal, Java serialization identity and the existing
current-App permission lease/source admission. Verify the trusted Android ART
environment rather than guessing runner setup. The UI3s/source-phase6s/
collector15s and1MiB/4MiB limits remain. A main-child wait and local ADB reap
cannot be called whole UiAutomation-service/descendant quiescence. This candidate
is not a solution to the public UDP stall, a performance result or a new APK.

The final focused run initially exposed an output-fixture assumption: the
actual child was reaped after overflow, but remaining output did not reach EOF
within the unchanged drain deadline on the loaded host. The fixture now checks
the actual receipt: code73 only for EOF/no ownership error, otherwise stricter
code72. Both retain overflow reason3, the8191-byte cap and failure status.
Production limits and cleanup remain unchanged; no check or platform skip was
removed. The corrected focused run passed.
