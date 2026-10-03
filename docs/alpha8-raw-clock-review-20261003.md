# Alpha8 M1 raw-supply clock and limiter review

This independent review reads the frozen owner runtime and bounded numeric log
fields only. It starts no media session and changes no source runtime, phone,
guest, gateway, NPC, NPS or production configuration. The machine-readable
receipt is [alpha8-raw-clock-review-20261003.json](alpha8-raw-clock-review-20261003.json).
The existing observation and its window limits remain in
[alpha8-raw-submit-readback-20261003.md](alpha8-raw-submit-readback-20261003.md).

## Actual dependency selection and the corrected artifact identity

The inspected runtime root is the required external directory
`/Users/wyw/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003`.
Its source entry point is `source-alpha8-dd43a39/udp_nps_gateway.py`.

Two independent read-only mechanisms agree on the gateway dependency selection:

1. `plistlib` reads the exact `ProgramArguments` array from
   `/Users/wyw/Library/LaunchAgents/local.huoguo.m1.udp-owner-trial.plist`.
2. macOS `KERN_PROCARGS2` reads the active gateway PID26875's actual argv array.
   Only entry point and closed, nonsecret runtime-option fields were exported.
   Complete argv and environment strings were never printed or persisted.

Both select `--native-encoder` as
`/Users/wyw/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003/session-pool-encoder`,
whose inspected SHA256 is
`59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf`.
The persistent packetizer is `h264_udp_packetizer`, SHA256
`567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`.

The separate `hardware/macos-h264` file has SHA256
`3ad91b912768e1fd00c9a10690c582bbdcac7ac1f0fd415a1d7a8d372def5de2`.
An earlier reviewer message called it the hardware encoder without proving
selection. That was imprecise: it is the fallback artifact, not the explicit
gateway selection. `HostHardwareSession` forwards `--native-encoder`; the child
construction uses `args.native_encoder` before its fallback directory. Checking
an identically hashed `/private/tmp` file also cannot substitute for verifying
the persistent dependency path.

This proves the active gateway's selected executable path and bytes at review
time. No media child was observed in this review. It does **not** establish the
executable hash of the already-completed gold39 child at its execution instant,
nor independently establish how the inspected executable was built from Swift
source. A future round must freeze that build receipt and record the actual
child selection before interpreting native timing.

The last four native `ready` records each contain hardware=true, 540×960,
expected-frame-rate readback30/status0, and `pixel_pool_mode=manual`. They request
low-latency mode=false, with read status−12900 and no successful property
readback. These records have no session ID or wall timestamp; their relationship
to a particular round is log-order association only. The filename
`session-pool-encoder` proves neither session-pool selection nor a low-latency
property readback. Requested false must not be renamed readback false.

## The completion-plus33ms hypothesis is absent from the source

The frozen `hardware_stream.py` is SHA256
`6fe47b938b1db88e09175ece5a465cffdcfb71144c753be0f2a90164dce64e72`.
The relevant source semantics are:

| Frozen source location | Actual behavior |
| --- | --- |
| `hardware_stream.py:276–290` | `FrameRateBudget` refills tokens with elapsed `time.monotonic`; burst capacity2 |
| `hardware_stream.py:511–536` | gRPC `ImageFormat` specifies RGBA/size/display, no FPS field or separate capture limit |
| `hardware_stream.py:694–747` | budget wait precedes dequeue; `consume()` precedes raw stdin write/flush |
| `hardware_stream.py:722–746` | completion updates `last_submit`, used only by the1-second idle-repeat check |
| `emulator_hardware_encoder.swift:663–731` | source PTS must increase; slot acquisition/conversion/VT submission, no explicit FPS sleep |
| `emulator_hardware_encoder.swift:937–984` | read one complete RGBA frame, submit it, repeat; no completion-relative frame period |

The token budget follows `tokens=min(2,tokens+elapsed×fps)`. A10ms or20ms raw
write advances the same clock, so that elapsed time is credited at the next
budget refresh. It is **not** followed by a new full33.33ms wait. The completion
timestamp does not reset the pacing schedule. Native expected frame rate,
CMTime duration1/fps and keyframe spacing are separate encoder configuration;
the inspected loop does not turn those into a second30Hz sleep limiter.

For a narrow semantic counterexample, the actual class was extracted by AST
without importing hardware worker code, and run with a deterministic fake clock
and continuously available backlog for60seconds:

| Fixed write cost | Actual token-budget simulated submissions/s | Hypothetical completion+33.33ms formula |
| ---: | ---: | ---: |
| 2ms | 30.033 | 28.302 |
| 10ms | 30.033 | 23.077 |
| 20ms | 30.033 | 18.750 |
| 30ms | 30.033 | 15.789 |
| 40ms | 25.017 | 13.636 |
| 50ms | 20.017 | 12.000 |

The small excess over30 is the initial bounded burst and endpoint counting.
This is an offline code-semantics fixture, not a runtime performance result.
It rules out the proposed completion-relative mechanism in this implementation;
it does not rule out real event-wait overshoot, thread scheduling, bursty source
arrival, blocking control writes or downstream stalls.

## What the measured numbers do and do not establish

