# Experimental receive feedback and bounded diagnostic metadata

The separate instrumentation session accepts two optional booleans:
`network_feedback` and `diagnostic_events`. Both default to `false`. The actual
video assembly, reference gating, 80 ms assembly deadline and production APK
remain unchanged. The feedback originates on the socket receive thread, not
the codec worker. In `async_video` mode it does not wait for codec input.

These are experimental measurements, not a complete congestion controller or
evidence of WAN, V50, physical screen FPS, touch latency or AV synchronization.
No packet/media content, screenshots, keys or credentials are logged.

## Authenticated feedback wire format

All payloads use the existing HGUE AES-GCM envelope with the existing
directional unique nonce and replay checks. The touch, recovery and feedback
senders share the same synchronized client nonce counter. Byte orders are BE.

`HGUF` is exactly 112 bytes, Python struct `>4sI13Q`: magic `HGUF`, version 1,
then the following unsigned 64 bit values. Java rejects negative values, zero
sequence/start time and an interval whose end precedes its start.

| Column | Value | Meaning |
| --- | --- | --- |
| 0 | feedback_seq | Monotonic session sequence, starting at 1 |
| 1 | interval_start_phone_us | Previous snapshot time on the phone |
| 2 | interval_end_phone_us | Current snapshot time on the phone |
| 3 | authenticated_HGUD_datagrams_total | Video datagrams after GCM/replay validation, including parity/duplicates and subsequently invalid HGUD bodies |
| 4 | estimated_HGUD_IPv4_wire_bytes_total | Entire HGUE datagram size plus 28 bytes IPv4+UDP header per video datagram; excludes Ethernet, VPN, IPv6 and radio overhead |
| 5 | completed_logical_frames_total | Native validated complete bodies, before codec inbox admission |
| 6 | expired_frames_total | Core frame expiration counter |
| 7 | reference_lost_total | Core reference-chain losses |
| 8 | recovered_shards_total | Shards reconstructed by FEC |
| 9 | receive_loop_over80ms_total | Complete receive-loop iterations exceeding 80 ms, including socket wait |
| 10 | interval_processing_max_us | Maximum processing time excluding receive wait, including diagnostic drain; current feedback-send pressure appears in the following interval |
| 11 | interval_socket_wait_max_us | Maximum socket receive wait including timeouts; this is not a network one-way latency |
| 12 | highest_authenticated_frame_id | Highest HGUD frame ID observed after authentication |

Counters are cumulative; the host validates monotonically increasing sequence,
phone interval bounds and cumulative deltas. It derives rates from **phone
interval duration**. Feedback is sent around every 100 ms after first media.
Recovered shards, reference losses and frame expiry are loss proxies; none is
a measured network packet-loss percentage. Global processing and feedback-send
maxima are also recorded in the final phone report. A datagram socket send may
still fail and ends the bounded experimental session explicitly.

`HGPQ` request is 16 bytes, `>4sIQ`: magic, ping ID and host monotonic timestamp.
`HGPR` reply is 32 bytes, `>4sIQQQ`: magic, the **exact opaque 12-byte request
token**, phone arrival time and phone send-preparation time (both us). Replies
are limited to one per 50 ms; a normal 100 ms ping is not throttled. Arrival is
sampled after socket receive and before authentication; send time is sampled
before seal/send. Only the host computes RTT from its own send/receive clock.
Phone reply processing duration is separately useful but cannot be subtracted
to claim a precise pure-network RTT. Never subtract phone and host clocks.

## Final report: native_frame_events

Native metadata storage holds at most 256 events, drops oldest and counts
evictions. JNI drains at most 64 rows per call. The phone drains up to 128 every
100 ms, stores at most 8192 converted events, and reports both eviction counts
and any pending native events. No full stats JSON object is built per packet.
The receive thread owns this array; it is snapshotted after receive ends.

Each event is an object with `event`, `reason`, `pts_available`, and the
numeric fields below. Raw JNI rows contain 14 columns with `type` at index 0;
the JSON substitutes its readable `event` name for `type`.

