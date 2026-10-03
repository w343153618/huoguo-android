# Automatic source admission for one owner LAN trace

This candidate removes the repeated180-second manual ENTER window. It is
explicitly default-off and affects only the owner LAN media-only acceptance
driver. It does not change the App, capture/encoder algorithms, source renderer,
queues, buffering, persistent services, NPS or friend M5 deployment.

The driver finishes its existing formal-session, target-absence, private-input
and marker checks, then makes one synchronous bounded source collection. It
requires the exact expected PID/UID/start before and after, active matching
MediaSession ownership and state3/speed1, successful bounded reads and reaped
children. It rechecks target App absence after this potentially15-second read,
then samples native CLOCK_MONOTONIC and validates freshness before am instrument.
If the App became active or unavailable, there is no instrumentation or
force-stop. The absence checks are not an atomic phone-side reservation and do
not prove no-restart semantics.

Native source-bracket and trace timestamps have the named
host_clock_gettime_CLOCK_MONOTONIC_ns contract. The collector's Python monotonic
values remain separately named and validated; the packetizer's UPTIME, source
Unix estimate, guest elapsed time and phone/SF time are never subtracted from
this source clock. Launch and first capture must fall within30 seconds of the
earliest native source observation. The file reader samples its observation
clock after reading, so a producer append during pread cannot be spuriously
classified as a future capture.

Before SF samplers start, this attempt's first capture must be qualified from a
complete bounded prefix. The reader opens only a fresh empty same-UID0700 root,
walks every directory component using dirfd/O_NOFOLLOW, requires a single new
attempt and regular0600 capture.jsonl with one hard link, and pins their inode
identities. It never changes attempts or discovers historical traces. Reads
always start at byte0 and are limited to64KiB/256 complete rows; partial tails
wait. The numeric validator requires the initial trace-clock/start header,
first capture_seq1, matching geometry/RGBA bytes and ordered nonfuture native
timestamps. Only source_first_capture_missing can wait until the freshness
deadline; malformed/unknown/stale input fails and enters owned cleanup.

Source evidence is numeric_playing_format_unknown. Visual motion stays null,
live format is false, codec/itag/size/contentFPS/bitrate remain null. A changing
MediaSession position is not decoded motion, and quality-menu or old itag299
readbacks cannot fill these fields. Qualification covers the first owned
capture only, not a later helper reconnect, phone presentation, optical latency,
cellular/V50 performance or a same-format A/B. Any later performance analysis
must retain this evidence scope.

The existing continuous owner admission, exact trusted-HTTPS original-registry
503 witness, role-zero readback, mark-before-Popen and actual owned quiescence
remain supervisor responsibilities. The driver does not bypass authentication,
reserve a production identity or signal an original service. Cleanup failures
are separate fixed-class fields and preserve the primary failure. A descriptor
close-error fixture actually closes the owned FD before injecting OSError;
it does not establish that every real kernel close failure has closed an FD.

Independent reviews and root ran95 inert targeted checks covering the new gate,
prefix, driver integration and legacy driver cleanup. They used fixture clocks,
owned temporary files and inert children; no phone/media test is inferred from
this result. Root final-source full suite passed1544 checks in58.235s; this remains
source/owned-fixture evidence. One protected real trace is the next separate gate. A new private supervisor was prepared without execution or
credentials; its manifest pins all driver imports separately from the frozen
f08 host core/native/helper, leaves old failed supervisors unchanged, and uses a
finite240-second listener rather than a manual wait.

## Exact reviewed candidate bytes

- `scripts/probes/owner_source_gate.py`: `8b9f0cf4264a6605a103db4ef69ec1a7a85e19898c3cd93f9db76db4e8b853fa`
- `scripts/probes/owner_trace_prefix.py`: `4e2348215b8b1389a875159d77a8eaa56f8faaa1dcb2c82f88d369062c5d0255`
- `scripts/probes/run_authenticated_lan_ui.py`: `d887421dafaa6d99ff1e98614ba8d43746c0a37d7c02a467f75677777ccaf552`
- `tests/test_owner_source_gate.py`: `df8eaa5e59753633566f3548b70443537b41466f61d8fa5500ffb669a695ce76`
- `tests/test_owner_trace_prefix.py`: `f88f5d8bd4f15cacf344c41a71ba25a6f1ff17e4254a9fdfa32d7df409b2abf1`
- `tests/test_owner_source_driver.py`: `3bbeb7de5bb6d286a17d7fd7d029a483b4b23b9ccef9911216506215dd222896`

Published alpha8/code39 and stable1.31/code32 remain unchanged.

The first automatic supervisor actually acquired the protected listener and
ended naturally with quiescence confirmed, zero signals and no driver, media
or trace. Its broad bounded_supervisor_failure cannot identify a root cause.
Fresh package metadata separately showed two phone user UIDs; the next private
candidate targets user0 explicitly, keeps all original guard/cleanup boundaries
and adds closed stage/class diagnostics. This is setup evidence, not FPS.

The opt-in driver uses explicit user0 for its two marker UID reads and requires
one exact package/UID row. Default-off commands stay unchanged. Actual phone
metadata was a comma UID list, which the old legacy parser could take the first
item from; a synthetic multiline rejection cannot explain the first attempt.
The next candidate adds closed stage/class diagnostics and300-second process
runtime to cover the bounded45-second user0 helper installation. It is still
finite, and its first-session media/freshness/steady limits are unchanged.
