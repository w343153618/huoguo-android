# Bounded encoder recovery after an oversized IDR

The experiment previously marked an oversized IDR as already requested, blocking
its dependent P chain until a naturally occurring smaller IDR. That avoided
corrupt pictures but could freeze output for multiple GOPs. This iteration keeps
the dependency protection and changes recovery, without modifying production
gateway, emulator, phone, NPS, or accounts.

The packetizer now emits immediate encoder-budget feedback and at most six IDR
requests per broken-chain epoch, no more than one per 500 ms. Failed IDRs never
reset the bound. Only a complete, accepted IDR resets recovery. Exhaustion is
explicit; future natural valid IDRs are still accepted. Requests are driven by
incoming source AUs, so complete source stalls are a separate concern.

The controller uses actual full encrypted IPv4 wire bytes, including HGUD/HGUE
headers, GCM tags, IPv4/UDP, and FEC. Capacity is
`wire_bitrate_bps * 80000 / 8000000` bytes. It leaves 15% scheduling margin,
reduces by 15–50% per allowed action, rounds down to 100 kbps steps, and never
goes below 1 Mbps. These are bounded recovery defaults, not a claim that an IDR
size is linear in AverageBitRate. Targets never increase automatically.

Valid feedback arriving inside the cooldown is retained. The next allowed IDR
request first applies its pending reduction; otherwise an oversized IDR arriving
just after each request could repeatedly fall inside cooldown and never adapt.
Successful recovery clears stale pending feedback while preserving the lowered
target and cooldown.

## Host integration

The owner must create `HostHardwareSession(..., 'ADAPTIVE_VBR', ...)`, keep all
control writes under one lock, and dispatch whitelisted native events:

```python
from recovery_controller import RecoveryController

recovery = RecoveryController(initial_bitrate_bps, wire_bitrate_bps)

# Existing event reader; keep complete/exhausted as well as request/budget events.
decision = recovery.consume(event)
if decision is not None:
    with control_lock:
        channels['control'].sendall(decision.command_bytes())
    # Record decision.report(); do not report it as accepted until acknowledged.
```

`command_bytes()` returns `0xf0 + uint32BE target` when the target changes,
followed by `0x11` for IDR. **The target opcode is 240, not 16**: opcode 16 is an
unrelated variable-length scrcpy control message. The existing hardware worker
accepts target changes only in ADAPTIVE_VBR and returns `0xf0 + uint32BE`;
`recovery.acknowledge(value)` records this reply, with zero recording rejection.
The report distinguishes requested target from the last encoder acceptance
reply. Late replies never raise the desired target.

The controller handles `encoder_budget_feedback`, `request_idr`,
`recovery_complete`, and `recovery_exhausted`. Other events are ignored. Numeric
bounds, exact reason/action strings, expected wire bitrate, and monotonic clock
are validated. Its own six-action limit is reset only by `recovery_complete`.
Do not additionally discard an entire target-plus-IDR command due to a separate
IDR cooldown. A shared owner policy for native and phone requests can be added,
but must retain target changes and pending observations.

## Reproducible checks

From the canonical source tree, build into a separate owned temporary directory:

```sh
python3 -m unittest discover -s tests -p test_udp_recovery_controller.py -v
python3 experiments/moonlight-v2/transport/android-udp/build.py \
  --build /private/tmp/huoguo-udp-recovery-build
python3 experiments/moonlight-v2/transport/android-udp/verify_recovery.py
python3 experiments/moonlight-v2/transport/android-udp/verify_native_roundtrip.py \
  --build /private/tmp/huoguo-udp-recovery-build/host \
  --output experiments/moonlight-v2/transport/android-udp/evidence/recovery-normal-roundtrip-20261001.json
```

External requirements are the existing ffmpeg/ffprobe and the clean pinned
Moonlight/nanors checkout described in [NATIVE-TRANSPORT.md](NATIVE-TRANSPORT.md).
No downloads or services are created. Generated H.264 and executable files remain
in `/private/tmp`; saved evidence includes numeric/status measurements only.

The offline source is CPU-generated `testsrc2`, physical 720×1280, 60 fps,
Baseline with one reference and no B frames. A valid 120,000-byte filler NAL
is added to one IDR; decoding that modified source produces identical frame MD5s.
This intentional fault has 185,502 full wire bytes against the fixed 80,000-byte
capacity at 8 Mbps / 80 ms. A scripted encoder responds to the actual controller
command using an already encoded small IDR. That proves command order and
recovery/dependency policy; it does **not** prove real VideoToolbox adaptation.

The 2026-10-01 regression produced these results:

| Condition | Complete recovery IDR after budget feedback | Result |
| --- | --- | --- |
| Owner ignores adaptation and waits for its 2 s natural GOP | 2033.068 ms | 119 unsafe dependent P frames blocked; one valid IDR decoded |
| Owner applies proposed 8→4 Mbps command and supplies small IDR | 23.179 ms | All 120 restored frames decoded with exact MD5 match |
| 68 consecutive oversized IDRs | No output | Six requests, one exhaustion event, zero unsafe output |

The complete recovery interval is packetizer feedback-to-full-IDR output. It
is **not** measured capture-to-phone latency. The small IDR's encoding time was
outside that interval. The result does not establish phone display FPS,
real-video streaming, UDP socket behavior, WAN throughput, NAT punching, native
touch, audio, or synchronization. Exact timings vary with the host load.

The normal native roundtrip also returned all 120 frames with exact decoded MD5s
under zero loss, deterministic removal of two data shards per FEC block plus
reordering, and seeded 2% random loss plus reordering. This is a CPU/stdio/FEC
regression, not a phone or WAN result.

Next validation is an actual 8 Mbps M1 video source with a fixed 8 or 12 Mbps wire
budget, observing accepted VideoToolbox target, IDR size, time without output,
received/actually displayed FPS, and phone frame expirations. That integration
is owned by the parent experiment. Do not enlarge buffering or wire budget to
hide oversized-IDR failures.
