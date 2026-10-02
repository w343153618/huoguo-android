# Real video latency and reliability iteration — 2026-10-01

本轮完成 24 组真机 YouTube UDP 串流测试（每组 35 秒），以及两组仅抓取
测试。M1 虚拟机仍为 6 核、16 GiB、720×1280；串流输出为 540×960。

可重复的收益是关键帧发包/组帧时间：16→32 Mbps 瞬时发包上限，按
100 KiB 归一化约 68→35 ms。平均视频编码目标仍为 4 Mbps。最后两轮
1080p60 源视频呈现 58.12/58.32 FPS，稳态窗口没有 >100 ms 呈现空档；
后一轮注入约 2% 分散视频包丢失，FEC 恢复 269 个数据分片。

清除帧率提示、降低源视频到 720p60、晚提交 Surface 都未显示可重复的
整体收益。30 ms 缓冲可少约 30 ms 排期等待，但余量更小，保留 60 ms
作为实验基线。源端已测到 100–250 ms 级供帧空档，尚未细分其内部解码/
合成原因。不能宣称已经稳定 60/120 FPS，也尚未证明公网或 V50 的效果。

手机两个原 APK 已通过 SHA-256 读回恢复，原刷新率设置已恢复。M1 网关
和下载服务均 HTTP200，虚拟机 Awake，全部本轮测试进程已退出。

## Scope and acceptance boundary

This iteration uses the existing M1 `RemoteAndroid17Compare` instance and the
USB-connected OnePlus 15. The guest remains 6 CPU cores, 16 GiB RAM,
720×1280 at 320 dpi. Real public Morphe YouTube playback is the source:
`https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=60s`. The player UI offered and
was observed using 1080p60 and 720p60. A UI quality label is not proof of 60
unique decoded content frames.

The network path is M1 wired interface `en7` → home LAN → phone Wi-Fi.
Video, AAC audio and native touch acknowledgements use authenticated UDP,
AES-256-GCM and FEC 10+2 in the isolated component. There is no cloud/NPS,
cellular, remote V50, optical touch latency or acoustic lip-sync acceptance in
this iteration. The guest capture/control pipe remains local to the host;
the phone media transport is UDP.

Streams request 540×960, H.264, a 60 FPS cap, a 4 Mbps video target, and
30/60 ms buffers. The encoder explicitly requires and reads back Apple
hardware encoding. A wire pacing ceiling of 16/32 Mbps is a separate
parameter from the average video target. Adaptive recovery/feedback can
lower the accepted video target; actual encoder ACKs are retained.

Every real UDP sample lasts 35 seconds. Comparisons use autonomous phone
SurfaceFlinger presents in the fixed 5–30 second window after the first
authenticated server packet. Decoder callbacks, requested presentation
targets and SurfaceFlinger presents are separate observations. Screenshots
were used only to operate/verify the player quality menu, never to estimate
FPS. All unsuccessful and degraded samples are retained.

## Captured bottleneck evidence

Opt-in bounded capture traces now join screenshot PTS and capture sequence
across gRPC return, raw queue, pipe, pixel conversion, VT submission/callback
and encoded egress. Host traces explicitly use one CLOCK_MONOTONIC domain.
Phone associations use the same screenshot PTS, not subtraction of unrelated
host and phone clocks. The schema and limitations are documented in
[capture-vt-trace-schema-20261001.md](capture-vt-trace-schema-20261001.md).

Across 5,807 joined submitted frames from the hint matrix, steady-state
pixel conversion p50 is 0.085–0.088 ms; VT submit→callback p50 is
7.120–7.191 ms. No associated copy, VT, slot, pipe or stdout phase exceeds
50 ms. VT callback elapsed time includes scheduling and is not pure media
engine execution time. Native traces lack the final summary after termination
and are incomplete prefixes; zero native diagnostic drops are not asserted.

Three useful same-PTS associations:

- Non-IDR capture 575→576 in `hint0-01`: gRPC gap 102.541 ms,
  screenshot PTS gap 92.917 ms, phone complete-frame gap 104.530 ms, right
  frame assembly 0.081 ms. This supplies evidence of an upstream pause.
- Capture 428→429 in `hint60-01`: gRPC gap 53.039 ms, screenshot gap
  52.898 ms, local capture→encoded socket 8.111/8.252 ms; right IDR
  assembly 57.612 ms and phone complete-frame gap 112.379 ms. A source
  pause and keyframe serialization compound.
- The 255.105 ms phone stall in buffer30 round 2 matches a non-IDR source
  PTS gap 252.530 ms, gRPC gap 259.284 ms and local capture→encoded socket
  9.231 ms. This event cannot be blamed solely on a small playback buffer.

