# Experimental native UDP video transport

This directory contains an experiment, not a replacement deployed to production.
`h264_udp_packetizer` consumes the existing host's framed Annex B H264 channel.
It produces IPC records (`uint16BE packet length`, then an HGUD plaintext packet)
for the host's authenticated/encrypted UDP wrapper. It does not open a socket.
The Android JNI library accepts only packets already authenticated by Java.
There is no media TCP fallback in these components.

## Build

From the canonical project directory:

```sh
python3 experiments/moonlight-v2/transport/android-udp/build.py
python3 experiments/moonlight-v2/transport/android-udp/build.py --android
python3 experiments/moonlight-v2/transport/android-udp/verify_native_roundtrip.py
```

Required external dependencies are the clean, pinned Moonlight/nanors checkout
under `~/.cache/huoguo-v2-sources/moonlight-common-c`, an existing CMake, and the
existing Android NDK `~/Library/Android/sdk/ndk/29.0.14206865`. No download,
dependency vendoring, emulator, phone, cloud, or production-service change is
performed. `build.py` checks dependency commit pins before CMake.

Default outputs are outside Git:

- `/private/tmp/huoguo-android-udp-build/host/h264_udp_packetizer`
- `/private/tmp/huoguo-android-udp-build/host/phone_receiver_probe`
- `/private/tmp/huoguo-android-udp-build/android-arm64/libhuoguo_udp_fec.so`

Android is built for arm64-v8a / API 23 with static libc++, so packaging needs
only `lib/arm64-v8a/libhuoguo_udp_fec.so`. No generated library is committed.

## Producer contract

Invocation is `[packetizer, wire_bitrate_bps]`, default 40000000, allowed
500000..40000000. The host marker is `h264`; geometry records are
`0x80000000,width32,height32`, all big endian. Media records are
`flaggedPTS64,payloadLength32,AnnexB payload`. Bit 62 is CONFIG, bit 61 is IDR;
bit 63 is reserved for geometry. PTS occupies bits 0..60.

The logical frame body is:

| Offset | Field |
| --- | --- |
| 0 | width32 BE |
| 4 | height32 BE |
| 8 | flaggedPTS64 BE; media must not have CONFIG bit |
| 16 | configLength32 BE |
| 20 | CONFIG bytes (IDR only; max 65536), followed by complete Annex B AU |

The complete body is at most 1 MiB. Geometry changes are read from the source,
not hardcoded. A strict header gate requires profile 66 Baseline, progressive
4:2:0, one reference, P/I slices, no B/SP/SI, CABAC, FMO, field picture,
weighted reference, long-term reference, reference-list modification or MMCO.
Unsupported source syntax fails closed. This is a supported-subset gate, not
a complete H264 decoder or a claim of arbitrary codec compatibility.

HGUD is the existing 56-byte-header core format with up to 1024-byte shards and
10 data + 2 Reed-Solomon parity shards per full block. The final block may have
fewer data shards. Frame IDs increase for every source AU; rejected reference
frames remain in the dependency chain, preventing a later P frame from being
misrepresented as independently decodable. Keyframes reset that chain.

The 40 Mbps default is a **short-time wire budget**, independent of the 4 Mbps
encoder target. Scheduling includes each HGUD packet plus 24 bytes HGUE outer
header, 16 bytes AES-GCM tag, and 28 bytes IPv4/UDP overhead. It does not include
Ethernet, Tailscale/WireGuard, IPv6, or cellular radio overhead. A full frame is
preflighted against 80 ms; rejected frames do not reserve future pacer debt.
An oversized IDR emits `encoder_budget_feedback` asking its owner for a smaller
IDR or lower encoder bitrate, not unlimited retries of the same large frame.
After a rejected reference/IDR, bounded recovery requests continue at most once
per 500 ms while source AUs keep arriving. Each broken-chain epoch permits six
requests; only an IDR fully emitted before its deadline resets that count.
Receiving another failed IDR does not reset it. Exhaustion emits one
`recovery_exhausted` event and keeps unsafe dependent P frames blocked. A later
valid natural IDR can still restore the chain. No background source-stall timer
is implemented in this packetizer.

Stdout is nonblocking and deadline limited. If the downstream wrapper stalls,
expired frames are not indefinitely queued. An expiry inside a partially written
IPC record aborts the producer to avoid continuing a corrupted length stream.
The owner must bound its Python/socket queue too: producer pacing cannot observe
delay after bytes have entered the wrapper's pipe. Capture timestamps are taken
when the complete source AU reaches the packetizer, not at original emulator
capture. Summary statistics therefore explicitly describe the packetizer/stdout
stage, not capture-to-phone latency.

