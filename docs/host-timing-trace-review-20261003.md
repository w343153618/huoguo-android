# Independent host timing candidate review — 2026-10-03

Decision: no concrete source blocker remains for one bounded M1 owner trace
experiment at30FPS. This is source and owned-fixture acceptance only. No host
media, device, service, real network, production configuration or credential
was accessed or changed by this review. It does not approve a friend/public
deployment or claim concurrent instrumentation has zero overhead.

Reviewed frozen candidates:

| Source | SHA256 |
| --- | --- |
| `hardware_stream.py` | `b61beee9db06d6b3667cb17c8355b99f3f04b641095e759a49940d3c34aea07d` |
| `udp_lan_worker.py` | `e74a6f855cab8111d866a5b454e0d74a2e109811cfc4791f16e105450c5d4f0d` |
| `host_timing_trace.py` | `ca7ccc0ccb8832368bbb792a91a1c1a6639c01e61e110f21f311ecd4ee5992e7` |

The gateway's optional CLI entry is reviewed separately in
[owner-host-capture-trace-entry-20261003.md](owner-host-capture-trace-entry-20261003.md).
Gateway defaults are trace disabled; NPS trace opt-in rejects an unlimited
gateway lifetime. A server-owned path cannot be enabled or replaced by an App
request. The default formal/runtime dependency selection is not changed here.

## Default off and media operation order

The raw writer still takes controls only after native readiness, writes them
before budget handling, performs one existing budget delay/consume sequence,
dequeues according to the original policy, rejects nonincreasing source PTS
before consume, and preserves header/pixel/flush byte order. Idle repeat and
capture counters retain their original distinctions. When disabled, no
diagnostic timestamps, sinks or files are created. The existing opt-in capture
trace and the new diagnostics must not be confused with this default-off path.

The new feed wrappers dispatch to the original receive/publication bodies
without observation state when disabled. Complete-record publication still
finishes once begun even after cancellation; unpublished or partial records
are discarded under the original rules. Authentication, replay windows,
socket pacing and datagram serialization are not modified by observations.
Owned fixtures compare raw bytes/flushes/counters/budget-clock calls and cover
feed short writes, failure, cancellation and publication bookkeeping.

These assertions establish functional order, not instruction-count identity or
a performance equivalence measurement. Additional imports, branches and Python
wrapper calls exist even with diagnostics off. With diagnostics on, allocations,
clock reads, a bounded queue, JSON serialization and trace writes consume CPU;
some stamps occur inside the existing condition critical section. No synthetic
test proves zero concurrent overhead or absence of scheduling perturbation.

## Fault isolation corrected before freezing

The review identified that the old sink's close read the clock before enqueueing
its shutdown sentinel. A closing diagnostic clock error could override the
raw writer's media exception from its `finally`, and an initial clock error
occurred after starting the sink writer. This was reported to the implementation
agent and corrected in the frozen source above:

- start/end clock-sample errors increment the sink's separate numeric
  `clock_errors`; sentinel/join retirement still runs;
- a trace writer's thread-start failure closes its owned descriptor and retains
  that initialization error;
- raw-loop begin/end observation guards do not replace a raw writer exception;
- raw final close and failed worker-constructor trace close are best effort;
  trace close errors increment diagnostic counters instead of overwriting the
  media error or adding a false media-cleanup failure;
- feed trace retirement errors likewise remain diagnostic coverage loss.

The independent
[test_host_timing_trace_review.py](../tests/test_host_timing_trace_review.py)
executes six fault/lifecycle checks, including an AST extraction of the actual
raw worker final trace block. It never executes the media worker, starts a
listener or loads deployment credentials. All six pass against the frozen
candidate. Related host/raw/feed/capture suites total94 passing checks:

```sh
python3 -m unittest \
  tests.test_host_timing_trace_review tests.test_host_timing_trace \
  tests.test_udp_lan_worker tests.test_hardware_stream tests.test_capture_trace -q
```

`py_compile` of the independent fixture and `git diff --check` also pass.
This count is source/owned-fixture validation, not new phone or timing evidence.

## Schema, files, clocks and retirement

New timing events require exact closed field sets and finite numeric/bool
values. Arbitrary strings, media bytes, control values, usernames, keys,
session tags, network tuples and exception text cannot enter those events.
The contract uses static string labels only. Existing capture-trace metadata
has a different schema and does not gain unrestricted new fields through this
adapter.

The local operator must supply an existing private directory owned by the
effective UID. Symlink components and a changed final parent inode are
rejected; exclusive random0700 attempt directories contain0600 trace files.
Same-owner ancestor substitution remains the explicitly documented boundary,
not a guest/host isolation guarantee. An explicitly enabled attempt rejects
an invalid directory rather than silently disabling diagnostics. Fresh attempt
directories prevent a reconnect from reusing another attempt's trace file.

Sink limits are24,000 accepted records,16MiB and a bounded pending queue.
Submission does not await disk writes, and a full queue drops metadata. A sink
close can wait at most the existing2-second sentinel enqueue plus2-second writer
join; it does not guarantee the OS's underlying file write finishes by then.
Its writer remains observable as alive if retirement did not complete. These
are bounded data/wait contracts, not a hard real-time disk guarantee.

`CLOCK_MONOTONIC` host observation timestamps, Python `time.monotonic` budget
state and packetizer `CLOCK_UPTIME_RAW` are explicitly distinct named domains.
Source PTS remains the emulator's estimated screenshot-generation Unix value,
separate from media-decode PTS and packetizer admission time. Observations do
not rewrite any scheduling clock, frame deadline, audio clock or target PTS.
Failed diagnostic stamps are0; intervals involving those zeros must be excluded,
with numeric clock-error counts reported instead of fabricated latency.

Sink clean close is not producer quiescence. The raw worker reports whether its
existing producers actually exited without adding a new join to the media
flow; the feed reports its own producer independently. Current-thread exclusion,
ongoing producers or forced process exit can leave coverage incomplete. Sinks
close outside authentication/control/lifecycle locks, but ordinary observation
locking still exists and is not a measured cost-free operation.

## Required evidence for the next owner experiment

Before interpretation, record the newly frozen source/binary and actual child
selection; preserve the original owner's account, nonisolated-environment label
and idle/formal-session guards. Use dedicated checked high ports without changing
the persistent formal services or cloud domestic rules. Keep real content,
format/dimensions,30FPS/4M VBR/80ms, raw FIFO2, native slots3, audio mode, phone
refresh/CPU restrictions and route fixed. First use a30–45second trace window;
a higher frame rate or long session can hit record limits sooner.

Read **each** Python/native trace's accepted/written/dropped counts, record/byte
caps, failed state and clock-error counts; read producer-quiescent and sink writer
alive separately. A partial or capped trace cannot substantiate an exhaustive
stall attribution. Concurrent counters are explicitly best-effort snapshots,
not atomic capture cohorts. Raw write/native read overlaps must not be added as
independent sequential costs. The packetizer's admission clock is not a remote
capture-to-display timestamp.

A trace-off control is needed before claiming the trace-on rate represents the
unobserved pipeline. Only after coverage localizes a dominant wait should one
behavior parameter be changed. These fixtures do not validate source unique
content FPS, independent phone presentation, optical input latency, audio/video
sync, cellular, V50 or a friend/public path.
