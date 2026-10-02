# Offline Baseline SPS restriction experiment

This is a narrow, single-variable fixture experiment. It does not change a live
encoder, Android, phone, network, account, display or production service. It changes
only an SPS in a codec-configuration packet of a copied `.framed` fixture.

The accepted scope is progressive H.264 Baseline (`profile_idc=66`), exactly one
reference picture, POC type 0 or 2, no HRD, no existing bitstream restriction,
ordinary PPS without CABAC/FMO/redundant pictures, and one P/I slice per media
packet. Every IDR/key flag must agree, media PTS must increase and actual parsed
picture order must increase within each IDR epoch. Unsupported or malformed input
fails. High profiles, B slices, alternate picture ordering and in-band SPS in a
media packet are deliberately rejected rather than guessed at.

The patch preserves the original profile, constraints, level, reference count,
geometry, POC fields and existing VUI bits preceding the restriction. It adds:

- `bitstream_restriction_flag=1`
- `motion_vectors_over_pic_boundaries_flag=1`
- `max_bytes_per_pic_denom=2`, `max_bits_per_mb_denom=1`
- `log2_max_mv_length_horizontal=16`, `log2_max_mv_length_vertical=16`
- `max_num_reorder_frames=0`
- `max_dec_frame_buffering=max_num_ref_frames=1`

Exp-Golomb coding, RBSP trailing bits and emulation-prevention bytes are rebuilt
and parsed again. All other NALs remain byte-identical. The report verifies that
every complete media packet, including its original PTS/key header, is unchanged.
Original and patched binary files must resolve under `/private/tmp`; an existing
output is never overwritten. The report contains numeric metadata and hashes,
without pixels, compressed payloads or raw logs.

## Actual fixture used for the decisive phone A/B

```sh
python3 scripts/probes/patch_baseline_sps_vui.py \
  --input /private/tmp/huoguo-vt-baseline-20261001.framed \
  --output /private/tmp/huoguo-vt-baseline-vui-dpb1-20261001.framed \
  --report docs/evidence/native-iteration-20261001/vt-fixture-baseline-vui-patch.json
```

That copy has 300 unchanged media frames (297 P, 3 I). The actual original SPS is
540×1200, profile 66, level 32, one reference and POC type 0. Its 34×75 macroblocks
give a level DPB limit of `min(floor(20480 / 2550), 16) = 8` frames; the original
SPS lacks an explicit restriction. This motivates the A/B but does not prove that
the phone holds eight pictures. Only the unchanged-fixture phone timing comparison
can resolve that hypothesis.

Use the existing `CodecFileProbe` to compare original and patched copies with the
same phone codec and release policy. The generating agent performed the pure
Python patch and metadata checks only. It did not run VT or a phone test. Optional
`--verify-ffmpeg` performs single-threaded software decoding of both variants and
requires equal per-frame pixel hashes and full frame counts before writing the
patched file; this does not use VideoToolbox. Choose a fresh output name if using
that option after an existing copy has already been created.

## Why this matches part of Moonlight, without copying unsafe assumptions

[Moonlight's pinned SPS handling](https://github.com/moonlight-stream/moonlight-android/blob/b48494cb96bff23d8886c4775cc4f39a1075495d/app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java#L1491)
adds bitstream restrictions on modern Android or selected older decoders. When
creating a missing restriction it sets zero reorder; it sets DPB to reference
count. Existing restrictions follow a different branch and are not uniformly
rewritten to zero reorder. Earlier reference-count and level changes also depend
on reference-frame invalidation and Android-version conditions. This tool keeps
the real original reference count and level, and accepts only the confirmed
one-reference fixture.

[FFmpeg's official h264_metadata options](https://ffmpeg.org/ffmpeg-bitstream-filters.html#h264_005fmetadata)
offer timing, aspect ratio, color, crop, level and other metadata edits, but do not
expose setters for reorder count or maximum decode-frame buffering. That filter
cannot directly implement this exact VUI A/B.

```sh
python3 scripts/probes/patch_baseline_sps_vui.py --self-test
```

The six self-tests are pure Python SPS/bit/escape checks and start no codec.

The SPS/bit/Annex B primitives now come from the shared production source module
`h264_low_latency.py`; the probe retains its independent whole-fixture POC, media
equality and file handling. The shared core recreated the already accepted phone
A/B fixture byte for byte, rather than generating a new bitstream variant.
