# Isolated authenticated UDP video on a real phone

`UdpVideoProbe` is a separate instrumentation component. It creates the target
app's guarded `SurfaceView`, reuses `MainActivity.configure` and its existing
video output policy, and feeds complete reconstructed H.264 access units. It
does not call `show`, create a stream session, read an account/password, connect
to a TLS/TCP video endpoint, create AudioTrack, or alter production network
routes. This first component measures video only: native touch, audio and actual
acoustic A/V sync are not tested. After its measurement window, normal connection
UI restoration may resume the app's ordinary updater behavior.

## Build

Build the native Android arm64 FEC JNI library using the UDP core's documented
NDK procedure, then supply its exact output to the instrumentation builder:

```sh
python3 scripts/probes/build_phone_transport_probe.py \
  --udp-native-library /private/tmp/<native-build>/libhuoguo_udp_fec.so
```

The builder checks for little-endian ELF64 AArch64 before placing the library at
`lib/arm64-v8a/libhuoguo_udp_fec.so`. The instrumentation manifest uses
`extractNativeLibs=true`; `NativeUdpFec` loads the library explicitly from the
instrumentation context's native library directory, not from the production
app. The same APK still contains `PhoneProbe` and `CodecFileProbe`. The installed
app needs the existing `huoguo_codec_component_probe` intent guard. Omitting
`--udp-native-library` builds the previous Java/file/TCP probe components but does
not make the UDP component runnable. No library is added to the target app's
`jniLibs`, and no app Gradle build or install is implied by this script.

## Fixed temporary session input

The experiment owner provisions only this private file over USB:

```text
/data/user/0/local.remoteandroid.direct/files/udp-video-session.json
```

It must be a regular non-symlink file, owned by the target application UID,
mode `0600`, with 1–4096 UTF-8 bytes. Required fields are:

| Field | Constraint |
| --- | --- |
| `key_b64` | Standard Base64 for a fresh 32-byte per-test AES key |
| `session_tag_hex` | Fresh 16-hex-digit unsigned 64-bit test tag |
| `peer_host` | Literal unicast IPv4; no DNS lookup |
| `peer_port` | Exactly `15961` |
| `bind_port` | Exactly `15960` |
| `seconds` | `1..120` |
| `fps` | `60` or `120`; requested cap, not a measured rate |
| `buffer_ms` | `30..80` |
| `video_release` | `scheduled` or `immediate` |
| `profile` | Optional caller label, at most 160 characters |

Do not put the key or session file in command arguments, logs, evidence, Git or
the report. The probe reads only this fixed file and deletes it in `finally`.
The runner should independently delete this same path if instrumentation is
terminated before cleanup. This is a temporary encrypted test session, not a
new account or a production credential.

## Authenticated wire protocol

Each UDP datagram has 24 bytes of big-endian authenticated associated data:

```text
uint32 magic       0x48475545 (HGUE)
uint32 version     1
uint64 sessionTag
uint64 sequence
bytes AES-GCM ciphertext + 16-byte authentication tag
```

The maximum full datagram is 1400 bytes. A native HGUD packet with its 56-byte
header and 1024-byte shard produces a 1120-byte encrypted datagram. The packetizer
pipe's two-byte record length prefix is not part of the UDP wire packet.
Server-to-phone nonces are big-endian `0x48475545 || sequence64`. Phone-to-server
nonces use the distinct prefix `0x48475543` (HGUC) with their own monotonic
sequence counter. Thus equal sequence values in opposite directions cannot
reuse a key/nonce pair. Server media starts at sequence 1; initial phone READY
uses sequence 0. Every control retry receives a new sequence.

The phone sends authenticated ASCII `READY` every 500 ms until receiving the
first authenticated HGUD data packet from the pinned peer. Receiving duration
starts at that packet rather than instrumentation launch, so hardware encoder
startup does not consume the media test window. Overall receive time is bounded
by `seconds + 30` from socket startup, followed by at most one second of decoder
drain. The phone validates the exact source IP and port, header, session tag and
AES-GCM tag before advancing a bounded 4096-sequence replay window. The window
accepts in-window reordering, rejects duplicates/old packets, uses unsigned
64-bit comparisons and constant-work modulo slots. An authentication failure
cannot poison the replay window. The probe never stores packet ciphertext,
plaintext shards or image pixels in its report.