Stderr emits bounded numeric/status JSON events: `summary` (roughly once per
second while processing plus on normal EOF), `request_idr`,
`encoder_budget_feedback`, `recovery_complete`, `recovery_exhausted`, and
`frame_rejected`. Forced SIGTERM can precede a
final EOF summary; callers should retain the latest cumulative periodic summary.
No credentials or encoded picture bytes are logged.

`recovery_controller.py` consumes these events and proposes downward encoder
target changes, retaining the wire budget and 80 ms assembly limit. Its interface,
actual hardware control command framing, and offline regression are documented in
[RECOVERY-CONTROLLER.md](RECOVERY-CONTROLLER.md). It is an experimental local
encoding recovery policy, not completed production WAN congestion control.

## JNI and clock boundary

`local.remoteandroid.direct.NativeUdpFec` exports static natives:

```java
long nativeCreate();
byte[][] nativeAccept(long handle, byte[] authenticatedHgud, long arrivalUs);
byte[][] nativeExpire(long handle, long nowUs);
long[] nativeStats(long handle);
void nativeDestroy(long handle);
```

Phone arrival/expiry use a positive monotonic microsecond clock. Host monotonic
timestamps are never compared directly with it. Each frame gets a stable local
timestamp from its first authenticated shard; all shards must agree on original
host timestamp, flags, reference, length, blocks and lifetime. The local deadline
is first-arrival + lifetime (at most 80 ms). This is an **assembly budget**, not
a capture/WAN deadline and not a measured end-to-end latency guarantee.

At most eight clock mappings and eight core pending frames exist. A 256-entry
retired-frame window plus high-water checks prevents old shards from repeatedly
receiving fresh assembly budgets. Frames that expire are retired; late parity
for already delivered frames is settled rather than recreating a mapping. When
a new IDR bypasses older unresolved frames, all their mappings are immediately
retired instead of consuming mapping capacity until the original deadline.
Registry handles are bounded IDs, not exposed raw native pointers; per-state
locking and shared ownership make concurrent destroy/accept safe.

`nativeStats` returns 21 cumulative integers in this order:

| Index | Meaning |
| --- | --- |
| 0..4 | accepted JNI input packets, plaintext HGUD bytes, invalid, duplicate, settled |
| 5..8 | core expired packets, frames expired, frames delivered, FEC recovered data shards |
| 9..12 | lost references, core keyframe requests, dependency drops, core memory rejection |
| 13..16 | mapping rejection, active mappings, mapping evictions (currently zero), expired mappings |
| 17..20 | rejected logical bodies, completed logical bodies, needs-keyframe (0/1), maximum assembly microseconds |

Mapping-expired late packets count as settled after retirement, so the core's
expired-packet counter alone is not an expiration-total counter. FEC recovery
also includes data reconstructed when parity arrives before still-in-flight data;
it cannot by itself be interpreted as measured network packet loss.

## Validation and unverified boundaries

`phone_receiver_probe --self-test` checks synthetic control bodies (not decodable
H264): unrelated host/phone clocks, two missing data shards per block, exact
reconstruction, settled replay, conflicting timestamps, expiry without deadline
restart, IDR recovery, eight mappings, old replay, monotonic regression, config
length bounds, and immediate cleanup after a newer IDR bypasses older incomplete
frames (13 checks). CTest passed. Android NDK compilation also passed, with all five
JNI exports built, but compilation alone is not phone execution.

The offline fixture is CPU-generated 540×1200/60 fps H264, Baseline, one reference,
no B frames, target 4 Mbps and maxrate 8 Mbps. At the 40 Mbps producer budget all
120 AUs were emitted without sender deadline or admission drops. Zero loss,
deleting two data shards in every block with groups of three reordered packets,
and seeded random 2% loss plus reordering each returned all 120 decodable frames.
Their decoded frame MD5 lists exactly matched the original, with zero ffmpeg
decode diagnostics. Evidence is `evidence/native-roundtrip-20261001.json`.

That evidence is stdio framing/FEC/CPU decoding; it does **not** measure UDP
sockets, WAN/NAT, AES-GCM, Android source/video apps, VideoToolbox, JNI execution
on the phone, phone decoder/display, native touch, audio, or AV synchronization.
Actual phone UDP verification is the next layer owned by the parent experiment.
