# Owner-only host raw/feed timing candidate

This candidate addresses the missing regions identified in
[alpha8-raw-submit-readback-20261003.md](alpha8-raw-submit-readback-20261003.md)
and [alpha8-raw-clock-review-20261003.md](alpha8-raw-clock-review-20261003.md).
It is source-level diagnostic work, not a new performance result. No phone,
guest, runtime directory, gateway, NPC, NPS or production service was changed
by this implementation. Existing physical30, rendering, resource, bitrate,
FIFO2, token burst2, VT slots, packetizer pacing and playback settings remain
the inputs to a future experiment.

## Explicit local opt-in and files

`LanMediaWorker(..., capture_trace_dir=None)` is a server constructor keyword;
`None` is off. It never reads a trace path from an App/HTTP session descriptor.
The gateway's separate `--capture-trace-dir` option forwards the local choice.
The directory must already exist, belong to the current effective UID, have
no group/other permission bits, and have no symlink in any path component.
The probe creates one exclusive random0700 attempt directory. Paths such as
`/tmp/...` and `/var/...` are deliberately rejected on macOS when those path
components are symlinks; use a verified `/private/tmp/...` or an existing
restricted runtime parent. Do not resolve away the symlink check.

Each attempt holds three metadata traces:

| File | Producer and scope |
| --- | --- |
| `capture.jsonl` | Python screenshot enqueue, raw writer regions, encoded egress, numeric counters |
| `capture.jsonl.native.jsonl` | Existing explicit Swift encoder trace; verify selected binary supports it |
| `feed.jsonl` | Owned host complete-record reads, packetizer pipe publication, cancellation counters |

Each Python file is exclusive0600. Its existing asynchronous sink has queue
capacity256, at most24000 accepted records, and at most16MiB including reserved
summary space. It drops observations when full instead of waiting on disk in
the media hot path. The native sibling retains its existing bounds; it is not
counted as part of either Python file's limit. There is **no aggregate quota**
across attempt directories. This option is for a bounded owner diagnostic
gateway, not a new always-on `max-runtime0` production default. The operator
must manage retained attempts and check total disk use.

`HostHardwareSession(..., raw_writer_diagnostics=False)` is separately default
off. `True` requires both `capture_trace` and an explicitly selected native
encoder, and forwards `--raw-writer-diagnostics` to the hardware worker. The
UDP worker enables it only under the server-side directory opt-in. A bad
explicit directory fails before the worker socket starts; no media fallback
or trace-path substitution occurs. The existing authentication, integrity,
anti-replay, cancellation, formal-session monitor and domestic egress rules
are unchanged.

The off path creates no timing sink and calls no new observation clock.
The actual token-budget `delay()` and `consume()` calls, raw/control writes and
flushes remain in their original order. The feed still buffers one complete
record and completes a publication that has already begun even if revoked.
Source-level wrappers and branches have a cost; the fixtures establish byte
and algorithm semantics, **not zero CPU overhead**. Actual concurrent trace
cost still needs a bounded real-video off/on comparison.

## Numeric contract and interpretation

`host_timing_trace.py` admits only fixed event keys and finite numeric/boolean
values. No image/video/audio payload, command value, username, secret, network
tuple, arbitrary process text or exception detail is accepted. Fixed header
strings describe the contract. Per-iteration record fields are bounded below32.

| Event | Meaning |
| --- | --- |
| `raw_loop` | Loop begin/end, preceding loop and write endpoints, condition region and exact wait begin/end, queued count, control region/count, dequeue identity, outcome and cumulative counters |
| `raw_budget` | Original delay/consume call brackets, token snapshots, original requested wait, actual event-wait interval, original budget-updated value |
| `raw_write` | Attempted header+RGBA+flush region, byte count, completion flag, original screenshot identity, or a separate empty flush interval |
| `feed_read` | Each bounded complete-record fill segment, read/timeout call counts, lengths, terminal outcome and cumulative publication anchors |
| `feed_publish` | Complete-record pipe write/flush brackets, short-write calls/offset, success, original source PTS for media/config, cancellation state and cumulative publication/failure anchors |
| `feed_cancel` | Bytes discarded before publication, partial flag and cumulative discard counters |
| `host_timing_summary` | Clock/schema/emission faults and whether the producer was observed quiescent |

Raw outcome codes are1 submitted,2 empty flush,3 nonincreasing-PTS reject,
4 cancellation,5 incomplete/unset and6 failure. `capture_seq=0` is an idle
repeat, not a new gRPC capture. A write endpoint is the **attempt's** endpoint;
check `flush_complete` and outcome before counting a submitted frame. Zero
timestamps indicate an unobserved phase or a counted clock error, not an
instantaneous operation. Control count0 has no control IO; its bracket is an
empty region. The condition region includes the locked queue/control inspection;
the separately bracketed condition wait excludes it. Lock acquisition and
scheduled inter-loop time can appear outside that region. Emit queue calls
after the endpoint can appear in the next `begin_ns-prior_end_ns` gap and must
not be mistaken for native work.

