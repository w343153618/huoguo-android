# Experimental authenticated UDP AAC component

This extends the isolated phone probe. It does not change the production App,
gateway, emulator, NPC/NPS or download service. It does not open an ADB port.

The host reads the existing local `HostHardwareSession.audio` AAC records. That
session normalizes audio PTS to the same microsecond epoch as the host video PTS.
Only AAC-LC 48 kHz stereo configuration is currently accepted by the phone; an
unsupported configuration is counted, never silently interpreted as stereo 48k.

`audio_datagram.packetize_audio(flagged_pts, payload, frame_id)` returns plaintext
HGUA datagrams. The caller MUST use the existing authenticated peer/session
AES-256-GCM envelope and one locked server-direction nonce counter shared with
video and control. Do not introduce separate counters with the same key/prefix.

Wire format is big endian `>4sBBHQIIIHHII` (40 bytes): magic `HGUA`, version 1,
flags (0 media / 1 codec configuration), header size, source PTS microseconds,
nonzero uint32 frame ID, codec (`0x00616163` AAC), full record size, fragment index,
fragment count, offset and fragment size. Audio PTS excludes scrcpy's config bit
62 and rejects reserved bit 61 and timestamp-to-nanosecond overflow. Each
plaintext datagram is at most 1080 bytes; the 40-byte AES-GCM outer overhead keeps
the encrypted datagram at most 1120 bytes. Record size is at most 64 KiB, config
size at most 64 bytes. Repeated codec config uses the SAME frame ID; alternatively,
identical ASC under a new frame ID is ignored without recreating AudioTrack.

Phone assembly keeps at most 32 partial and 16 complete records, a 256-record
deduplication history and an 80 ms assembly expiry from first fragment arrival.
Completed media uses a 10 ms reorder window. Missing/expired audio is dropped;
this component has no audio retransmission or FEC and no TCP media fallback.
The assembly deadline is a local observation budget, not a measured network
one-way or source-to-acoustic delay.

`UdpAudioReceiver` receives parsed frames through a bounded 8-record worker
queue. Decoder input admission uses the remainder of the 80 ms assembly/input
budget. AAC decoding and PCM scheduling never execute on the UDP receive thread.
Audio output uses the SAME `PlaybackClock` object as video. It estimates the
AudioTrack queue tail from hardware timestamps when valid (playback head plus
25 ms fallback otherwise), then submits PCM near the media deadline using bounded
nonblocking writes. This is intentionally not immediate PCM playback. Grossly
late PCM is dropped rather than accumulating delay indefinitely. Close interrupts
the owned workers and releases owned codecs/tracks without changing saved user
preferences. No audio bytes, filenames, credentials or account data enter reports.

Session options `audio_enabled` and `touch_enabled` default to false to preserve
video-only probe behavior. READY, keyframe feedback and touch packets share one
serialized client AES-GCM nonce counter. `udp_audio` report counters distinguish
received fragments, completed records, decoder inputs, PCM writes, hardware
timestamp observations, expiry/queue/late drops and failure class. `audio_tested`
means decoded PCM was written, not that a person heard it or that lip sync was
measured. Hardware audio timestamps and codec callbacks are estimates, not
acoustic measurements; actual lip sync remains explicitly unmeasured.

Offline protocol verification (from repository root):

```sh
python3 -m unittest discover -s experiments/moonlight-v2/transport/android-udp -p test_audio_datagram.py
javac -d /tmp/huoguo-audio-check experiments/nps-transport/phone/UdpAudioAssembler.java tests/java/local/remoteandroid/direct/UdpAudioAssemblerCheck.java
java -cp /tmp/huoguo-audio-check local.remoteandroid.direct.UdpAudioAssemblerCheck
```

The pure checks cover fragmentation, reversed arrival, duplicate/conflicting
fragments, complete-record replay, malformed/zero/overflow input, AAC config,
expiry without resurrection, bounded loss and multiple records in PTS order.
They do not establish phone decoding, audio continuity, lip sync, WAN or V50
acceptance. Android SDK compilation separately checks the receiver API surface.
