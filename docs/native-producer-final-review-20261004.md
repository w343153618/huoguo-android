# Native trace producer final: read-only review, 2026-10-04

## Result and scope

The protected auto5 owner M1 LAN run produced two Swift native traces with a
start clock record and raw/VT observations, but neither trace has an end clock
record or sink summary. This is incomplete native timing coverage. The specific
reason that either Swift process failed to reach trace finish remains unknown.
The reviewed code contains concrete signal and `_exit` paths which can bypass
that finish; this review does not assert that any one path occurred in auto5.

This review read source, frozen source/runtime files and fixed numeric fields in
the completed run's private evidence. It did not execute an encoder, run media,
operate a phone, change a service or replace a frozen runtime. The only new file
is this document. An independent reviewer also checked the native lifecycle and
the source/binary pins without executing media.

## Actual dependencies and observed trace scope

The auto5 supervisor selects the f08 seed runtime explicitly, rather than an
unselected fallback encoder. The following hashes were read from actual files:

| Dependency | SHA-256 | Comparison |
| --- | --- | --- |
| `hardware_stream.py` | `b61beee9db06d6b3667cb17c8355b99f3f04b641095e759a49940d3c34aea07d` | Canonical and f08 frozen source are byte-identical. |
| `scripts/probes/emulator_hardware_encoder.swift` | `ae4223b67298322dbe19da16360590ce8d5f620e2b9871d3c339c91d7f7bb250` | Canonical and f08 frozen source are byte-identical. |
| `session-pool-encoder` | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` | The f08 runtime executable and existing external executable match. The independent reviewer also matched the persistent copy. |
| `udp_lan_worker.py` | `e74a6f855cab8111d866a5b454e0d74a2e109811cfc4791f16e105450c5d4f0d` | Actual f08 frozen source. |
| `scripts/probes/host_timing_analysis.py` | `a60d3a7afda6d79668e90b5328ffe0e039e62a031dd30e465e5822190598cba0` | Current analyzer selected for the run's follow-up. |

The external paths needed to reproduce this dependency review are:

- Frozen source/runtime: `/private/tmp/huoguo-hosttrace-f08-fhznnpig/`.
- Selected encoder: its `runtime/session-pool-encoder`.
- Matching external executable: `/private/tmp/huoguo-session-pool-encoder`.
- Run evidence: `/private/tmp/huoguo-hosttrace-auto5-56w511u8/`.

These are restricted external runtime/evidence locations, not new deployment
targets. No executable was rebuilt here. Source equality does not independently
reproduce the build provenance of the historical executable; existing frozen
pins and actual executable hashes remain distinct evidence.

| Private attempt | Native file bytes | `raw_read` | `vt_submit_return` | `vt_frame` | Start clocks | End clocks | Sink summaries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `attempt-d117e6d26194199c` | 1,180,333 | 923 | 923 | 923 | 1 | 0 | 0 |
| `attempt-8ca3b944c361908e` | 121,585 | 95 | 95 | 95 | 1 | 0 | 0 |

Those are observed record counts, not proof of all submitted frames or all
callbacks through process termination. Source content format remains unknown;
this review supplies neither a same-format performance comparison nor phone,
WAN, optical or acoustic acceptance.

## Exact shutdown and evidence boundaries

1. [`HostHardwareSession.close`](../hardware_stream.py#L434), lines 434–448,
   first closes its channels, then sends `SIGTERM` to the owned worker process
   group if the worker is still running. Its bounded fallback is `SIGKILL`.
   The native encoder is spawned by the worker without a new session at lines
   620–635. Thus this path can reach both worker and encoder. Auto5 does not
   independently record which of those branches ran for each encoder.
2. [`worker` cleanup](../hardware_stream.py#L941), lines 941–967, closes sockets
   and then directly terminates a still-running native child, with a bounded
   kill fallback. This cleanup does not first close the native child's stdin
   and confirm a normal EOF/drain. This is another concrete trace-finish bypass
   path; it is not a diagnosis of the actual two process exits.
3. Swift only ignores `SIGPIPE` at
   [`emulator_hardware_encoder.swift:882`](../scripts/probes/emulator_hardware_encoder.swift#L882).
   There is no `SIGTERM` or `SIGINT` trace-final handler. The ordinary outer
   completion calls `traceWriter.finish()` at line 997; catch calls it at line
   1009. Signals can terminate the process without executing either call.
4. The native drain watchdog uses `_exit(2)` at
   [line 825](../scripts/probes/emulator_hardware_encoder.swift#L825), and the
   catch deadline uses `_exit(1)` at line 1007. These paths intentionally bypass
   subsequent normal trace finish. Missing final remains unknown on such a path.
5. In [`udp_lan_worker.py:322`](../udp_lan_worker.py#L322), `self.native` is the
   **H.264 UDP packetizer**, not the Swift encoder. Its `_finish_native`
   (lines 719–756), `native_shutdown` and `native_final_status` report that
   packetizer. Both auto5 host-session reports have packetizer final observed,
   natural exit code 0 and no packetizer TERM/KILL fallback. These facts cannot
   establish the Swift encoder's final, drain result or actual exit status.
6. The supervisor's `signals_owned_or_original == 0` covers its own signal
   operations on the directly supervised candidate/original services. It does
   **not** mean that `HostHardwareSession.close`, worker cleanup or another
   nested layer issued no signals. It must not be reported as a whole-pipeline
   no-TERM/no-KILL result.

## Why the existing footer is not producer final

- [`BoundedCaptureTrace.finish`](../scripts/probes/emulator_hardware_encoder.swift#L161),
  lines 161–175, describes the trace sink queue/file, not cessation of native
  submissions and callbacks. `clean_close` is currently a literal true, and the
  `close(fd)` result is ignored. A footer is not independent process evidence.
- The end clock at line 162 is admitted through ordinary capped `record()`;
  record, pending or writer failure can reject it. Reserving a small amount of
  byte space for the footer does not reserve record/pending admission for end.
- [`HardwareEncoder.finish`](../scripts/probes/emulator_hardware_encoder.swift#L821),
  lines 821–878, drains at line 830 but then reads hardware/properties and builds
  a report. Those later steps can throw. The catch's `try? finish(reason:
  "error")` at line 1008 discards actual drain status. Neither a report nor the
  catch path proves a successful drain.
- In [`deliver`](../scripts/probes/emulator_hardware_encoder.swift#L750), lines
  750–751, LIFO defer ordering decrements `pending` before submitting the callback
  trace row. `pending == 0` alone cannot establish that callback trace production
  has finished. Callback lifetime and trace admission require separate evidence.
- Resize also calls encoder finish at lines 975–976. That is an intermediate
  encoder segment, not final completion of the outer process.
- The analyzer deliberately keeps native producer quiescence unknown at
  [`host_timing_analysis.py:213`](../scripts/probes/host_timing_analysis.py#L213).
  It requires a start/end clock bracket before admitting native regions and
  retains `whole_pipeline_coverage_accepted = False` at line 495. The current
  auto5 native records must not bypass those gates.

## Minimal default-off next contract

First add a diagnostic-only candidate under the already default-off native
`--trace` option. Preserve ordinary media framing, timestamps, slots, bitrate,
drain attempts, watchdogs and signal policy. Do not add broad cleanup recovery,
signal handlers or a production teardown change as part of this diagnostic step.

The candidate should emit exactly one closed numeric `native_producer_final` at
outer normal/catch completion, never at resize. Its fields should distinguish:

- Fixed `end_reason_code` and `exit_intent`; actual process exit remains a
  separate corresponding-Popen observation.
- Outer input-frame count, submitted/encoded/dropped counts, pending frames and
  an explicitly tracked callback/trace-producer active count.
- `drain_attempted` and the actual signed drain status captured at the
  `VTCompressionSessionCompleteFrames` call site, before later readback/report
  operations can fail. Resize drains need segment counts or a separate aggregate
  uncertainty flag so a later successful drain cannot erase an earlier failure.
- `no_future_submissions` and `producer_quiescent` as separate strict booleans.
  Successful drain after an input/output error may establish quiescence without
  establishing successful media. Uninitialized zero-frame EOF is explicit.
- A monotonic timestamp in the existing
  `host_clock_gettime_CLOCK_MONOTONIC_ns` domain. Do not subtract it from the
  packetizer's `CLOCK_UPTIME_RAW`, source wall PTS or phone clocks.

Final, end clock and sink summary need a small reserved terminal admission/byte
budget, a single terminal state transition and ordering after all previously
accepted trace writes. They must not pass through the ordinary capped
`record()` admission. Callback accounting must include the callback trace emit;
otherwise pending-zero can race terminal closure. Producer state and sink
success remain separate: writer failure/drop/cap does not disappear merely
because a final can be written.

The analyzer should accept only the versioned closed event schema, reject
duplicate/contradictory finals and require the final's ordering, callback/pending
readbacks, sink coverage and a matching actual process-exit receipt before any
full native producer acceptance. Legacy traces without a final remain unknown.
Do not upgrade whole-pipeline coverage from this one layer.

This diagnostic alone does not make the current forced-close path graceful.
If later evidence requires graceful native EOF, scope that as a separate bounded
owner candidate: first prove the sole raw writer has stopped and finished any
partly written record, then close only its native stdin and retain owned output
draining within a fixed deadline. Its fallback signals and uncertainty must be
recorded for the corresponding encoder. Such a behavior change is not implemented
or validated by this review; the existing owner-experiment authorization remains.

## Discriminating fixture plan

Use inert lifecycle dependencies and the real trace writer/analyzer where
possible; do not present hand-authored rows alone as execution of native teardown.

1. Trace off: no new trace resource/clock, media stdout byte changes or alternate
   cleanup behavior.
2. Zero-frame clean EOF and clean frame-boundary EOF: one outer final, end and
   footer; partial header/payload EOF: unsuccessful input result, never falsely
   declared successful media.
3. Drain status success/error and a later report/readback error: preserve the
   actual drain result independently; the error is not overwritten by final.
4. Pending reaches zero while callback trace emission is deliberately held:
   final cannot declare producer quiescent or close its sink early.
5. Resize followed by EOF/error: only one process final, with intermediate
   encoder segments distinguished and uncertainty retained.
6. Record/pending/byte saturation and sink write failure: terminal reservation
   remains bounded; ordinary coverage loss remains visible.
7. Footer without producer final, missing/duplicate final, malformed types,
   contradictory counters, wrong clock and late rows: producer acceptance stays
   false/unknown. A packetizer exit receipt cannot qualify a Swift final.
8. Owned inert process signal/watchdog exits: missing final remains unknown;
   parent `exit_intent` cannot be substituted for actual process status. Scope
   supervisor and nested signals independently.

Only after these source/inert checks should a newly built, separately pinned
native candidate be tested in one protected real-media run. The current
`59264ab7…` run must not be retrospectively relabeled with the new contract.