Root's gold39 measurements and the existing bounded log show near30 complete
gRPC frames/s while completed raw submissions fall to14.18–22.92/s and raw-frame
replacement rises. The associated whole worker-close row is1546 captures,
979 submissions,567 replacements,0 idle repeats. Root reports976 AUs consumed
by the packetizer over51.598seconds, including startup/shutdown. The979/976
comparison has compatible totals but no exported same-frame cohort or encoder
final, so the3-unit difference must not be named a verified VT drop count.
The567 replacements establish a substantial loss before encoding, without
identifying which upstream wait created it.

One concrete window at Unix1791027711895 has raw29.95/s, submitted17.70/s,
replaced12.25/s over5142ms. Its91 completed raw-write samples have median3.086ms,
p95=10.238ms, maximum19.803ms. The source percentile definition means46 values
are at most the median, another41 at most p95, and the remaining4 at most the
maximum. Their sum therefore cannot exceed approximately640.926ms, plus less
than0.1ms allowance for decimal rounding. Their mean upper bound is7.0432ms.
This bound covers those completed writes only; it is not an exact partition of
the sampling window or all iterations.

Even this low submission window contains no completed raw write above20ms.
The observed mean interval is roughly56.5ms, so an explanation based solely on
these raw-write costs, with a fresh33ms sleep added after each, is both absent
from the code and unsupported by the measured percentile boundary. Additional
time outside the measured write region remains to be localized. A p95 is not
an average, and the pipeline row and phase row are separate near-adjacent
snapshots, not atomic per-frame joins.

There is a real, source-visible backpressure path, however:

1. Swift reads a complete RGBA payload before waiting for one of3 VT slots.
   While it waits, it cannot read the next RGBA payload, which can block the next
   Python raw write.
2. A slot is returned at the end of the VT callback's `deliver`, after encoded
   output has been written. Its output lock is held during that write; its
   `stdout_write_ms` measurement excludes time waiting to acquire that lock.
3. Python's encoded-output thread writes into the hardware-video socket.
   The UDP worker receives a whole framed AU and synchronously writes/flushes
   the packetizer stdin. That publication region currently lacks its own
   duration metric.
4. The native packetizer paces at32M and writes framed shards into a pipe;
   the authenticated socket sender also accounts/paces at32M. Receiver read
   delays or socket wait overshoot can propagate back through these finite
   buffers. Two pacers do not, by their mere presence, prove a16M bottleneck:
   their stages can pipeline and carry independent timing/debt constraints.

The observations do not distinguish this path from time spent in Python
condition/budget waits, control writes or thread/CPU scheduling. The existing
source source-PTS/capture-gap and raw-pipe percentiles cannot attribute each
replacement to one cause. Zero whole-frame budget rejection also cannot rule
out output-deadline failures or stalled output consumers.

## Minimal next experiment: localization before parameter changes

Run one independent, bounded **M1 owner/nonisolated** diagnostic pipeline, using
dedicated conflict-checked high ports and a newly frozen candidate binary and
source snapshot. Keep the formal gateway/NPS, account identity, guest state,
phone CPU limits and live-session protections unchanged. Do not overlap the
same guest's existing media session. This document does not authorize or start
that deployment.

Keep the current actual540×960 output,30FPS App/native/raw budget,4M VBR,80ms,
FIFO2, VT slots3, phone refresh, audio mode and public route fixed. Re-read the
real source content/rendition/position and CPU limits around the round. Record
the actual executing child path/hash plus the gateway's configuration; do not
infer mode from its filename. One30–45second real-video round with the existing
bounded capture/native trace is sufficient to localize the next hypothesis;
trace-off versus trace-on checks overhead if performance comparisons are made.

The existing trace already records capture-enqueue→dequeue/pipe write, native
complete raw read→slot wait→conversion→VT call/return→callback→stdout lock/write,
and Python encoded-egress timing. Only a small owner-opt-in diagnostic extension
is needed for the currently missing raw-loop regions:

- condition-wait begin/end and whether a complete raw frame was queued;
- budget tokens/updated/requested-wait before sleep, actual wait end, and token
  state at `consume`, without changing the clock or token algorithm;
- control-write count and duration, without command contents;
- loop-start/end and prior write completion, with cumulative fixed counters;
- feed publication begin/end/bytes so packetizer ingress stalls can be joined.

Use fixed-capacity numeric metadata, bounded record/byte limits and an explicit
coverage summary. Check trace drops/caps before computing any cross-stage
tail. Preserve source PTS for frame identity joins; it is an emulator screenshot
generation Unix estimate, not decoded-video PTS. Python/Swift trace intervals
use declared `CLOCK_MONOTONIC`; packetizer intervals use `CLOCK_UPTIME_RAW`.
Do not directly subtract these epochs without verified mapping. A raw pipe
write and the native input read overlap; never add them as independent serial
CPU costs. A header-read wait may simply mean the next frame has not arrived.

Select a behavior change only after the joined coverage identifies a dominant
region. If budget/loop wait dominates while slots/output are short, compare one
bounded budget-only candidate with native/App/source unchanged. If VT slot
wait correlates with stdout or feed-publication stalls, compare the same
capture/encode pipeline with a bounded immediate local H.264 drain, changing
only the output consumer. That drain experiment diagnoses a host path and is
not phone/public-network acceptance. Do not simultaneously change resolution,
pool mode, socket wait, phone limits or playback buffering; do not change the
production defaults from this read-only review.
