# Native frame numeric App export candidate — 2026-10-04

The existing default-OFF native frame event ring was available only in the raw
instrumentation detail report. App mode exports a numeric summary, which omitted
that ring. A new report-time projection now retains a bounded closed numeric
subset. This is source and offline validation; no new App/JNI/helper artifact,
phone session, framebuffer RPC, source input or service change was performed.
The dedicated OP12 ADB read still reports device-not-found, so current phone
process/helper/artifact state is unknown. Prior signed code40 installation and
public code39 release records remain separate.

## Contract and bounds

`UdpVideoProbe.numericAppSummary` adds `native_frame_events_numeric`. Native
collection remains the existing separate `diagnostic_events` descriptor switch,
default false. Decoder-stage metrics do not enable it. This change does not add
per-packet clocks, JSON, threads or native events, change RX/drain ordering,
change the80ms assembly grant, or enlarge a queue/ring. It performs bounded
validation and projection after receive ends; it is not a zero-cost claim.

The native ring remains256, each drain at most64, and the existing Java ring8192.
Export takes the first64 **remaining** Java rows. Java/native evictions may mean
these are not the first64 generated events. No rows beyond that prefix are
validated or represented as covered. Retained/exported/omitted/generated counts,
native pending and separate native/Java evictions are explicit. Overflow-safe
accounting requires generated = Java retained + Java evicted + native evicted +
native pending for this existing one-enable-per-receiver collection contract.
Missing enable metadata is unknown; explicit OFF is unavailable. Neither means
zero generated or zero FEC faults. Explicit ON requires the matching enabled
snapshot and exact capacities.

The closed numeric columns retain event code, frame/reference ID, flags,
first/last RX-time microseconds, mathematical FEC quorum time, decision time,
fixed deadline, logical length, reason code, sequence and logical PTS value.
The five existing event/reason pairs are checked. Integers, times,1–80ms legal
header grants, ordering and capacities are validated. A delivered PTS value or
frame ID is not independent provenance; arbitrary fields, credentials, raw
media and `host_capture_us` do not cross this projection.

These phone microsecond values are caller-supplied RX/tick decisions, not verified
physical packet arrival times. Quorum is mathematical shard sufficiency, not
logical delivery, codec readiness or presentation. Host/phone/SF clocks cannot
be subtracted on the strength of this JSON. Numeric flags explicitly keep those
promotions false. Even `all_generated_events_exported=1` describes only generated
metadata when no omission/eviction/pending/sequence gap exists;
`all_pipeline_events_covered` stays0, including a known-empty enabled snapshot.

The original whole numeric report limit stays64KiB. Excess detail rejects the
entire report without truncation; actual codec/audio close and end-reason handling
are still separate. A64-row prefix is not all-session or steady-window event
coverage. Combining large existing stage arrays with new event detail can exceed
the budget; future experiments must qualify their bounded scope rather than
increase the limit or treat a rejected report as accepted cleanup evidence.

## Actual validation layer

Eight new host JVM tests compile and exercise the actual Java report methods
using typed JSON substitutes. They cover missing/OFF/known-empty/pending,
existing event/reason metadata,64-row prefix omission, separate eviction
accounting, malformed/fractional/overflow/boundary rejection, time/sequence and
reason contradictions, secret/body/host-clock exclusion and the existing whole
report rejection. These are not Android JSONObject/ART/native JNI or phone
observations; fixture event rows are synthetic.

The new tests plus existing transport/Inbox/report-budget/completion/hour-reason
fixtures passed42checks in18.098s. A separate actual SDK37 compile of all current
App/generated UDP/helper Java sources and three existing stage checks passed
4checks in3.096s. The existing worst-stage numeric report with native collection
OFF is55219bytes, still below the unchanged64KiB limit. That byte count does not
qualify combined full native/stage detail or a live report.

The preceding exact f0e508f/run37192379073 was independently overall/build/UDP
success. It does not validate this later source; this candidate needs its own
pushed SHA and cloud run. Source byte hashes and all layer limits are in the
adjacent JSON.

## Next bounded work

Keep the reviewed f0 frame-only private freeze unchanged. Once the dedicated
phone is available, fresh App/JNI/helper absence/CPU/original identity/full role
and formal checks, then one normal saved-UI current captured Attempt/read-only
frame. That route uses no UiAutomation/source input and must not reserve45965
during original public media. Metadata does not grant permission or establish
motion/FPS. Old failed global UI retirement remains separately gated.

Independently design a bounded offline numeric event consumer and an explicit
native-event diagnostic qualification. No native collection opt-in, matching
new App build, actual report or moving public event association has yet been
accepted. Preserve80ms/FIFO/sessionFPS/lead0/AAC, wait/guard and all existing
host/NPS/user-data protections. This export does not resolve the earlier public
source30/phone20 stall, prove physical packet loss or support a new APK release.
