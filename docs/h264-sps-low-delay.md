# Apple Baseline SPS low-delay hint

`h264_low_latency.patch_apple_baseline_config(payload)` is a pure bitstream helper.
Its caller must limit it to codec CONFIG packets from the verified native Apple
hardware encoder with frame reordering disabled. The helper performs no capture,
encoding, decoding, networking, Android action or file access. Its existence does
not enable a production service option.

The API returns `(payload, metadata)`. Metadata is JSON-safe and always contains
`status`, `applied`, `changed` and `reason`:

- `patched`, `applied=true`, `changed=true`: a missing restriction was introduced.
- `unchanged`, `applied=true`, `changed=false`: the existing restriction already
  specifies zero reorder and a one-reference decode-frame limit, so the original
  bytes were accepted without modification.
- `unsupported`, `applied=false`, `changed=false`: unknown, malformed or out-of-scope
  configuration; the exact original bytes are returned. The caller can log the
  reason and continue the original stream without guessing a profile or frame
  dependency.

Only one Annex B SPS followed by one PPS is accepted. The production hint requires
Baseline profile 66, progressive frames, reference count 1, POC type 0 and no HRD.
PPS syntax must be within the narrow ordinary Baseline scope. Other profiles,
reference counts, picture-order modes, partitioned/mixed CONFIG payloads or
incorrect existing restrictions are not rewritten. The original profile,
constraints, level, reference count, geometry and preceding VUI bits are preserved.
The PPS remains byte-identical. No encoded picture is examined or modified by the
production API.

The implementation is shared with `scripts/probes/patch_baseline_sps_vui.py`; the
offline fixture probe adds whole-stream media equality, POC-order and PTS checks.
Its shared `patch_sps` helper deliberately rejects an existing restriction by
default for the original missing-restriction A/B. The production CONFIG API
explicitly enables the correct-existing-restriction idempotence path.

The measured short Apple SPS/PPS configuration, not media pictures, is included
in `tests/test_h264_low_latency.py`. Twelve offline tests cover the exact config,
prefix/PPS preservation, idempotence, incorrect reorder/DPB limits, unknown profile,
reference count, picture-order mode, HRD, absent VUI, malformed/truncated input,
extra/reversed NALs and emulation prevention.

```sh
python3 -m unittest discover -s tests -p test_h264_low_latency.py -v
python3 scripts/probes/patch_baseline_sps_vui.py --self-test
```

## Completed controlled fixture A/B

The refactored offline probe produced bytes identical to the 300-frame patched
fixture used for the phone A/B. The
[patch metadata](evidence/native-iteration-20261001/vt-fixture-baseline-vui-patch.json)
verifies that every complete media packet, PTS and key/IDR flag stays unchanged.
Only the codec-config SPS differs.
[Offline FFmpeg verification](evidence/native-iteration-20261001/vui-patch-decode-verification.json)
decodes all 300 frames in both variants and confirms an exact decoded frame
sequence match.

With the same OnePlus `c2.qti.avc.decoder.low_latency`, scheduled release, 80 ms
configured buffer and locally paced 60.002 FPS feed, the
[original replay](evidence/native-iteration-20261001/codec-baseline-scheduled-summary.json)
has mean/p95 input-queue-to-output-ready times of 133.76/137.92 ms, 289 codec
callbacks from 300 queued frames and 3 late discards, leaving 8 unreturned at the
bounded end. The
[patched replay](evidence/native-iteration-20261001/codec-vui-dpb1-scheduled-summary.json)
has 19.86/20.83 ms, 299 callbacks and zero late discards, leaving 1 unreturned.
Mean feed lateness is 0.16/0.12 ms. No emulator capture or streaming network is
in this comparison.

This isolates the missing SPS restriction as a cause of the measured codec
holding for this particular bitstream/decoder pair. It does not directly observe
internal DPB allocation or pure hardware decode execution. These codec callbacks
are counts, not physical display FPS: their vendor timestamps still echo the
requested target. No acoustic A/V or touch-to-photon measurement is established.

## Real-video integration and current limit

The valid Morphe Big Buck Bunny runs request 540×1200, 60 FPS, 4 Mbps VBR,
scheduled release, 80 ms configured buffer and default audio. The OnePlus uses
home Wi-Fi. Source and phone cadence below come from separate actual-present
SurfaceFlinger sampling, not the vendor codec timestamp.

| Path / report | Source actual cadence | Phone receive / codec callback FPS | Phone actual cadence | Queue → ready mean / p95 |
|---|---:|---:|---:|---:|
| [Isolated LAN](evidence/native-iteration-20261001/phone-morphe-lan60-vui-summary.json) | 57.70 FPS | 57.45 / 57.37 | 56.48 FPS | 21.73 / 38.35 ms |
| [Existing public NPS/TCP](evidence/native-iteration-20261001/phone-morphe-nps60-vui-summary.json) | 59.52 FPS | 44.88 / 44.39 | 42.76 FPS | 25.13 / 78.13 ms |

Evidence: [LAN source](evidence/native-iteration-20261001/source-morphe-lan60-vui.json),
[LAN phone](evidence/native-iteration-20261001/phone-surface-morphe-lan60-vui.json),
[NPS source](evidence/native-iteration-20261001/source-morphe-nps60-vui.json),
[NPS phone](evidence/native-iteration-20261001/phone-surface-morphe-nps60-vui.json).
Source/phone wall-time counting gives 57.04/55.67 FPS on LAN and 59.02/42.32 FPS
on public NPS; table cadence uses intervals between actual presents. Windows have
different lengths, so these cannot be interpreted as matched per-frame loss
ratios. Public phone present gaps are p95 55.32 ms and maximum 221.26 ms, including
8 gaps above 100 ms. **Public stable 60 FPS remains unachieved**, despite the
validated improvement in codec holding. V50, cellular operation, sustained
playback, exact lip-sync and completed P2P/UDP app integration remain unverified.

The later
[production LAN follow-up](evidence/native-iteration-20261001/phone-morphe-lan60-vui-production-summary.json)
is excluded from comparisons: its
[source sample](evidence/native-iteration-20261001/source-morphe-lan60-vui-production.json)
contains zero new frames because the player stopped. Its sparse callbacks/presents
do not demonstrate a regression of the SPS correction or gateway deployment.

## Enabled M1 scope

`DIRECT_SPS_LOW_DELAY=true` was enabled only in the existing M1 LaunchAgent
`local.remoteandroid.m1compare.gateway`; the original plist remains at
`~/.config/huoguo-android/experiments/20261001-gateway-sps/gateway-before-sps.plist`.
The source option still defaults to false. No auth, NPC or AVD changes were made
by this enablement, and M5 was not deployed. The public result exercises this
existing M1 production path, while the valid LAN run uses an isolated gateway.
This bounded deployment and the available rollback are separate from a claim of
completed end-to-end performance acceptance.

[Moonlight's pinned SPS handling](https://github.com/moonlight-stream/moonlight-android/blob/b48494cb96bff23d8886c4775cc4f39a1075495d/app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java#L1491)
provides the corresponding restriction/DPB precedent. This implementation keeps
the existing reference count instead of copying conditional reference-count or
legacy-level reductions.
