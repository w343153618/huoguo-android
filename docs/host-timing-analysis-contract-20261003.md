# Bounded host-region analyzer

`scripts/probes/host_timing_analysis.py` prepares automatic analysis for the
owner-only trace candidate from
[host-raw-feed-timing-candidate-20261003.md](host-raw-feed-timing-candidate-20261003.md).
This iteration changes only the new analyzer, its offline fixtures and this
document. Frozen capture/worker sources, phones, runtime directories and
services were not changed. No real trace or performance result is claimed.

Run it after the bounded attempt has stopped:

```sh
python3 scripts/probes/host_timing_analysis.py \
  --attempt-dir /private/tmp/VERIFIED-OWNER-TRACE/attempt-VERIFIED-ID
```

The directory input selects exactly `capture.jsonl`,
`capture.jsonl.native.jsonl` and `feed.jsonl`. Separate `--capture`, `--native`
and `--feed` inputs are supported for a missing role, but must have those exact
names and the same parent directory. Never combine reconnects or different
hosts/attempts. The files themselves carry no session/host identity: matching
names and clock strings do **not** prove the executing host, source or binary.
The caller must retain the actual attempt/source/binary readback separately.
Paths are never copied into the output.

An optional host observation window uses positive integer **CLOCK_MONOTONIC
nanoseconds** and is half open:

```sh
python3 scripts/probes/host_timing_analysis.py \
  --attempt-dir /private/tmp/VERIFIED-OWNER-TRACE/attempt-VERIFIED-ID \
  --window-start-ns VERIFIED_MONOTONIC_START \
  --window-end-ns VERIFIED_MONOTONIC_END
```

Do not fill those fields with Unix wall time, screenshot PTS, phone/SF time,
packetizer `CLOCK_UPTIME_RAW`, or budget `time.monotonic` values. The analyzer
does not infer a cross-domain mapping. It calculates no source-to-phone,
touch, acoustic, WAN or end-to-end latency.

## Bounded parsing and fixed output

Each of the three roles admits at most16MiB,24002 lines,8192 bytes per line and
64 input fields per record. Files must be regular and are opened no-follow and
nonblocking; file size/time changes during reading are recorded as incomplete
coverage. Oversized files are not parsed. Long lines, excess records, truncated
last lines, malformed JSON, schema drift and rejected clock contracts receive
fixed counters, not original strings or errors.

The new diagnostic event fields come directly from `host_timing_trace.FIELDS`.
They must all exist, with no extra fields. Logical flags admit booleans or
integer0/1, because the real formatter initializes unused phases with integer0.
Timestamps, IDs and counters require exact bounded integers; booleans, floats,
NaN/Inf and arbitrary strings are rejected there. Existing Python/Swift trace
events have explicit event-specific schemas too. Only `distribution()` is
reused from `capture_trace_analysis.py`; its older permissive sanitize,
first-wins joins and clock rules are not inherited.

The output is a fixed `host-region-analysis-v1` object: per-role coverage,
named stage distributions, exclusion counts, bounded numeric identity examples
(at most8), and raw/feed cumulative counter anchors. It does not return input
records, arbitrary keys, filenames, payloads, credentials or exception details.
Invalid layout/window CLI input returns only a fixed numeric error code.

## Clock, identity and coverage rules

Each role needs exactly one valid start/end clock pair, positive bracketing
values and the exact CLOCK_MONOTONIC declaration. Python files also need exactly
one matching fixed host timing contract. Endpoints must be positive, ordered,
within that file's trace-clock bracket and, when requested, wholly within the
selected window. A zero stamp is excluded and counted as unobserved, never
converted to a zero-duration sample. Equal **nonzero** endpoints represent an
observed zero interval and remain valid. Known clock faults disable that role's
intervals and cross-role frame joins.

Each raw iteration requires exactly one loop, budget and write row. Duplicate
rows remove that identity from analysis rather than selecting the first row.
Capture association uses the unique `(capture_seq, source_pts_us)` pair;
repeated screenshot PTS can still identify the explicitly selected capture.
Only successful fresh raw submissions join native events and successful feed
media by unique preserved PTS. Native input IDs, RGBA bytes, output AU bytes and
the feed's `AU bytes+12` are checked. Status0 without stdout/AU evidence is not
accepted as output. Config/geometry are separate, idle `capture_seq=0` is not a
fresh screenshot, and failed flush is not publication even if all bytes were
written. A callback before encode-call return is legal and not rejected.

`feed_read` measures one `_feed_fill` segment. Its read sequence is not matched
one-to-one with publication sequence or source PTS. One AU can have multiple
fill segments. Stage names containing `attempt` include observed failed write
or flush regions; they must not be reinterpreted as successful encoded frames.

