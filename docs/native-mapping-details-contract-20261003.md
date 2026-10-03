# Experimental phone mapping diagnostics, 2026-10-03

This is a diagnostics addition to the independent authenticated UDP candidate. It does not change the formal TLS/TCP App, its media defaults, the phone assembly budget, the four-frame Java Inbox, playback clock, recovery/epoch decisions, lead0 or PCMoff. Existing native 21-field statistics remain unchanged. It does not establish a performance improvement or classify every FEC failure.

## Frozen independent JNI contract

`NativeUdpFec.nativeSetMappingDiagnostics(long, boolean)` opts in only the mapping diagnostics; it is distinct from the older per-frame event diagnostics. The native receiver defaults to disabled. The experimental Probe explicitly enables this facility and reads the actual enabled value before starting media.

`NativeUdpFec.nativeMappingDetails(long)` returns exactly 292 nonnegative signed Java longs: 36 header values followed by 32 rows of eight event values. Native unsigned values keep the existing signed-long saturation convention. A saturated frame ID must not be treated as a unique correlation key. The schema version is 1, capacity 32 and event columns 8. The header and column names are frozen in `NativeUdpFec.MAPPING_DETAIL_HEADER_NAMES` and `MAPPING_DETAIL_EVENT_NAMES`, mirrored by the C++ receiver.

The three rejection reason codes are 1 for an old frame outside the retained frame-ID window, 2 for an occupied eight-mapping capacity, and 3 for a mismatching header on an existing mapping. Code 4 observes a newly admitted mapping whose frame ID had an earlier capacity rejection retained in the separate 32-entry tracking cache. Code 4 is observed after new mapping insertion and before native core reception. It does not extend the original assembly grant. For code 2 and 4, `first_capacity_reject_phone_us` is the first rejection in the retained tracking window, not a guarantee that it is the first rejection ever. Cache eviction and disabling can remove that association.

The independent ring is non-destructive and returns its newest retained events in chronological order. Unused rows are zero. `coverage_mask=15` means the four hook families exist; it does not imply complete event retention or enabled-time coverage. Rejections while disabled increment `rejections_unobserved`, without pretending they have a classified reason. Disabling clears retained events and the tracking cache. Ring clearances increment `events_cleared`, not `events_evicted`; tracking loss during disable is represented by `disable_transitions`.

Java validates the actual length, version, enabled state, installed coverage, declared capacities, nonnegative values, bounded depths, rejection partition, ring accounting, ordered sequence/time, header first/last values, reason values and unused padding. A nonempty ring must be a contiguous newest sequence tail ending at `events_total`; a cleared empty ring retains no first/last metadata. It requires:

- `mapping_rejected_observed + rejections_unobserved == legacy_mapping_rejected`;
- the three rejection counts sum to `mapping_rejected_observed`;
- `events_total == events_retained + events_evicted + events_cleared`;
- admitted-after-capacity count does not exceed observed new mappings.

Only enabled snapshots require current adapter depth to be no greater than enabled-time observed maximum; disabled snapshots still report live current depth. Capacity tracking is 32 and adapter/core depths are at most eight. Invalid or overflowing accounting is rejected explicitly, never converted to zero counters.

## Observation and report boundaries

The existing receive thread performs one cold read, one fixed snapshot read in its existing approximately 100 ms statistics poll, and a final read after its worker cleanup attempt. No new sampling thread or per-frame JSON is added. Each JNI read allocates one fixed 292-long array; successful Java validation uses primitive loops and static names/indexes. Polling duration, maximum duration and attempt/failure counts are included. The measured `jni_and_validation_*_ns` interval includes the JNI snapshot/array operation and Java validation, not later JSON conversion, diagnostic writes inside native packet processing, or the enable operation. It is not a full concurrent instrumentation-overhead measurement. The 100 ms period is a requested polling interval rather than a scheduling guarantee.

`arrival_phone_us` and `snapshot_phone_us` come from valid native accept/expire caller timestamps, derived from phone `System.nanoTime()/1000`. The snapshot timestamp is the most recent native valid tick, not the time Java polled the array. `last_read_phone_system_nano_time_ns` records the Java poll finish separately. No field is an optical, acoustic, WAN one-way or physical touch measurement. Native hooks reject zero or backward caller timestamps before these events.