| Field | Meaning |
| --- | --- |
| frame_id | Transport frame identity |
| host_capture_us | Original host capture clock token, not comparable with phone time |
| reference_id / flags | Transport dependency metadata |
| first_arrival_us / last_arrival_us | Phone socket arrival times of shards seen while this mapping is active; later parity after retirement is excluded |
| fec_quorum_ready_us | First mathematical per-block FEC shard quorum, zero if never reached; this is not a validation/decode-ready timestamp |
| event_phone_us | RX arrival/tick decision time supplied to native; excludes native CPU duration |
| deadline_phone_us | First arrival plus the fixed assembly lifetime |
| logical_bytes | Transport-declared complete logical body size |
| reason_code | Numeric reason below |
| event_sequence | Native monotonic event sequence; gaps identify eviction |
| pts_us | Validated logical presentation token when `pts_available` is true; otherwise zero/absent semantics |

| Type | Event | Reason |
| --- | --- | --- |
| 1 | fec_quorum_ready | 0 none |
| 2 | frame_delivered | 1 keyframe or 2 predictive |
| 3 | frame_expired | 3 assembly_deadline |
| 4 | settled_by_delivered_frame | 4 superseded_by_delivered_frame |
| 5 | logical_rejected | 5 malformed_logical_body or 6 logical_chain_blocked |

Type 2 means validated native body delivered to Java, **not codec callback or
physical screen presentation**. Type 4 means an older unresolved mapping was
retired after delivery advanced; it does not assert that it decoded. Ordered
delivery can occur after the mathematical quorum. Expiry, quorum and delivery
have distinct meanings and may need the final native counters for context.

## Final report: inbox_epoch_events

The synchronized complete-AU inbox holds at most 256 pure metadata events and
counts oldest-record eviction in `video_input_queue.epoch_events_evicted`.
JSON construction happens only after the worker has joined; a join timeout
leaves the worker array unsnapshotted. The existing 4-frame/2-MiB queue limits,
reference gating, decoder reservation cancellation and close semantics remain.

Fields: `event`, `reason`, `epoch`, `previous_epoch`, `time_ns`, `pts_us`,
`received_ns`, `queue_frames`, `queue_bytes`. All times are phone monotonic ns;
PTS is a presentation token to join with a validated native body. The complete
body does not carry frame ID, so the inbox does not invent one. For
`chain_lost`, `epoch` is the resulting epoch and `previous_epoch` is the
invalidated epoch; queue sizes describe what was cleared.

Events include `idr_admitted`, `recovered`, `chain_lost`, `stale_fail_ignored`.
Loss reasons include `oversized_au`, `queue_frame_overflow`,
`queue_byte_overflow`, `worker_complete_au_expired`, `geometry_without_idr`,
`codec_input_timeout`, `stale_codec_epoch`, `codec_stopped`. A successful
initial or recovery IDR produces `initial_idr_committed` or
`epoch_idr_committed`; admission alone does not mean recovery completed.

## Reproducible offline verification and build

From the repository root:

```sh
python3 scripts/probes/check_udp_video_inbox.py
python3 scripts/probes/check_udp_feedback_diagnostics.py
python3 experiments/moonlight-v2/transport/android-udp/build.py \
  --build /private/tmp/huoguo-udp-feedback-phone --android
python3 scripts/probes/build_phone_transport_probe.py \
  --udp-native-library /private/tmp/huoguo-udp-feedback-phone/android-arm64/libhuoguo_udp_fec.so
```

Existing external SDK/JDK, matching built app classes and pinned clean
Moonlight/nanors checkout are required. The check script documents configurable
paths via `--help`, builds in a temporary directory, and neither installs an
APK nor accesses a phone, emulator, service or network. The native fixtures are
synthetic logical Annex B bodies for metadata/FEC verification; they are not
decoded videos or a performance test. Root owns real session installation and
acceptance. APKs/libraries are generated build artifacts and are not committed.