Two additional 35-second capture-only runs remove VT, audio and remote media
load. Capture rates are 48.775 and 54.337 FPS; maximum gRPC gaps 170.315 and
169.178 ms. Concurrent source SurfaceFlinger also pauses (max 163.838 and
169.078 ms). Removing several loads does not isolate the VT contribution,
but source pauses persist without the encoder or phone network path.

Detailed evidence: [hint capture windows](evidence/surface-capture-20261001/hint-matrix/capture-window-analysis.json),
[buffer analysis](evidence/surface-capture-20261001/buffer-matrix/buffer-independent-analysis.md).

## Controlled results so far

| Condition | Phone SF FPS, two rounds | Maximum present gap, ms | Decision |
|---|---:|---:|---|
| Content hint 60, 60 ms buffer | 57.56 / 57.68 | 66.40 / 143.82 | Retain current hint |
| Clear content hint, 60 ms buffer | 56.00 / 56.40 | 110.90 / 144.20 | No measured improvement |
| Buffer 60 ms | 56.92 / 58.44 | 144.17 / 66.38 | Retain as comparison baseline |
| Buffer 30 ms | 56.96 / 52.04 | 88.73 / 255.11 | Less presentation margin; no general recommendation |

The worst 30 ms sample has severe upstream pauses too, so this matrix does
not establish that the buffer caused the source slowdown. Same-PTS arrival
to requested target p50 changes from 75.689/76.252 ms at buffer60 to
46.212/46.382 ms at buffer30. This proves the clock margin changes, not
that complete touch-to-photon latency drops by the same amount. Audio has
only whole-session counters; no matched-window acoustic A/V skew is available.

Changing source quality to 720p60 yields phone SF 54.40–55.64 FPS across
four samples. It does not show an improvement over the earlier 1080p60
samples. The guest physical resolution never changes from 720×1280.
The original player setting is restored to 1080p60 after this comparison.

## Repeatable improvement: keyframe wire pacing

| Source720 sample | Wire ceiling | Successful IDRs in window | IDR first→complete p50/p95, ms | Size-normalized p50, ms/100 KiB |
|---|---:|---:|---:|---:|
| First 16 Mbps | 16 Mbps | 13 | 59.633 / 73.092 | 68.499 |
| 32 Mbps round 1 | 32 Mbps | 12 | 27.961 / 35.412 | 34.632 |
| 32 Mbps round 2 | 32 Mbps | 12 | 28.728 / 37.405 | 34.711 |
| Repeat 16 Mbps | 16 Mbps | 12 | 52.333 / 60.880 | 68.158 |

Actual socket write spans show the same reduction. The repeated 16 Mbps
sample adapts its accepted encoder target from 4 to 3 Mbps, producing
smaller IDRs; the normalized comparison retains this difference. Only
successfully assembled frames receive a completion time. Failed delivery
is retained separately.

This is a repeatable reduction in a particular latency component, **not**
stable 60 FPS or proof of a WAN-safe bandwidth setting. Overall phone SF
is 54.40 / 54.72 / 55.64 / 55.52 FPS in those four samples, and capture
gaps remain. The 32 Mbps wire option is retained for later network-aware
tests while the average video target can stay at 4 Mbps.

Detailed data: [source quality/wire analysis](evidence/surface-capture-20261001/source-quality-wire-analysis.json).

## Actual display mode and Surface submission experiment

Window requests are hints. The initial requested 120 Hz samples and later
requested 60 Hz samples still read back 90 Hz. A temporary system refresh
range override produces 60 Hz for one sample but unexpectedly reads back
120 Hz in its second sample, although both requested 60 Hz. The two samples
present 52.28/52.20 FPS, but they are **not two valid fixed-60 replicates**.
The original system values (`min_refresh_rate` absent; `peak_refresh_rate`
165.0) were restored and read back. No universal fixed-60 conclusion is made.

The new opt-in Surface submission hook compares the original immediate
future-target submission (`0`) with submission within 16 ms of the same
target. It preserves PTS, codec setup, late-output policy and the shared
audio clock. Per-output waits are bounded; the normal client flag remains
zero and is never saved in preferences. Details:
[surface_submit_experiment.md](../scripts/probes/surface_submit_experiment.md).

The 90 Hz mode readbacks match in all four ABBA samples:

| Surface submission policy | SF FPS, round 1/2 | Maximum present gap, ms, round 1/2 |
|---|---:|---:|
| Original immediate submission, lead0 | 57.28 / 58.08 | 77.64 / 88.71 |
| Wait until target−16 ms, lead16 | 56.32 / 57.48 | 144.19 / 88.72 |