The App numeric summary retains `native_mapping_details`, all numeric headers and, for a nonempty ring, eight numeric column arrays of at most 32 elements. The existing numeric converter omits empty arrays when `events_retained=0`; the numeric `event_columns=8` and capacity remain explicit. It keeps the 64 KiB hard report limit. Status 1 means the latest snapshot passed the contract; -1 means not read, -2 means missing new JNI methods and -3 means malformed or disabled-while-requested readback. Missing/invalid status omits the native counters and events rather than substituting zeros. A later valid final snapshot may produce status1 after a prior poll failure, while `read_failures` and the session failure remain visible. Missing new methods fail the owner experiment explicitly; there is no silent old-JNI compatibility fallback. Initial/final failures still attempt bounded existing cleanup and report the available diagnostic status.

## Source-level validation and frozen Java sources

`python3 -m unittest tests.test_native_mapping_details tests.test_decoder_stage_metrics` passes five Python tests, including 110 assertions against actual Java schema validation and an explicit missing-native-symbol fixture. Fixtures include valid evicted/cleared tails and a rejected non-tail or sequence gap. An additional owned host JNI library compiles the actual `native_udp_fec.cpp` with the frozen receiver and existing verified pinned FEC, then executes 11 Java-to-native checks for the fresh disabled snapshot, enabling, positive tick, old 21-field statistics, disabling and closed-handle failure. Its temporary local library is removed with the fixture. Host JVM missing-symbol and JNI checks do not replace an Android old-library or new APK execution test. The actual summary converter with 120 stage segments, 64 stage events, a full 32-row mapping ring, 1,000 actual prior-sequence evictions, audio numeric fields and large timestamps produces 53,680 bytes with narrow JVM JSON substitutes. Deliberately oversized output fails the existing 64 KiB limit. This is not ART runtime or phone overhead validation.

`python3 scripts/probes/check_udp_video_inbox.py` compiles the actual experimental Probe and runs its 27 offline FIFO/epoch/reference checks. It does not open ADB, configure a codec, build/install an APK or measure a phone.

Frozen Java and JNI bridge sources for the proposed alpha4 build:

- `experiments/nps-transport/phone/NativeUdpFec.java`: `42657e7b62d9a64f828a9d1f8adeedc14a46e2d1189157773f3951cd021476f9`.
- `experiments/nps-transport/phone/UdpVideoProbe.java`: `327fa96487636277728da15a04d047b57229429fad19c245f829678175bbc805`.
- `experiments/moonlight-v2/transport/android-udp/native_udp_fec.cpp`: `753aa489099d0313b7af51a2016aea3f9902839496b5982218a163fcc85306a9`.
- Companion receiver header, owned by the native diagnostics agent: `e24af3f6e5f5b53d11d8b69094032a881cf766385d489f2c949f591c49d58b59`.

The corresponding new native methods must be packaged with this same candidate. The root experiment owns native/APK build, original signing identity, phone verification, preservation of limits/data/accounts and the next real media run. Earlier alpha3 measurements did not contain these fields and must not be reinterpreted as mapping-detail evidence.

## Integrated candidate build checkpoint

Root integrated the frozen JNI and Java sources and built independent `1.31-alpha.4 / code35`. Android assemble/lint passed; the full repository unittest run passed 954 checks in26.747s. APK SHA-256 `8c00a1660ccef0e84b11c98c16091fd630c5980d29704e069562b92a04c94351`; packaged AArch64 JNI SHA-256 `578347ca24008ce7b987c77f2f1356c56761ac3d9ee4e7be89b8e9130476e402`; original signer retained. A separate formal build/lint also passed, and its APK has no native libraries or new JNI mapping method names. Neither APK was published in this checkpoint. The phone still retains alpha3. Next action is a matching UI-helper build/readback, followed by bounded owner M1 real-media sampling, with actual dependency SHA verification and preserved CPU limits. M5 now carries friend daily formal sessions and is not interrupted by this candidate. New classified mapping fields have no Android runtime or concurrent sampling-overhead acceptance yet.
