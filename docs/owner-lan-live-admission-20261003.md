# M1 owner admission and listener-only validation

The live original dd43 M1 registry has now been checked with the existing
huoguo account and its pinned local HTTPS certificate. The owner-only,
nonisolated round started no media and made no phone, M5 or NPS operation.
The fixed numeric record is in
[owner-lan-live-admission-20261003.json](owner-lan-live-admission-20261003.json).

The first read-only preflight falsely counted one hardware process group and
one packetizer: both matches were the original dispatcher PID26875, whose argv
contains the selected encoder and packetizer paths. It was stopped before any
authentication or reservation. The correction excludes only that dispatcher
after validating its PID, start identity and frozen source. Its descendants and
all other matching hardware/packetizer processes, including orphaned groups,
remain part of the gate. No positive count was simply forced to zero.

Two subsequent attempts verified exclusive loopbackUDP45965/interface binding,
then sent a normal bounded POST to HTTPS127.0.0.1:45561. Both received exactly
503/udp_worker_unavailable from the running registry, followed by matching old
gateway/source/runtime identities and zero counts in all four owned roles.
The password was entered with echo disabled and used only in memory; no new
account or credential file was created.

The first attempt released immediately without starting LAN. The second held
the reserve, checked formal TCP activity and dedicated candidate ports, marked
owned startup before Popen, and ran the frozen f08ace3 LAN listener on physical
en7 at45560/45963 for30seconds. Its actual listening record confirmed the trace
CLI opt-in. No /udp/session was sent to this listener and no READY was sent.
The same Popen instance returned naturally with exit0 and its own
candidate_shutdown: quiescence_confirmed=true, stop_failures=0. The independent
post-readback retained the original PID/start/source/runtime and zero media
roles. Only then was the reserve released. No process received a signal.

This verifies live admission, temporary listener startup and owned shutdown.
It does not measure diagnostic overhead, exercise a capture/native/feed trace,
or establish phone/public-path FPS. The persistent gateway and its dependencies
remain unchanged. Source/video preparation, actual binary execution readback,
fresh CPU context and one bounded real-media trace are still the next gates.
The last phone readback was WeChat foreground with the experimental process
absent and the stable process retained; it was not operated during this round.

The witness proves the old registry's atomic admission check at that moment,
not a persistent idle-drain marker. The continuous reserve blocks later public
factories, but does not survive a supervisor crash or atomically lock the formal
TCP gateway. Those limitations remain as described in
[owner-lan-admission-20261003.md](owner-lan-admission-20261003.md). Repeating this
listener-only verification without a new concern is unnecessary.