There is no repeatable gain. Input→output-dequeue-ready p50 rises from
approximately 19.6 ms to 40.8–42.6 ms: the output thread observes the next
decoded output later while holding the previous one. This is not a
measurement that hardware decoding computation doubles. The lead16 samples
each reach the 80 ms wait budget once, and real OS parks sometimes exceed
their requested 2 ms duration. The feature stays opt-in and disabled.

A further lead0/lead16 pair requesting system/window 60 Hz presents
52.00/53.32 FPS, but mode readbacks are 60 and 120 Hz respectively. In the
latter sample the SF header additionally reports a 60 Hz period, so mode
readbacks and effective app cadence cannot be substituted for each other.
It is not a valid matched fixed-mode A/B and is not used to claim a gain.

The test client and instrumentation APKs were temporarily replaced with
matching-signature builds. Both original installed APKs were restored and
pulled back: their SHA-256 values exactly match the pre-test copies. No
app data clear/uninstall occurs. The min/peak refresh settings were restored
to their original absent/165.0 values. Private backup APKs were deleted only
after hash equality. Restoration evidence:
[surface-submit-client-restore.json](evidence/surface-capture-20261001/surface-submit-client-restore.json).

## Final 1080p60 source and controlled-loss checks

The restored original client and probe are used for the final two 35-second
experiments. Source UI is restored to 1080p60, stream output remains
540×960, average accepted encoder target remains 4 Mbps throughout both,
wire ceiling is 32 Mbps, buffer60 and the original Surface submission path
are retained. Phone mode start/end reads 90 Hz in both.

| Final condition | Phone SF FPS, 5–30 s | Maximum present gap, ms | Gaps >100 ms in window |
|---|---:|---:|---:|
| No injected packet loss | 58.12 | 55.45 | 0 |
| Every 50th video datagram dropped before socket | 58.32 | 88.73 | 0 |

The second case explicitly drops 346 video datagrams (approximately 2%).
Receiver FEC reports 269 recovered data shards, zero frame assembly expiry,
zero lost references, and zero dependency drops. Lost parity packets need
not result in recovered data shards, so these counts should not be equated.
Both sessions have a decoder inbox overflow and recovery during startup,
before the steady comparison window. They are not zero-drop sessions.
There is no new encoder target downshift, sender deadline frame loss or
send error in either case.

This validates real-video transport under one controlled dispersed-loss
pattern on a LAN. It does not emulate cellular random/burst loss, cross-ISP
congestion, NAT traversal or a cloud relay. A single good clean run also
does not prove a sustained stable 60 FPS source.

Evidence: [final-wire32-matrix.json](evidence/surface-capture-20261001/final-wire32-matrix.json).

## Artifacts, checks and final live state

There are **24 real-video UDP samples ×35 seconds (840 requested seconds)**
and **two capture-only samples ×35 seconds** in this iteration. The same
public video is reused; these are controlled condition samples, not 24
different videos. Compact numeric index:
[iteration-summary.json](evidence/surface-capture-20261001/iteration-summary.json).

Thirty Python capture/trace/host regression checks passed. Current
MainActivity and UDP probe sources compiled; 85 Java feedback/parameter/wait
checks and 22 FIFO/recovery checks passed. The release target and separate
instrumentation APKs built successfully. Changed tracked source whitespace
checks passed. No upgrade is published to the production download entry.

Final readback confirms guest6/16384MiB/720×1280/320dpi, host GPU enabled,
guest Awake, gateway `/ping` HTTP200, download page HTTP200, no owned test
workers remaining, and fixed phone test session/report files absent. The
M1 environment remains running for future tests. M5, NPS and production
routes are preserved. Details:
[final-live-state.json](evidence/surface-capture-20261001/final-live-state.json).

## Decoder path and remaining questions

Live guest GLES readback is the Apple M1 Max emulator translator with
`OpenGL ES 3.0 (4.1 Metal - 91.7)`, not a SwiftShader renderer. The live
emulator already has `ANDROID_EMU_MEDIA_DECODER_VTB=1`. This is not a missing
switch to enable. Source playback hardware decode is still a separate
question from the hardware encoder used for remote streaming.

Current H.264 host decoding can use VideoToolbox and can fall back; VP9 has
a libvpx path in this Apple emulator implementation. Historical codec metrics
do not prove the current decoder or per-frame hardware status. Primary-source
evidence and the next discriminating checks are in
[apple-emulator-decoder-path-20261001.md](apple-emulator-decoder-path-20261001.md).

The next useful source investigation is matched source-buffer/decoder-output
and guest present timing, rather than increasing VM cores, RAM or playback
buffer blindly. Network-aware burst pacing, recovery under loss and a complete
native touch/audio session must subsequently be measured over real cloud UDP
relay/P2P paths and on V50 before product acceptance.