Clock, sink coverage and producer coverage are separate. Sink coverage requires
a unique terminal summary, consistent actual line/accepted/written counts,
zero drops/clock errors, no cap/failure, no parser rejection/change/truncation,
and clean close. Python producer coverage additionally requires its unique
numeric observation summary, zero emit/schema faults and actual quiescence.
Swift producer quiescence remains **unknown**: its clean trace close cannot
replace native final `pending_at_end` or process exit evidence. Therefore the
analyzer always leaves `whole_pipeline_coverage_accepted=false`; valid stage
samples are observed subsets and complete observed frame chains do not imply
full-session coverage.

## Regions and window limits

Distributions separate condition region/wait, control writes, original budget
check/wait/consume, raw write/empty flush, inter-loop gaps, native header/payload
read, slot wait, conversion, VT call/callback, stdout lock/write, encoded socket
write and feed fill/publication. It reports same-frame raw-write/native-read and
native-stdout/feed-publication **overlap** where all required identities and
clocks are present. It never sums overlapping regions into serial CPU cost.
The condition region contains condition wait. Inter-loop gaps can include the
previous observer's emit cost and thread scheduling, not exclusively native
work. The unchanged budget-updated value is never subtracted from trace stamps.

The fixed `mixed_gaps_ms` map adds five explicitly mixed endpoint distributions:

| Fixed key | Measured bracket and conditions |
| --- | --- |
| `raw_mixed_loop_begin_to_condition_begin_ms` | `condition_begin_ns - begin_ns`: observation setup, first condition acquisition and scheduling. |
| `raw_mixed_budget_wait_end_to_dequeue_ms` | `dequeue_ns - wait_end_ns`, only with positive `requested_wait_ns`: stop check, second condition acquisition, dequeue/bookkeeping and scheduling. No `check_end_ns` fallback is used when no wait occurred. |
| `raw_mixed_consume_end_to_write_begin_ms` | `write_begin_ns - consume_end_ns`, only when budget consumption was observed: timing calls, shared phase-lock acquisition/append, observation work and scheduling. |
| `raw_mixed_write_end_to_loop_end_ms` | `end_ns - write_end_ns`: raw-submit observation, shared phase-lock acquisition/append, counter/last-submit bookkeeping and scheduling. It is an attempted-write region, not proof of successful publication. |
| `raw_mixed_prior_end_to_next_begin_ms` | `begin_ns - prior_end_ns`: previous diagnostic emissions, next-loop work and scheduling. Requires the unique actual `iteration-1` loop and an exact match to its `end_ns`. |

All five use unique complete raw triplets and the same capture CLOCK_MONOTONIC,
positive/order/bracket/half-open-window rules. The two loop/write brackets also
require matching raw capture/PTS identities. Missing diagnostic rows, zero
stamps, clock faults, inapplicable waits, duplicates, conflicts and excluded
windows produce no invented zero-cost sample. Each distribution's fixed status
is `observed_subset` when at least one interval was observed and `unknown`
otherwise. Equal positive endpoints still constitute a measured zero interval.

`mixed_gap_exclusions` has a separate, fixed per-gap counter map; it does not
change the original `excluded_intervals`, stages, joins or counter anchors. The
new prior/next bracket intentionally aliases the existing inter-loop endpoint
pair rather than suggesting an additional serial stage. None of these gaps is
a measurement solely of a particular lock, none is a serialized CPU cost, and
their means or percentiles must not be added together. A long bracket supports
more direct instrumentation; it does not identify its mixed components.

Raw/feed anchors use actual observed endpoint snapshots within the selected
window, expose their own first/last times and elapsed span, and detect counter
regressions. They are non-atomic concurrent snapshots. Their differences and
rates are neither the same-frame join cohort nor full-session FPS, and must not
be matched to independent phone/SF windows merely by approximate wall time.
First/last edges, missing/duplicate rows and excluded boundary intervals remain
visible in the result.

Validation uses the current actual Python `HostTimingTrace` and
`BoundedCaptureTrace` formatters plus explicit Swift-format contract fixtures.
It does not execute the native encoder. Seventeen new tests cover normal joins,
clock/order/coverage faults, zero versus unobserved stamps, truncation, caps,
producer state, duplicate/missing IDs, repeated PTS, idle/config/flush failure,
callback ordering, window/counter boundaries, malicious fields, input growth
and bounded reads. The seven existing capture-analysis checks also pass.
Real concurrent overhead, real M1 stage attribution and phone/public-path
acceptance remain pending the next bounded owner sample.

Seven additional offline checks verify named mixed intervals, observed zero,
no-wait/no-endpoint handling, fixed unknowns, half-open windows and clocks,
reversal/identity/prior conflicts, duplicate triplets, closed output, and
unchanged original stage/exclusion/join/anchor results. The capture formatter,
native encoder, raw budget/write order and devices are unchanged.

```sh
python3 -m unittest discover -s tests -p 'test_host_timing_analysis.py'
python3 -m unittest discover -s tests -p 'test_capture_trace_analysis.py'
```
