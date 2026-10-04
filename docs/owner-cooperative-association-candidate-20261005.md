# Cooperative captured Attempt association candidate — 2026-10-05

A standalone, default-OFF Java interface now retains actual same-JVM references
for a captured Attempt, receiver, Surface, phone-clock object and three resource
workers. It is outside the App source set and has no App/helper call site,
transport, JNI attach, activation switch, installer or release method. The
constructor reads nothing. It does not fill the missing genuine native callback.

Capture and each observation read the adapter under the original App lock. A
callback receives a phase-scoped View usable only on that same thread while the
lock is held; an escaped or foreign-thread View revokes the observation. The
before/after checks refuse a later Attempt, receiver, Surface, clock domain or
generation. Death, exceptions, reentrancy and deadlines are sticky UNKNOWN and
retain the actual references. There is no JSON, digest, PID or old-scope adoption.
The adapter itself is trusted code, not a wire permission; the current fixtures
provide synthetic Android state and do not establish a real App adapter.

This candidate models explicit normal cancellation: retirement requires the
captured retiring Attempt with exactly one generation advance. Each close call
must come from its exact captured worker and uses its retained closer. Completion
joins actual workers outside the App lock and observes successful callback return
and Thread.TERMINATED. These are **observations**, not codec/audio/input native
quiescence: a closer could be misbound, a worker could fail after callback return,
and the actual App has not supplied reviewed resource adapters. Natural expiry
and the actual App activity/Surface transition contract are not implemented.
Even CLOSED_OBSERVATION has no PM, scope, server or gateway release effect.

An independent narrow check found the first implementation started its 3-second
command budget after monitor acquisition. A real JVM thread held the monitor for
3.1 seconds; the waiting callback incorrectly ran. That 1-check failure (3.682s)
is retained. Each command now anchors its caller clock **before** waiting for the
monitor and checks that same bound before and after work. The original association
end is fixed at capture, at most 30 seconds; it is never renewed by phases.
Monitor/filesystem/callback execution is cooperative, not a hard preemptive wall
clock. Existing 3s/6s/15s, descriptor30, single5s and native process budgets remain.

Final **29 new actual JVM checks passed in 5.523s**. They use real object identity,
monitor contention, three actual worker threads/pipe endpoints, normal close and
joins, peer death during join, stale references, callback failure, clock rollback,
expiry and reentrancy. Android state/resource-role adapters remain synthetic.
API37.0 bootstrap compilation succeeded with all code warnings treated as errors;
only JDK obsolete source/target8 option warnings were suppressed. D8/minAPI30
produced 10-class **11596-byte DEX**, SHA256
`e108d13ddb3cb2d5c971fe2e68e529f49817ebdf86e80689bbedd44ee186bddf`.
It was not packaged, signed, installed or executed on Android. Earlier new-fixture
lint failure and empty-input D8 wrapper result are retained privately, not accepted
as compilation/DEX success.

Next bind a deliberately cooperative App adapter to its actual captured Java
objects, original locks and normal resource owners, then independently prove the
native-process bridge and ART lifetime. Host booleans, account, Plan, nonce or a
report identity cannot authorize that callback. Matching new signed App/helper,
actual saved LAN credential restore, current Attempt/codec/audio/input cleanup,
independent PM/package absence and privileged-writer exclusion remain prerequisites
before any same-created-FD retirement and original five-field gateway release.

The phone manual handling question remains unanswered. No device poll, screenshots,
helper removal, App upgrade, media or RPC occurred in this iteration. Old82f helper
installation and remote instrumentation cleanup remain unknown; the failed oldf0
trial is not reclassified. Public artifacts, services and all production defaults
remain unchanged. This new source requires its own CI run.

Exact source `da44db2571d8e581a4a8cd664f4c961f57360872`, run `37238864790`,
independently completed with overall/build/UDP success. Actual Linux 2289
tests passed in 143.233s with 11 existing skips. No rerun or borrowed previous
green. Cloud appendix remains local until the next meaningful source push.
