# Alpha8 M1: capture delivery and raw-submit readback

This is a read-only review of the existing M1 frozen runtime after root's gold39
saved-UI/public-UDP test. No source, phone, service, guest, NPS or queue setting was
changed. The independently exported evidence is
[alpha8-raw-submit-readback-20261003.json](alpha8-raw-submit-readback-20261003.json).
It contains fixed whitelisted numeric rows, not a raw runtime log.

## Actual dependency boundary

The M1 LaunchAgent points to
`~/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003/source-alpha8-dd43a39/udp_nps_gateway.py`.
Its `udp_lan_worker.py`, `udp_nps_gateway.py`, `udp_lan_sessions.py` and
`hardware_stream.py` bytes match commit
`dd43a39f49f6dceb55854fbc6e43367d34f381c3`. This is distinct from editing the
canonical repository or rebuilding the current App. The hardware_stream file
SHA256 is `6fe47b938b1db88e09175ece5a465cffdcfb71144c753be0f2a90164dce64e72`;
the UDP worker SHA256 is
`19d1c18e15016afde74ecc9773464047c9c72674eba13c5c3ccccbcf7a19b14f`.

The runtime explicitly selects its persistent `session-pool-encoder` executable,
SHA256 `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf`.
Its persistent packetizer SHA256 is
`567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`.
The runtime `hardware/macos-h264` is a symlink to the older external M1 baseline
binary with another hash; it is not the selected native encoder in this worker.
The filename `session-pool-encoder` alone is not a property readback: the final
two native ready rows in the bounded log actually report `pixel_pool_mode=manual`,
540×960, `using_hardware=true` and `low_latency_mode_requested=false`. These ready
rows lack session IDs/timestamps and are associated by log order only.

The frozen path is: emulator authenticated gRPC RGBA screenshot stream → Python
two-frame FIFO → Python raw submission/token budget → Swift stdin/read,
three-slot/pool/conversion/VideoToolbox → H.264 stdout → Python SPS relay/video
socket → UDP-worker record feed → native packetizer → authenticated UDP sender.
The current worker passes raw-submit budget30 for the selected30FPS session and
keeps native encoder expected FPS30. The gRPC request has no separate FPS limit.

## The observable supply gap

In the ten adjacent approximately5-second rows near this test, **complete gRPC
RGBA delivery remains about30/s, while completed Python raw submissions are
14.18–22.92/s**. A replacement is a complete raw image removed before encoding,
not a phone decoder drop. No idle repeats occurred in these rows.

| Sample end Unix ms | Interval ms | Raw FPS | Submitted FPS | Replaced FPS | Reference-window relationship |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1791027670747 | 5104 | 29.97 | 22.92 | 7.05 | Start boundary crossing |
| 1791027675813 | 5065 | 30.01 | 15.79 | 13.82 | Within requested reference |
| 1791027680957 | 5144 | 30.13 | 20.41 | 10.11 | Within requested reference |
| 1791027686120 | 5164 | 29.63 | 20.53 | 8.91 | Within requested reference |
| 1791027691266 | 5146 | 30.32 | 19.43 | 10.88 | Within requested reference |
| 1791027696415 | 5148 | 29.91 | 14.18 | 15.73 | Within requested reference |
| 1791027701561 | 5147 | 29.92 | 20.40 | 9.33 | Within requested reference |
| 1791027706750 | 5188 | 30.26 | 22.17 | 8.29 | Within requested reference |
| 1791027711895 | 5142 | 29.95 | 17.70 | 12.25 | Within requested reference |
| 1791027717041 | 5150 | 29.71 | 15.73 | 13.79 | End boundary crossing |

The eight interior reference segments sum to41.144seconds of rounded durations.
Weighting their already-rounded rates gives raw30.0163/s, submitted18.8362/s and
replaced11.1560/s. These are aggregate host observations, not a verified
same-frame cohort with the SF or phone results. Per-segment raw_queue maxima reach
110.840ms, raw_pipe p95 ranges10.036–20.297ms, and one boundary row has
raw_pipe max143.708ms. `raw_pipe` includes write/flush blocking and native-reader
backpressure; it is not just Python copying CPU time.

The last two whole worker-close rows, associated by order rather than an exported
session identity, contain1546 captures/979 submissions/567 replacements, then
183/99/82. Root's full packetizer report contains976 input AUs over51.598seconds,
948 output frames, zero whole-frame budget rejections, seven output-deadline
drops and21 dependent drops. Its51.598seconds includes startup/shutdown. It must
not be relabeled as the steady45-second source window; the three-unit difference
from the Python979 submissions does not identify a native drop mechanism.

