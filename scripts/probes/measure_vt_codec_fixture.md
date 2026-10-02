# Paced VT bitstream fixtures for an isolated phone decoder comparison

This probe removes emulator capture, real-video variation, network transport and
phone UI sampling from one narrow question: does the Apple H.264 bitstream or the
chosen phone decoder configuration cause output holding? It generates a synthetic
FFmpeg `testsrc2` source at 540×1200, 60FPS and feeds complete RGBA frames to an
explicit native encoder at 60Hz. This is synthetic codec evidence, not real-video,
WAN or V50 acceptance.

The input PTS sequence is deterministic: start at 1,000,000 microseconds by default
and increment by exactly 16,666 microseconds per frame. Wall-clock pacing uses
1/60 second. The one-microsecond fraction is deliberately not accumulated in PTS,
so the same PTS sequence is replayed in every experiment. The script records feed
gaps, blocking time, lateness and the SHA-256 of the complete generated RGBA source.
Compare that source hash across A/B fixtures before comparing phone results.

## Three explicit encoder variants

Run sequentially, after the other host hardware session has stopped. All commands
are from the canonical project root. The existing runtime is external:
`~/Documents/ChatGPT/others/android-remote/m1-compare`.

```sh
python3 scripts/probes/measure_vt_codec_fixture.py \
  --label legacy-runtime \
  --native-encoder ~/Documents/ChatGPT/others/android-remote/m1-compare/hardware/macos-h264 \
  --low-latency-mode omit --frames 240 \
  --framed-output /private/tmp/huoguo-codec-legacy-240.framed \
  --report docs/evidence/gemini-review-20260930/codec-fixture-legacy.json

python3 scripts/probes/measure_vt_codec_fixture.py \
  --label new-native-default \
  --native-encoder /private/tmp/huoguo-vt-lowlatency \
  --low-latency-mode false --frames 240 \
  --framed-output /private/tmp/huoguo-codec-new-default-240.framed \
  --report docs/evidence/gemini-review-20260930/codec-fixture-new-default.json

python3 scripts/probes/measure_vt_codec_fixture.py \
  --label dedicated-vt-low-latency \
  --native-encoder /private/tmp/huoguo-vt-lowlatency \
  --low-latency-mode true --frames 240 \
  --framed-output /private/tmp/huoguo-codec-lowlatency-240.framed \
  --report docs/evidence/gemini-review-20260930/codec-fixture-lowlatency.json
```

`omit` passes no new option to an older executable. A flag/configuration failure
never triggers an automatic fallback or another variant. 180–300 source frames
are permitted. The native executable receives FPS 60, bitrate 4,000,000, VBR,
bounded frame/time/idle limits. An independent watchdog bounds source generation
and native execution. Output and metrics readers drain concurrently.

The retained fixture uses the existing binary protocol byte for byte: `h264`,
the geometry marker, then config/media packets with original PTS and key/config
flags. A phone `CodecFileProbe` can replay this exact private fixture rather than
re-encode an MP4. Config packets must still be submitted as codec configuration;
media PTS should be mapped relative to the local replay start when scheduling
presentation. The generating script itself does not contact the phone or install
anything.

`--framed-output` must resolve beneath `/private/tmp`, including symlink checks,
and must not already exist. Generated RGBA is never stored; extracted H.264 used
for analysis stays in a temporary directory under `/private/tmp` and is removed.
The retained framed fixture is left for replay, including a partial fixture on
failure. Choose a new name for a repeat. Do not add these binary files to Git.

## Evidence and pass conditions

The `.json` report contains numeric framing/PTS/NAL metadata, source/output hashes,
strictly selected structured native metrics, and FFmpeg `trace_headers` output
parsed into numeric SPS and slice-header fields. Raw stderr, bitstrings, image
pixels and compressed payloads are not included. The software decoding check
records the actual complete decoded frame count with passthrough output timing;
it does not interpret the decoder's displayed/assumed FPS as source FPS.

PASS requires actual `using_hardware: true` in both ready and final native metrics,
successful native/source exit, the full fixed PTS sequence, successful SPS trace,
and a complete software decode of all requested source frames. Missing hardware
readback, unsupported dedicated mode, dropped frames, missing final summary or a
CBR/property failure must remain visible failures. Requested low-latency mode and
its property readback remain separate observations.

Inspect actual `profile_idc`, constraints, reference-frame count, VUI bitstream
restrictions, reorder/DPB limits, and `slice_type_mod5_counts` before relating phone
queue-to-ready timing to bitstream reorder policy. Legacy and newly compiled
executables can differ in profile and other source changes; this matrix is not
automatically a single-variable encoder-mode experiment. The metadata exposes
these differences rather than concealing them.

## Offline checks

```sh
python3 scripts/probes/measure_vt_codec_fixture.py --self-test
python3 -c 'import ast,pathlib; ast.parse(pathlib.Path("scripts/probes/measure_vt_codec_fixture.py").read_text())'
```

The self-test checks fragmented framing, original-byte retention, input header and
fixed PTS, legacy-option omission, repository-media rejection, numeric SPS/slice
trace parsing and metadata filtering. It starts no FFmpeg, VideoToolbox, emulator,
phone or networking process. Script creation and these checks do not establish any
runtime performance result.
