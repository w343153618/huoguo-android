# Controlled VideoToolbox H264 decoder audit

`vt_decode_audit.c` is an independent macOS arm64 interposer for
`VTDecompressionSessionCreate`. It is not automatically loaded anywhere and
does not modify the Android SDK, an emulator binary, launch agents, services,
devices, networking, or application settings. Its compiled output belongs under
`/private/tmp`, outside Git.

## Build and owned-helper validation

From the canonical project directory:

```sh
python3 experiments/moonlight-v2/capture/verify_vt_decode_audit.py --build-only
```

Default outputs:

- `/private/tmp/huoguo-vt-decode-audit.dylib`
- `/private/tmp/huoguo-vt-decode-audit-selftest`

Both are compiled with the existing macOS SDK, arm64, minimum macOS 11,
`-Wall -Wextra -Werror`. The helper is unrelated to the AVD. The explicit self-test
command is:

```sh
python3 experiments/moonlight-v2/capture/verify_vt_decode_audit.py
```

It uses CPU ffmpeg to generate one synthetic black frame solely to obtain a tiny
SPS/PPS format description. Temporary synthetic files are bounded, remain under
`/private/tmp`, and are deleted. The owned helper creates/queries/invalidates one
VideoToolbox session per test; it decodes **zero frames**. Only that child process
receives `DYLD_INSERT_LIBRARIES`. No emulator or user's media/UI is captured.

## Default and optional behavior

By default the library passes every original creation argument unchanged,
including the original decoder-specification dictionary pointer. Only H264
video sessions produce an audit record. Other codecs and calls with no format
description pass through without audit output.

Only an exact `HUOGUO_REQUIRE_HW_DECODE=1` opt-in copies the caller's decoder
specification and adds
`kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder=true`.
The original dictionary is never changed. The library does not alter other keys
and never silently retry with software decode. If creating the copied dictionary
fails, the explicit force operation fails instead of silently bypassing the
request. The opt-in can therefore cause playback to fail when hardware is
unsupported, resources are unavailable, or caller settings conflict.

After successful creation it queries
`kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder` with
`VTSessionCopyProperty`. A result is known only when the query succeeds and its
CF object is actually a `CFBoolean`. Missing/unavailable/nonboolean properties
set `hardware_known=false`: they are **not evidence of software decode**. A
known true result describes that created H264 session's hardware-decoder
selection; it does not establish frame throughput, AVD-wide acceleration,
renderer performance, capture performance, phone decoding, or end-to-end FPS.

The installed Apple SDK describes RequireHardware as failing when hardware
cannot be allocated and the UsingHardware property as a read-only CFBoolean.
Relevant primary references are [Apple's property documentation](https://developer.apple.com/documentation/videotoolbox/kvtdecompressionpropertykey_usinghardwareacceleratedvideodecoder)
and the installed `VideoToolbox.framework/Headers/VTDecompressionProperties.h`.

## Interposition and recursion

The library emits a Mach-O replacement/original function pair in
`__DATA,__interpose`. The replacement has its own name and calls the imported
original `VTDecompressionSessionCreate` directly from the same image. This
follows [Apple's documented dyld-interposing example](https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld-interposing.h):
dyld does not substitute this interposer's own replacee binding. It does not use
`dlsym(RTLD_DEFAULT)`, which could return the replacement. A thread-local guard
also prevents nested framework calls from producing recursive audit/force work.
The explicit owned-helper test validates exactly one audit record per H264
creation, with no recursion or hung process.

The SDK used here lacks `mach-o/dyld-interposing.h`, so the source creates the
equivalent minimal Mach-O pair rather than copying an external header. The
library is arm64, not arm64e, and does not claim support for pointer-authenticated
arm64e interposition.

## Output schema

Each H264 creation emits one short stderr JSON record. Values are only
whitelisted integers and booleans:

| Field | Meaning |
| --- | --- |
| `vt_h264_create_audit` | Fixed schema marker 1 |
| `ordinal` | H264 creation attempt count within this loaded process |
| `require_requested`, `require_applied` | Exact opt-in requested and copied key applied |
| `force_prepare_status` | Preparation status; 0 in default mode |
| `create_status`, `session_created` | Original/forced creation outcome |
| `property_read_status`, `property_is_boolean` | Actual VT property query outcome/type |
| `hardware_known`, `hardware` | Actual boolean when known; ignore hardware when unknown |

No media bytes, images, SPS/PPS, decoder specification, paths, pointers, decoder
names, environment values, credentials, accounts or user identifiers are logged.
An audit write failure does not change the decoder result. The library performs
one small write per session creation, not per video frame. Parent processes must
drain stderr normally; this is an audit tool, not a hardened production logger.

## Controlled AVD use boundary

The experiment owner may later launch an explicitly selected AVD executable with
an environment-only `DYLD_INSERT_LIBRARIES=/private/tmp/huoguo-vt-decode-audit.dylib`.
Audit mode should be tried first with the force variable absent. Optional require
mode is a separate A/B experiment and requires `HUOGUO_REQUIRE_HW_DECODE=1` in
that controlled launch environment. This document and build tool do **not**
launch either mode for an AVD.

Existing hardened-runtime/library-validation or restricted loader policy may
reject or remove the injection. Do not change SIP, resign emulator binaries,
modify SDK libraries or widen security settings to bypass that boundary. No
audit record can mean injection was rejected, a session already existed, or the
codec path did not call this H264 VT API; it does not prove software decoding.

## Current self-test evidence, 2026-10-01

`vt-decode-audit-selftest.json` records sandboxed helper creation failures
(`create_status=-12911`). Interposition, exact opt-in, original dictionary
preservation and H264-only logging passed, but hardware remained unknown.

`vt-decode-audit-selftest-unsandboxed.json` records the same bounded owned helper
outside that restriction. Default H264 creation succeeded; the actual property
query returned a CFBoolean true, and the independent helper query exactly agreed
with the audit. Exact require mode also returned known hardware true. Default
audit mode preserved an explicit caller disable: the session succeeded but the
hardware property was unavailable (`-12900`), so the audit reported unknown.
Adding RequireHardware while retaining that disable returned `-12906` on this
machine. The library records that failure; it does not erase caller keys or claim
hardware use. `HUOGUO_REQUIRE_HW_DECODE=true` did not activate force mode. JPEG
creation produced no H264 audit record. All seven contract checks passed.

These results confirm the standalone audit mechanism and actual property reads
for synthetic sessions. **The library has not been loaded into an AVD, and no
AVD hardware decode or real-video performance claim is established.**
