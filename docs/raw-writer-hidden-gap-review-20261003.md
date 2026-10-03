# Raw writer: unpartitioned gaps and the smallest next measurement

This is an independent read-only source review. No media/phone session was run,
and no runtime, binary, queue, lock, budget or configuration was changed. The
existing30-complete-RGBA/s versus approximately19-completed-submissions/s
observation is retained with the limits in
[alpha8-raw-submit-readback-20261003.md](alpha8-raw-submit-readback-20261003.md).
No new gap duration or root cause was measured by this review.

## Exact dependencies and source facts

The inspected frozen runtime is
`/Users/wyw/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003/source-alpha8-dd43a39/hardware_stream.py`,
SHA256`6fe47b938b1db88e09175ece5a465cffdcfb71144c753be0f2a90164dce64e72`;
`git show dd43a39:hardware_stream.py` independently produces the same hash.
The selected persistent encoder bytes remain
`59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf`.
Source review of Swift refers to
`scripts/probes/emulator_hardware_encoder.swift`,
SHA256`ae4223b67298322dbe19da16360590ce8d5f620e2b9871d3c339c91d7f7bb250`.
The binary's selected path/hash and prior manual-pool ready rows do not by
themselves certify its source-to-binary build provenance.

| Frozen Python location | Verified behavior and remaining boundary |
| --- | --- |
|277–290 | Burst2 token bucket refills elapsed time; consume precedes write. There is no completed-write-plus33ms sleep. |
|460,637–678 | FIFO2 retains tuples with the `pixels` object, replacing the oldest complete raw frame on overflow. No explicit full-RGBA Python copy is present in these queue operations. Protobuf/gRPC and kernel-internal copies are outside that source claim. |
|694–745 | Only `video_input` writes/flushes this `native.stdin`: queued controls, raw header/pixels, and empty flush. There is no source-visible second steady-state Python writer contending for its I/O lock. Buffered I/O and downstream pipe blocking still have real cost. |
|470–472,736,745 | `timing()` takes a shared `phase_lock` before appending. Raw writer takes it before its raw write and again after completion. |
|801–819 | Every5seconds, the sampler holds the same `phase_lock` while sorting/distributing all six bounded deques and clearing them. Its lock-hold and contention costs have not been measured. |
|699–721 | `condition.wait(.25)` is conditional on no queued frame/ready command. Collector enqueue/notify shares that condition; it is not an unconditional250ms delay. Budget waiting uses `stop.wait(delay)`, not collector notification. Scheduler overshoot remains a measurement question. |

The `phase_lock` is a concrete previously unpartitioned candidate, **not a
demonstrated cause**. Sorting up to six bounded1024-entry deques proves neither
a material stall nor negligible overhead. Controls can also block the same
writer before a raw frame. Their small buffered writes may be quick while the
later raw/empty flush carries their pipe wait; a short control-write region does
not certify completed application of a native control or its later IDR cost.

Swift reads a full raw payload before acquiring one of3 slots (lines669,964–965)
and returns a slot after callback output handling (line751). Pixel allocation,
base-address lock and RGBA→BGRA vImage permutation occur in the conversion region
(lines674–691). Callback stdout lock/write is at798–807. Thus complete hardware
H.264 encoding does not remove pixel conversion, local IPC, slot or output
backpressure costs. A long slot wait can stop the next raw read and block Python;
its downstream cause still needs joined evidence.

## What the new trace measures, and what it leaves mixed

The frozen diagnostic source is `hardware_stream.py` SHA256
`b61beee9db06d6b3667cb17c8355b99f3f04b641095e759a49940d3c34aea07d`,
with `host_timing_trace.py` SHA256
`ca7ccc0ccb8832368bbb792a91a1c1a6639c01e61e110f21f311ecd4ee5992e7`.
It adds observations without changing the reviewed budget/write order.

