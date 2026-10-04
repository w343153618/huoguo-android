# Existing public M1: real receive-progress stall, 2026-10-04

The current public path reproduced a substantial stall. This is a real CPU-limited OnePlus 12 observation using the published alpha8 and the existing M1 public service, on the same home Wi-Fi. It is not a cellular or remote V50 test. [Closed numeric evidence](existing-public-M1-progress-stall-20261004.json).

## Actual path and protected completion

App HTTPS49556 authenticated using the existing saved UI account; media used the selected NPS public UDP15556 profile. Requested/actual profile was 540×960, 30 FPS, 4M VBR, 80ms, lead0, AAC on, PCM queue/startup-ready/stage diagnostics off. The original FIFO, socket wait/guard, physical-network API binding and NPC identities remain. The API binding is not a packet-level domestic-route or P2P verification.

Source-only preparation held the existing admission reservation and obtained the exact live idle witness. It explicitly released that reservation before normal public media, avoiding blocking the original worker. A fresh source-only lease was acquired after the ordinary driver ended, for pause and final Stats readback. No second gateway, service replacement, signals, M5 operation, NPS restart or filter change occurred.

The driver returned1 with `steady_media_progress_stalled_or_unverified`; the supervisor returned2. Both sampler processes completed, the normal exit-confirmation and reconnect sequence ran, source was paused, helper/private input/target processes were cleared, and root independently verified the original gateway identity and four media-role counts of zero. This is a media-progress failure rather than a claimed PASS after cleanup.

## Real observations

| Observation | Source | Phone |
|---|---:|---:|
| Independent SF cadence | 29.964/s | 20.381/s |
| Full requested-window rate | 29.557/s | 19.766/s |
| Largest observed SF gap | 71.094ms | 5030.632ms |
| Observed gaps over100ms | 0 | 20 |
| Unknown trailing coverage | 340.399ms | 615.164ms |

Phone complete-frame receive and codec callback counters both plateaued: samples13–17 retained received375/callback367 across at least4.017s; the helper's progress monitor measured4.769s without progress. Raw packet arrival is not represented by these complete-frame counters. This argues against only a final Surface/display bottleneck, but does not yet distinguish source supply, network delivery, reassembly expiry or reference-chain recovery. Callback timestamps continue to echo requested targets and are not independent presentation timestamps.

The first App report recorded775 received complete frames,768 queued,752 callbacks,17 FEC frame expiries/reference losses,77 dependent-frame drops and7 keyframe requests. There was one Inbox overflow clearing4 frames, one input timeout and14 late discards. These are whole-session totals, not an event correlation proving which mechanism caused the five-second stall.

Fresh original hardware log bins, each approximately5.15s, independently show captured raw29.72–30.11/s while submitted raw varied15.73,21.18,28.94,22.34,16.31,24.08,29.96/s. Raw replacements accompanied the low-submit bins. These bins include a wider sequence than the phone/SF window and do not provide an exact same-frame cohort. They reproduce source-to-encoder supply loss on the original public runtime; they do not by themselves identify its mechanism.

The fresh original host long-session report has946 source access units,891 packetizer outputs,9 output-deadline drops,46 dependent-source drops and10 recovery requests. The socket guard separately recorded13 deadline-dropped frames and30 skipped-chain frames. Video send errors/would-block/ENOBUFS were0; maximum seal/send-call/syscall regions were16.497/39.721/37.967ms. These regions include scheduling and cannot be described as pure crypto CPU cost or network transit time. Original evidence-directory freshness, requested settings and duration associate the reports with this round, but this is not a cryptographic or actual Popen identity join.

## Content and limits

Fresh paused endpoints retained PID3470/UID10235/start1952, actual BBB ID `aqz-KE-bpKQ`, itag299/avc1/1920×1080 descriptor60 and251 Opus. Position352626→410285ms and cumulative content drops364→463 span the wider57.659s preparation/playback sequence, not just the30s sampler. Overlay remained on and its overhead is unmeasured. Format60 describes the selected content; guest display remains30Hz. Neither endpoint agreement nor SF cadence proves unique-content frame rate or continuous content identity throughout the window.

Recent frozen LAN rounds approached30/s with low raw-queue age, whereas this ordinary public observation did not. This is not a protocol-only AB: runtime revisions, wider phase bins, playback position and load differ. An initially considered Python-version explanation was disproved: gateway and worker in both paths use Python3.14.7. No default, queue capacity or buffer change is justified by that hypothesis.

## Next narrow experiment

Keep defaults. A new inert, bounded parser for a possible subsequent phone-only capture of the selected public media endpoint's IPv4/UDP and24-byte HGUE envelope headers passed16 focused fixtures. Correlate capture-relative packet arrival/sequence observations with complete-frame progress and the existing host phase bins. Header sequence holes are observations, not proven packet loss; headers cannot authenticate packets or identify encrypted video/audio lanes. A receive plateau with continuing envelope arrival would direct attention toward assembly/reference recovery; a packet-arrival gap would require separate host-send/cloud-path timing to locate it.

Capture has not run. The Mac BPF preflight was denied. On the phone, `command -v` returned `/system/bin/tcpdump`, but fresh actual execution and file metadata reads were denied both as UID0 and the normal shell. Path discovery is therefore not proof of an executable capture facility. No SELinux/global-security setting was changed. Next prepare explicit owner-only, bounded App ingress-progress observations, or a separately verified capture facility, before another correlated round. Existing receive code already retains whole-session packet/authentication totals internally; the published helper currently observes only complete-frame/callback progress. Adding a live observation requires a new matched candidate/helper and overhead/lifecycle verification; the current alpha8 cannot be retroactively credited with those fields.

Packetizer natural exit/final receipt does not certify the Swift encoder producer footer. Native full-pipeline coverage, physical touch/audio timing, remote V50, cellular/foreign-network transport and friend isolation remain separate gates. No new APK or update manifest was published in this round.