Feed read outcomes are1 complete,2 cancellation and3 failure. Publication kind
codes are1 codec,2 geometry,3 config and4 media. Codec/geometry have
`has_source_pts=false`, rather than inventing a frame timestamp. Media/config
retain the original masked source PTS; it is still an emulator screenshot
generation Unix estimate, not decoded-video content PTS. A complete `written_bytes`
with a failed flush remains `published=false`; it does not increment successful
record/byte counters. Cancellation before publication writes nothing. A started
publication still finishes all bytes and flush before the next record boundary.
Neither a host pipe write nor flush proves UDP transmission or phone display.

The observation timestamp clock is explicitly
`host_clock_gettime_CLOCK_MONOTONIC_ns`. `updated_python_monotonic_ns` is the
original budget's `time.monotonic` seconds scaled to nanoseconds and has a
separate declared contract. It is not used to change/refill the budget.
Packetizer `CLOCK_UPTIME_RAW`, phone uptime, and wall-clock sample endpoints
must not be subtracted from these epochs without an independently verified
mapping. The existing sink brackets a Unix clock sample for approximate wall
alignment; this is not an exact per-frame SF/phone cohort. Raw write and native
read overlap, so they cannot be added as independent serialized CPU costs.

Counters are best-effort cumulative snapshots from existing threads, not an
atomic capture-to-submission cohort. Join screenshot IDs/source PTS, iteration
IDs and publication PTS only where the necessary rows exist. First/last edges,
config records, idle repeats and cancellation remain separate. A missing row
must not be treated as a successful zero-duration sample.

## Coverage and teardown

`host_timing_summary` counts observation-clock faults, schema rejects and
emission exceptions. Actual sink coverage additionally requires
`trace_summary` with accepted/written/dropped records, byte cap, clock errors
and clean close. Accepted records may exceed written records when the byte
ceiling is reached. Queue cap, a failed/blocked writer, missing summary or any
clock error can invalidate the relevant timing coverage. The feed receipt
adds only numeric `host_timing_diagnostics` state when opted in.

The sink catches start/end clock sampling faults and still closes through its
bounded sentinel/join path; such faults are lost metadata and cannot replace
the original media error. Unexpected raw observation end errors and feed/raw
trace close errors are similarly isolated. A permanently blocked sink may
still have a live daemon writer after bounded close; report it as incomplete,
not as clean media coverage.

`clean_close` describes the sink, **not all media producers**. The existing
hardware teardown does not join every worker thread; the candidate reports
`producer_quiescent` from actual thread state without adding media waits or
claiming a complete final cohort. Feed teardown reports the sole feed thread's
state separately. A producer still alive, or the stop invoker excluded from
the existing joins, is not accepted as complete source coverage.

## Offline validation and pending next round

The meaningful fixtures execute the actual AST-extracted raw writer with owned
clocks, queues and pipes: off/on exact bytes/flushes and budget-clock call
counts; budget wait overshoot; spurious condition wake; control requests before
ready; cancellation; PTS reject; latest-policy skip; idle repeat; raw write and
flush failure. Feed fixtures cover fragmented original records/flags/PTS,
cancel before publication, partial-read cancellation, short writes with
cancellation after publication starts, invalid write counts, partial write and
flush failures, EOF/read-timeout boundaries, and clock/sink failure isolation.
Directory/async-cap fixtures test private exclusive paths, no HTTP path,
fixed numeric rejection, socket-start failure cleanup and bounded summaries.
All are offline checks, not a real M1/phone/WAN or MTK/V50 acceptance.

Run the relevant source checks:

```sh
python3 -m unittest discover -s tests -p 'test_host_timing_trace.py'
python3 -m unittest discover -s tests -p 'test_host_timing_trace_review.py'
python3 -m unittest discover -s tests -p 'test_udp_lan_worker.py'
python3 -m unittest discover -s tests -p 'test_capture_trace.py'
python3 -m unittest discover -s tests -p 'test_hardware_stream.py'
```

Before a real owner experiment, freeze the candidate source **including the new
`host_timing_trace.py` import dependency**, App/helper, selected encoder and
packetizer separately. Verify actual executed child selections and final trace
coverage. The prior dd43a39 runtime is untouched and has none of these new
fields; historical logs cannot be reinterpreted as having them. Keep the current
540×960/30FPS/4M VBR/80ms/FIFO2/public-path parameters and phone limits fixed,
re-read source rendition/position and CPU limits, and protect live formal
sessions. Start with one30–45second trace round. Decide on a behavior change
only after the joined regions localize a dominant wait; do not enlarge decoder
queues or alter NPS to hide a pre-encode supply deficit.

Current candidate file SHA256, separate from the frozen runtime hashes:

| Canonical source | SHA256 |
| --- | --- |
| `hardware_stream.py` | `b61beee9db06d6b3667cb17c8355b99f3f04b641095e759a49940d3c34aea07d` |
| `udp_lan_worker.py` | `e74a6f855cab8111d866a5b454e0d74a2e109811cfc4791f16e105450c5d4f0d` |
| `host_timing_trace.py` | `ca7ccc0ccb8832368bbb792a91a1c1a6639c01e61e110f21f311ecd4ee5992e7` |

These are source identities, not deployed binary identities or evidence of
throughput improvement. Raw trace files stay in restricted evidence locations
and are not committed; publish only reviewed numeric summaries with their
scope/window/clock limits.