Root separately reported source SF cadence30.002/max75.213ms/no gaps>100ms and
phone SF cadence17.262/max1094ms/105 gaps>100ms. Helper steady received counters
90→918 span46.311878seconds, about17.879/s, and callbacks87→911 about17.793/s.
The callback is not an independent presentation timestamp. Fresh actual video
rendition/itag was not reread after reload, and phone CPU bounds drifted between
readbacks. This is not a controlled A/B or proof of an improvement or regression.
SF and captured RGBA cadence also do not prove unique decoded content FPS.

## Clock and coverage contract

`pipeline_sample.interval_ms` is a rounded duration from Python `time.monotonic`;
raw/submitted/replaced rates are counter deltas over that duration, rounded to
two decimals. `unix_ms` labels the wall time at emission, from `time.time_ns`.
The first interval covers approximately1791027665643–1791027670747; the last
covers1791027711891–1791027717041. Phase rows follow their pipeline row and may
have a slightly different wall label; neither file ordering nor a similar label
is a precise frame join.

The source SF result's1791027714944 and phone result's1791027714985 are sampling
completion wall times, not last presented-frame timestamps. Subtracting the
requested45seconds supplies only a reference start1791027669944. Actual SF poll
start/end host anchors and a verified per-frame cross-clock cohort were not
available to this independent review. The two boundary rows are explicitly
separate; the interior relationship is to this requested wall reference only.
Wall-clock stability/cross-device calibration is not assumed.

The packetizer uses `CLOCK_UPTIME_RAW`; the existing Python/Swift opt-in trace
uses explicitly named `CLOCK_MONOTONIC`. Do not subtract those epochs without
calibration. Screenshot `source_pts_us` is an emulator estimated screenshot
generation Unix timestamp, not media decode PTS. Packetizer event
`host_capture_us` is taken after reading a whole H.264 AU at the packetizer; it is
packetizer admission time, not screenshot capture time. Join events by their
preserved source PTS, then use the declared clock/anchors for intervals.

## Narrowed location and the next bounded experiment

The evidence establishes a host supply loss **after complete screenshot delivery
and before raw submissions complete**. The raw writer can be delayed by its
budget/condition scheduling, control writes, CPU/GIL scheduling, or a Swift
reader stalled behind conversion, VT slots or output backpressure. Existing
5-second raw_pipe percentiles and aggregate replacements cannot distinguish
these. It is premature to blame VT hardware throughput, Android rendering,
phone decoder capacity or NPS. Output-deadline/dependency loss remains a later,
separate contribution; zero frame-budget drops does not prove zero scheduling
or stdout backpressure.

The smallest next diagnostic is one independent, bounded owner pipeline with
**all media parameters unchanged and only the existing capture/native metadata
trace enabled**, including a trace-off control if interpreting overhead. Recheck
actual content rendition/position, guest Vulkan/physical30, source dimensions,
phone CPU bounds, gold39/helper/binary hashes and idle gates immediately around
each round. Keep540×960/4M VBR/30FPS/80ms, FIFO2, native slots3, receiver queues,
phone refresh, audio and network path fixed. Trace startup and steady windows
separately; export fixed numerical fields and coverage summaries only.

The existing trace already joins capture enqueue and raw submit to Swift raw
read, slot wait, pixel conversion, encode-call return, callback, stdout lock/write
and Python encoded egress. Native output/error final alone is insufficient:
the selected bounded worker log has no encoder final, and the existing worker
close/cleanup can SIGTERM the process group or native child. Its complete
packetizer final does not substitute for a VT final.

The useful missing raw-writer measurements are bounded condition-wait begin/end,
budget requested wait/actual wait, control-command count and write duration, and
fixed cumulative capture/submission/replacement counters with interval anchors.
These should be owner opt-in observations, not account/request-controlled
settings or unrestricted logs. The native trace already has the core timestamps;
retain VT submit status/frameDropped and pending/in-flight observations when
classifying a slot stall. `raw_pipe` and native payload reads overlap, so do not
add them as sequential CPU costs. Header-read begin can wait for a new frame.

After that localization, choose **one** behavior factor. If ready raw images
mostly wait on the30FPS submit budget while native slot/convert/output remain
short, a bounded candidate may compare raw-submit budget30→60 only, with guest
physical30, native expected30 and App30 still fixed. This changes clearance
margin, not source refresh or an artificial duplicate-frame generator. If a
native/output stall dominates, leave the budget alone and compare the same
real-source capture/native encoder with an immediate bounded local H.264 drain
against the existing packetizer sink; only the output consumer changes, and that
comparison is host diagnosis rather than phone/public-network acceptance.
Neither candidate is deployed by this review. Do not expand the phone Inbox,
increase playback buffer, change NPS or rename the encoder's pool mode to conceal
this already-observed upstream supply gap.