## Native FEC and decoder handoff

JNI exposes `nativeCreate`, `nativeAccept(handle, authenticatedHgud, arrivalUs)`,
`nativeExpire(handle, nowUs)`, `nativeStats` and `nativeDestroy`. Both accept and
expire can return complete logical frame bodies. JNI verifies/bounds HGUD shard
assembly and uses an 80 ms fresh assembly deadline measured from first phone
arrival. Host capture and phone monotonic clocks are different; this budget is
not a measured 80 ms source-to-phone latency or a WAN jitter estimate.

Each reconstructed body contains big-endian width32, height32, flaggedPTS64,
configLength32, then optional CONFIG bytes and an Annex B H.264 access unit.
Config is permitted only on an IDR. The phone submits it separately with
`BUFFER_FLAG_CODEC_CONFIG`, then queues media using the original source PTS.
It starts or changes geometry only on an IDR with config. Input-buffer waiting
is bounded at 80 ms per submission; a local timeout marks dependency recovery
necessary and remains visible in the report.

When native or local state needs an IDR, the phone sends authenticated ASCII
`KEYFRAME`. Each recovery permits at most three requests, spaced 500 ms then
1 second; it subsequently waits for periodic IDR. The sender must authenticate,
pin and replay-check these commands, coalesce at most one request per 500 ms,
and explicitly forward its isolated hardware control request. Request counts
and times do not prove that a keyframe arrived or visible recovery completed.

`nativeStats` returns exactly 21 numeric counters, named in `NativeUdpFec.java`,
including expiry, recovered shards, dependency loss, requested IDR, assembly
latency and bounded-map rejection. No native payload or secret strings are
accepted into the report.

## Invocation and evidence boundary

With the fixed temporary session and matching app/probe already provisioned,
the experiment owner invokes:

```sh
am instrument -w \
  local.remoteandroid.phoneprobe/local.remoteandroid.direct.UdpVideoProbe
```

The instrumentation result is only `report_file=udp-video-report.json` plus
`report_bytes`. Complete metadata JSON is atomically written to the fixed
app-private `files/udp-video-report.json`, bounded at 64 MiB. A runner must read
only that fixed path, verify byte count and JSON type, then remove only that
report and the fixed temporary session file. It must not delete other app files.

Per-second autonomous samples retain authenticated packet counts, UDP payload
rate, media receive FPS, Java codec callback FPS, local late output discards and
native FEC counters. Complete queued-frame metadata retains original PTS,
receipt/input/ready/release/target/callback times, access-unit/config sizes and
strict vendor-timestamp validity. `accepted_media_receive_to_input_queue_ms`
uses all queued media; shared presentation-stage arrays cover matched callback
records. Eviction and unmatched-callback counters stay visible. None of these
numbers is an independently measured physical display FPS. Separate
SurfaceFlinger actual-present metadata is required, and audio is absent.

The same measured path must identify its real source, geometry, requested FPS,
network path and actual displayed cadence before acceptance. LAN UDP on a
OnePlus phone is not cellular, WAN, P2P or remote V50 acceptance.

## Offline security validation

```sh
javac -d /private/tmp/huoguo-udp-java-check \
  experiments/nps-transport/phone/UdpVideoSecurity.java \
  tests/java/local/remoteandroid/direct/UdpVideoSecurityProbe.java
java -cp /private/tmp/huoguo-udp-java-check \
  local.remoteandroid.direct.UdpVideoSecurityProbe
```

This exercises real AES-GCM, tampered ciphertext/AAD, wrong-direction nonces,
authentication-before-replay, duplicate/reordered/expired sequences, unsigned
counter boundaries, exact wire overhead and datagram limits. It starts no
socket, Android component, phone session, emulator or network route.
