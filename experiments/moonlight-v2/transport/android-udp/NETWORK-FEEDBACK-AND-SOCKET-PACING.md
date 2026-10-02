# Experimental network feedback and actual socket pacing

This is an isolated component experiment. Production apps/services, account
configuration, public endpoints, and NAT/P2P routing are not changed. Flags are
off by default. It remains private-peer, physically-bound authenticated UDP.

## Runner switches

`run_phone_udp.py --network-feedback` asks the phone receive loop for interval
counters and enables a conservative encoder policy. It uses the existing
`ADAPTIVE_VBR` host encoder control, with `240 + uint32 BE bitrate` and the
existing `240 + uint32 BE accepted bitrate` reply. No new encoder API is implied.

`--socket-pacing` additionally schedules video at the actual Python socket,
after the native stdout pipe. It uses the existing `--wire-bitrate` budget,
40 Mbps by default, rather than applying a second low target at the nominal
4 Mbps encoder rate. Both native and socket pacers include every FEC shard,
24-byte authenticated envelope, 16-byte GCM tag, and 28-byte IPv4/UDP overhead.
The socket layer is a final scheduling guard against pipe backlog catch-up.
It does not promise to improve an already well-paced native stream.

`--diagnostic-events` requests bounded numeric native source/output, actual
socket, and phone inbox/receiver frame events. Native CLI arguments are
`wire_bitrate cooldown_us 1`; older binaries lacking this optional argument
must not be used for this switch. The caller supplies the independently built
packetizer and existing external runtime with `--packetizer` and `--runtime`.

## Authenticated feedback schema

All messages use the existing per-test HGUE AES-GCM envelope and replay window;
all outgoing lanes share a single fresh sequence space. No credentials or keys
are included in the report. Schemas below describe plaintext only.

HGUF is exactly 112 bytes: big-endian `>4sI13Q`, magic `HGUF`, version 1, then:

1. Feedback sequence.
2. Phone interval start monotonic microseconds.
3. Phone interval end monotonic microseconds.
4. Cumulative authenticated HGUD datagrams received.
5. Cumulative estimated video IPv4 wire bytes (`HGUE datagram length + 28`).
6. Cumulative completed logical frames.
7. Cumulative expired frames.
8. Cumulative reference-loss events.
9. Cumulative recovered FEC shards.
10. Cumulative receive-loop iterations over 80 ms.
11. This interval's maximum receive processing duration in microseconds.
12. This interval's maximum socket wait in microseconds.
13. Highest authenticated video frame id.

The phone normally sends at 100 ms intervals. The host validates intervals
between 50 ms and 2 seconds, monotonically increasing sequence/times/counters,
plausible byte deltas, and a host receive-rate limit. Delayed feedback can skip
sequences; a sequence gap is a missing **feedback** observation, not video loss.

HGPQ is a host echo query: 16 bytes, `>4sIQ`, magic `HGPQ`, host-issued id,
host monotonic microseconds. HGPR is 32 bytes, `>4sIQQQ`, magic `HGPR`, copied
id and host stamp, phone arrival microseconds, and phone reply-send microseconds.
The host sends at most one query per second and expires its bounded outstanding
queries after three seconds. RTT is host send to host receipt on one host clock.
The phone processing interval is reported separately; it is not treated as an
exact quantity that can be subtracted to obtain pure network transit latency.

## Encoder policy and coordination

This is **not GCC**: it has no packet-level transport-cc feedback, Trendline
estimator, bandwidth probe train, or independent path-capacity measurement.

The host evaluates windows of at least 500 ms. Two successive windows with
receiver assembly/reference pressure, receive-loop stalls, actual socket
deadline pressure, or a sustained RTT rise are required before proposing a
20% bitrate reduction. Updates are at least one second apart and require no
outstanding encoder target ACK. The target floor is 1 Mbps; the initial runner
request remains bounded to 4–24 Mbps.

RTT pressure additionally needs at least three accepted echoes received in the
last three host-clock seconds. Their median, the RTT EWMA, and the latest raw
RTT must all exceed `minimum_observed_RTT + max(20 ms, 50% of that minimum)`;
the latest echo must be less than two seconds old. The raw-sample condition
prevents two slow startup echoes followed by a fresh low RTT from continuing to
qualify through a temporarily high median and retained EWMA. A high startup
EWMA alone therefore cannot lower the target. This warm-up applies only to RTT:
assembly/reference loss and local socket pressure retain their independent
two-window response. These RTT measurements include phone processing and host
scheduling, so pressure is a cautious heuristic rather than proof of congestion.

After at least five stable seconds, recent feedback, adequate actual delivery,
and a matching encoder ACK, a probe can raise the target by at most 5% (steps
of at least 100 kbps are rounded down to 100 kbps units). It cannot exceed the initial target. Local oversized-IDR
recovery lowers a permanent ceiling for the session; network recovery cannot
raise that ceiling. A static/low-data screen does not prove extra path capacity
and therefore does not trigger upward probing merely because counters are clean.

Feedback disappearance clears the stable period and never causes automatic
upward probing. Recovered-shard counts and interval delivery ratios are retained
as evidence; neither is called an actual packet-loss rate. A delivery ratio alone
does not trigger a rate reduction because burst and feedback interval boundaries
can shift relative to one another.

