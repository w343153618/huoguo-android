# Native frame progress association candidate — 2026-10-04

A new pure offline consumer validates the closed numeric native event export
and associates retained decision events with already-observed helper worker/
callback plateaus. It uses the existing RX progress analysis for RX/worker
observations. No file, credential, device or network is read by default; no live
observer, input, UiAutomation, collection toggle, APK/JNI/helper build or service
operation is introduced. The reviewed private f0 frame-only candidate remains
unchanged. Dedicated phone unavailability still prevents real acceptance.

## Qualification and interpretation

`native_frame_progress_analysis.validate` requires the exact v1 export fields,
integer/capacity/sequence/eviction/pending accounting and prefix coverage flags.
It accepts at most64 native rows. Unknown/OFF snapshots remain unavailable and
have no invented zero-event counters. A known-empty ON snapshot is distinct.
Same-frame rows must preserve their original mapping metadata and cannot report
duplicate quorum or multiple contradictory terminal events. This is internal
prefix consistency, not independently verified native JNI or frame provenance.

`analyze` defaults to unverified provenance. Cross-prefix association requires an
explicit caller qualification of the same App Attempt and phone clock. Valid
JSON, matching fields or a true flag in input JSON do not provide that evidence.
The existing RX consumer retains64 rows and at most48 helper rows; no missing
samples are interpolated. Missing RX remains unassociated rather than a
successful empty plateau. Existing RX timeline gaps remain unknown even when a
native event is present. Counters are not atomically stamped packet arrivals.

Native decision microseconds are floor(phone nanoTime/1000), so the consumer
uses a conservative possible interval [us*1000,us*1000+999]ns. It counts an event
as interior only when that entire interval lies strictly inside the observed
worker plateau. Boundary overlap is reported separately, not assigned by an
arbitrary point convention. Times that cannot safely convert into signed phone
nanoseconds are rejected. No host/UPTIME/SF clock is imported or subtracted.

Outputs contain interior retained event-code counts, distinct-frame **counts**
and boundary-ambiguous counts. Raw frame IDs, bodies, credentials and host capture
timestamps are not copied into analysis output. Five event codes preserve
mathematical quorum, logical delivery, mapping expiry, settlement by newer
successful delivery and logical rejection. None means codec readiness or actual
presentation. Retained zero counts mean only no event in that retained subset;
they do not establish absence of faults, whole-plateau coverage or loss rates.
An observed native expiry and continuing authenticated RX can coexist without
proving physical packet loss or the cause of a later worker stall.

Even an export with all generated metadata represented does not cover all
pipeline events or establish the observed media/window's eligibility. All such
promotion flags remain false. An early64-row remaining prefix can miss the
steady window; this tool does not repair that by reconstructing omitted rows or
increasing a native ring, Inbox, TTL, playback buffer or whole-report limit.

## Actual checks and limits

Fourteen new checks, including one integration fixture that runs the actual
Java report-time projection into this Python validator, plus16 existing RX
analysis checks passed30checks in1.683s. Java uses typed JSON substitutes and
synthetic rows; this is not Android JSONObject/ART, actual native JNI or a phone
report. A later bounded-schema review rejects excess keys before constructing
any input-key set and removes a redundant contradiction condition. The affected
pure offline29checks passed0.004s afterward; Java projection was unchanged.

The initial run failed only because a broad substring assertion for `frame_id`
matched the distinct-frame-count field name. The exact quoted-key assertion was
corrected; that failing run was not accepted. Final cases cover unverified
provenance, unavailable/known-empty, simultaneous RX progress and expiry, two
stages for one distinct frame, microsecond boundary overlap, evictions/pending/
omissions, invalid types/flags, foreign keys and lengths, accounting/prefix
coverage, grants/quorum/expiry/order/conversion, contradictory same-frame events,
RX gaps and unavailable RX. No production device or App failure occurred.

The Java projection source f0444b8/run37194141701 independently completed
with overall/build/UDP success. It validates that export source only. This later
consumer needs its own source SHA/run; no signed artifact or real moving video
has been accepted here. Source hashes and layer flags are in the adjacent JSON.

## Next iteration

When the dedicated phone is actually available, use the unchanged reviewed f0
frame-only controller after fresh full phone/App/JNI/helper/CPU/original/formal/
emulator checks and normal saved-UI current Attempt authentication. Read one
private frame and inspect pixels after channel close; no source input or
UiAutomation, and no45965 reservation during original public media.

Independently qualify the closed owner-only native diagnostic descriptor path,
a new matching signed App and bounded sampling/report scope before enabling it.
The normal source/runtime defaults remain OFF,80ms/FIFO/sessionFPS/lead0/AAC,
wait/guard. Old failed global UI retirement and public moving source30/phone20
causal diagnosis remain separate gates. This tool does not establish phone FPS,
MTK/V50, cellular/remote, optical/acoustic or one-hour soak acceptance.

Exact consumer source3d476566ee8b4f2c9126713f0f3559e637a0958a was pushed.
Its own run37194838068 was independently read completed with overall/build/
UDP success. This validates that source only, not a signed artifact or real
phone/native collection. Do not re-run this accepted run or old failed jobs.
