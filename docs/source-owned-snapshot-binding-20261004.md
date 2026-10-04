# Owned source snapshot receipt binding — offline candidate

The new inert `scripts/probes/source_owned_snapshot_binding.py` compares the
native parent's started/waited rows with the Java completed-serialization row.
It requires the caller's expected parent PID/start, exact child PID/start/UID,
the same directory device/inode, a reported natural zero exit, complete runner
success output, and matching XML inode/size. All five private, regular,
single-link files are checked: started, waited, completed, runner.log, window.xml.
Foreign, missing, aliased, oversized, failed, cancelled or incomplete evidence
is rejected. It performs no file reads, process operations, ADB calls or input.

This binds supplied observations for consistency. A caller can fabricate all
of them; therefore valid rows do not establish actual execution ownership,
permission, source qualification, remote UI-service quiescence, or scope
removal. These result fields remain false. It does not parse source XML or
promote an observed Java serialization to an accepted source/Stats/target.
Current runtime/adapter behavior, deadlines and production defaults are unchanged.

Six new offline checks plus existing actual host-owned runner and snapshot
protocol checks passed: 32 checks / 3.706 seconds. This is not an Android
runner execution, real phone session, performance or sampling-overhead result.
No APK/JAR/binary was changed by this Python binding candidate.

The immediately preceding ART projection source
5e3fad61969284604cf7cad9f7cf54b7650e3c5e has its own verified GitHub run
37184071128: overall, build and udp_candidate all completed successfully.
That result does not cover this subsequent binding source commit.

Next: integrate the five-file lifecycle into a separately frozen, default-OFF
owned adapter with a private possibly-created scope journal and actual pinned
execution. Failure must retain exact scope and unknown status; three-file old
cleanup cannot delete the new five-file namespace. Actual remote runner exit,
framework cleanup and current authenticated App lease remain separate gates
before any new source-input or moving-video public campaign. Read the current
M1 source identity after maintenance; old PID3470/format evidence is historical.