Both local recovery and network changes acquire `recovery_lock`, then the
existing `control_lock`; touch uses the same `control_lock`. The actual socket
authentication lock is independent. The latest requested target waits for a
matching value ACK, with a two-second timeout. Rejection/timeout blocks upward
probing until a later accepted update. Legacy ACKs carry no request sequence;
an older reply with the same numeric value cannot be distinguished exactly, and
zero ACKs cannot identify a particular old request. Reports explicitly retain
this limitation. No exact sequenced-ACK guarantee or FIFO correspondence is
claimed.

## Pacing, deadlines, and reference safety

Video reserves a per-datagram slot using actual estimated wire bytes. It waits
outside the AES send lock and outside all control locks. Audio, touch ACKs and
echo queries send without waiting and charge their bytes to subsequent video
debt. This is an explicit priority policy: tiny intervals can burst, so it is
not a strict rate guarantee for every sub-millisecond time window. No credit
accumulates during idle periods for a later catch-up burst.

Only the native header's **host** monotonic capture and 80 ms lifetime are used
for send deadlines. Phone time is never mixed into that calculation. A shard
that cannot finish serialization by the deadline is not emitted. Each frame
tracks the original-data attempt bitmap of every block. Only after **all blocks'
original data** has passed to the sender does `media_data_complete` become true
and the source reference chain advance. This still does not prove phone receipt:
normal UDP loss and intentional pre-socket loss are handled by FEC/phone recovery.

Missing original data, unknown intermediate blocks, or an unexpected reference
invalidate the chain; all later dependent P frames are suppressed until a new
IDR's complete original data has been processed. Recovery requests share the
existing bounded cooldown. Missing only final-block parity after every block's
original data is complete does **not** invalidate the media reference or request
an IDR. Tail-parity deadlines have separate frame/datagram counters. If the native
producer omits final parity but outputs all original data, finishing the frame
also preserves the source chain and records incomplete FEC output separately.
`all_records_processed` (and its legacy alias `complete`) remains false for
these cases. It describes all native shard records passing to the sender, not
successful phone receipt or a promise of full FEC protection. Intentional
pre-socket fault injection remains explicit in lane
counters and does not artificially suppress subsequent media, so FEC/recovery
can actually be exercised.

Waiting at two layers can still add scheduler overhead. Before recommending
this mode, compare native-to-socket age, complete-frame send duration, socket
deadline drops, receiver pressure, actual phone Surface presentation gaps, and
bitrate ACKs against the default path. A smaller nominal socket rate can make
large IDRs miss 80 ms; increasing buffering is not used to hide this failure.

## Evidence and pure verification

The host report includes `network_feedback.interval_samples` (maximum 512),
policy actions/counters, raw host RTT, explicit encoder ACK status, and receiver
pressure deltas. RTT evidence exposes the latest raw sample, recent median,
recent echo count, warm-up readiness, freshness, and absolute pressure threshold
in both the final report and evaluated interval samples. `socket_video.frame_events` retains at most 10,000 numeric
metadata records: frame id, host capture/read/send times, actual sent datagram
and estimated wire-byte counts, `media_data_complete`, `all_records_processed`,
tail-parity deadline count, and drop/warning reason. Evictions are
counted. No media payload or touch coordinates are retained. `native_events`
is capped at 16,000; owned worker pipeline/phase samples are sanitized and capped
at 512. Host unix timing samples are not compared directly to phone monotonic.

Pure tests in `tests/test_udp_network_feedback.py` and
`tests/test_udp_socket_pacing.py` cover missing/out-of-order/flooded feedback,
counter bounds, ACK timeout, stable bounded recovery, echo-token validation,
startup-high/recent-low RTT, sustained high RTT after warm-up, independent local
pressure during RTT warm-up,
full-byte scheduling, idle catch-up prevention, priority traffic, lock isolation,
partial original-data expiry, reference-chain recovery, complete-data/incomplete-
parity cases, nonce safety after parity rejection, and metadata bounds. They do
not establish real-phone playback improvement, WAN behavior, V50 performance,
or touch-to-photon latency; those remain separate measured acceptance layers.

## Confirmed parity-only correction

The initial matrix's `paced-02.json` frame 714 had complete native output:
145,952 estimated IPv4 wire bytes across 128 shards. The socket sent 127 shards
(144,928 bytes); the remaining final parity shard missed the 80 ms deadline.
Phone diagnostic events nevertheless recorded FEC quorum and successful IDR
delivery for frame 714. The former conservative guard skipped source P frames
715–719 and waited for another IDR 720, creating unnecessary missing media.

The correction distinguishes complete original media from complete FEC record
processing. The pure tests exercise the same parity-only boundary, original-
data deadline failure, complete/missing data across multiple blocks, native
tail omission, and fresh AES nonce behavior. The old matrix documents the
problem; it is not a post-correction playback acceptance result. The evidence
path is `docs/evidence/udp-feedback-20261001/matrix/paced-02.json` relative to
the repository root.

The same initial matrix's `feedback-01.json` lowered the encoder target while
its pressure windows had only RTT pressure, with an EWMA retaining the initial
slow echoes; assembly/reference loss and local receive pressure stayed absent.
The new RTT warm-up and recent-sample checks address that false-pressure risk
in pure tests. The old run is diagnostic evidence, not playback acceptance for
this changed policy; any resulting improvement still needs a new phone run.