`condition_begin_ns` is stamped **inside** `with condition` (line737), so the
existing `raw_condition_region_ms` does not include first lock acquisition.
`write_begin_ns` is stamped after `timing('raw_queue', ...)` (lines828–830), so
`raw_header_rgba_flush_attempt_ms` excludes that phase-lock acquisition/append.
`raw_loop.end_ns` is stamped before emitting its three observation records
(`host_timing_trace.py:144–157`), so post-end diagnostic work sits in the next
inter-loop gap. These are important limits even when all recorded regions are
short.

Within one valid iteration and declared host CLOCK_MONOTONIC domain, inspect
these existing numeric endpoints before adding more instrumentation:

| Candidate interval | Known mixed content; cannot name it solely a lock cost |
| --- | --- |
|`condition_begin_ns − begin_ns` | Diagnostic-row setup after the begin stamp, first condition acquisition and scheduling. |
|`dequeue_ns − wait_end_ns`, only when a budget wait occurred | Post-wait stop check, second condition acquisition, dequeue and bookkeeping/scheduling. With no wait, use `check_end_ns` only as a separately labeled broader bracket. |
|`write_begin_ns − consume_end_ns` | Ordinary timing calls, `timing(raw_queue)` phase-lock acquisition/append, observation work and scheduling. |
|`end_ns − write_end_ns` | Raw-submit observation emission, `timing(raw_pipe)` phase-lock acquisition/append, last-submit/counter bookkeeping and scheduling. |
|Next `begin_ns − prior_end_ns` | Three diagnostic emissions, next-loop check/setup and scheduling; the prior endpoint must match the actual previous iteration. |

The current analyzer reports loop, condition, budget, control, raw-write and
inter-loop distributions. It does not separately attribute all brackets above.
They remain bounded numeric observations, not new measured locks. Keep
`queued_before/queued_after`, `condition_waited`, `control_count`, requested wait,
actual wait/overshoot, token readbacks, `outcome`, `flush_complete`, capture/PTS
identity and counter anchors with each interpretation. The millisecond
distributions alone cannot reconstruct which exact replaced capture was lost.

## Next step: analyze before changing behavior

When an authorized phone and protected host window are actually free, use the
already planned single30–45second trace with the same alpha8, actual540×960 raw
output,30FPS raw/native/App cap,4M VBR,80ms, FIFO2, slots3 and audio/path choices.
Re-read real source format/position and phone CPU constraints around the round.
This review neither starts nor authorizes a competing session. Protect M5 daily
use, the persistent owner gateway and every unrelated NPS/NPC connection.

First validate per-role clock brackets, source/binary dependency identities,
trace drops/caps, duplicate identities and producer/sink coverage. Then inspect
the mixed gaps alongside explicit budget-wait overshoot and joined native
slot/conversion/VT/stdout/feed stages. Look for temporal concentration near the
sampler's5second intervals; wall-log proximity alone is not an exact frame join.
If those mixed gaps are small, the phase-lock hypothesis loses support. If they
contain substantial missing time, a next default-off diagnostic can measure
`timing()` lock wait/hold and sampler lock hold directly, with fixed numeric
fields and the same bounded sink. Do not first move sorting, remove locks,
change buffering, increase FIFO capacity or raise the raw budget.

If explicit budget wait/overshoot dominates while native/output stages are
short, only then consider a raw-budget-only experiment. If slot/stdout/feed
backpressure dominates, compare the identical host path with a bounded immediate
local encoded-output drain; that is host localization, not phone/public media
acceptance. Do not combine either comparison with resolution/pool/buffering or
phone CPU changes.

The condition region includes its explicit wait, raw pipe write overlaps native
payload read, and callback/output/feed work can overlap across frames. Never add
stage means or percentiles as serial costs. Python budget.updated uses a
separately declared time.monotonic epoch; packetizer uses CLOCK_UPTIME_RAW.
Do not subtract either from CLOCK_MONOTONIC timestamps without verified mapping.
Source screenshot PTS can join frame identity; it is not optical or media-PTS
latency. Cumulative counters are non-atomic snapshots, and writer counters update
after `timing(raw_pipe)`: a boundary lag is not a verified encoded-frame drop.
