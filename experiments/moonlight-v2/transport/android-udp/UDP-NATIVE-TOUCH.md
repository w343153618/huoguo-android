# Experimental native Android touch over authenticated UDP

This is a component for the isolated UDP probe, not a replacement for the
production app input path. It injects Android touchscreen events through the
existing persistent local scrcpy/InputManager service. It never sends Mac mouse
events and never spawns a shell command per touch event.

## Integration contracts

Phone source: `../../../nps-transport/phone/UdpTouchControl.java`, package
`local.remoteandroid.direct`. Create `new UdpTouchControl(surface, sender)` on the
UI thread. Call `setGeometry(streamWidth, streamHeight, rotationDegrees)` when
actual source geometry changes. The default display rotation is 0. Pass an
inverse quarter-turn of 90/180/270 only when the displayed video itself has been
rotated relative to the source image; activity orientation alone is not enough.

`Sender.send(byte[] plaintext)` must use the **same session-wide authenticated
send lock and monotonically unique AES-GCM nonce sequence** as READY, keyframe
feedback and all other client datagrams. Retransmission uses the same inner
logical touch sequence but a fresh outer encryption sequence every time.
Authenticated HGTA replies route to `onAck(payload)`. `snapshot()` reports only
aggregate counters, and `close()` emits three copies of the final CANCEL without
waiting for ACK or sleeping, then stops the input scheduler. Close before
destroying the session key or UDP socket.

Host source: `udp_touch_control.py`. After peer, session tag, AES-GCM and replay
checks, route HGUT payloads to `UdpTouchBridge.submit(payload)`. Instantiate:

```python
bridge = UdpTouchBridge(width, height, writer, send_ack)
```

`writer(bytes)` writes to `HostHardwareSession.channel('control')`, using the same
`control_lock` as local bitrate/IDR requests. This is an internal host connection,
not an internet TCP media transport. `hardware_stream.scale_touch()` maps the
stream dimensions in each scrcpy message to the guest's physical screen size.
`send_ack(bytes)` uses the shared server UDP encryption sequence/send lock.
The writer callback must have bounded I/O; a blocked local socket must not hold
the main media receive thread. The bridge owns a bounded input queue and a
dedicated writer thread; `submit()` never waits for that writer.

Call `close()` while the local control channel is still available, so the bridge
can release every active finger. `stats()` reports counters and active pointer
count without coordinates. `writer_alive=true` after close means the callback
has not returned within the bounded join; do not claim input cleanup completed.

## Wire format

All fields are big endian. HGUT plaintext is 28 + 10 × pointer_count bytes, at
most **128 bytes for 10 fingers**, before the existing session encryption.

| Offset | Field | Meaning |
| --- | --- | --- |
| 0 | `HGUT`, 4 bytes | Touch snapshot type |
| 4 | version, u8 | 1 |
| 5 | action, u8 | DOWN=0, UP=1, MOVE=2, CANCEL=3, HOLD=4 |
| 6 | pointer_count, u8 | 0–10, complete currently active set |
| 7 | rotation, u8 | Inverse displayed quarter-turn, 0–3 |
| 8 | sequence, u64 | Positive per-session logical sequence, below 2^63 |
| 16 | sender_us, u64 | Sender monotonic microseconds; not a shared wall clock |
| 24 | changed_token, u32 | DOWN/UP contact; 0 for other actions |
| 28 onward | token u32, x/y/pressure u16 | Normalized values 0–65535 per contact |

A token is unique for each *contact lifetime*, even if Android reuses the
MotionEvent pointer ID during a later gesture. An UP excludes the lifted token;
DOWN includes the newly pressed token. CANCEL carries an empty active set.
HOLD refreshes stationary fingers or the empty released state every 200 ms.

HGTA is a 16-byte ACK: magic `HGTA`, version u8=1, status u8 (applied=0,
superseded=1, expired=2), reserved u16=0, logical_sequence u64. Current host only
ACKs critical DOWN/UP/CANCEL, with status applied or superseded. ACK means local
control messages were written, **not that the guest processed them or a visible
frame reached the phone**.

## Loss, ordering and limits

- DOWN/UP/CANCEL have at most four scheduled sends, using 20/40/80 ms retry
  intervals after their initial send. There is no unbounded reliable stream.
- MOVE is latest-only and coalesced by the dedicated sender/host input queues.
- Every message is a complete active set. A later MOVE/HOLD repairs a lost DOWN;
  a later snapshot excluding a finger repairs a lost UP. No mouse fallback.
- Older logical sequences are discarded. Critical duplicates get an ACK but
  never extend a held finger's lease or inject the action again.
- A stationary finger is released after 750 ms without a newly applied valid
  snapshot. Link loss, closing the probe and CANCEL therefore have release paths.
- An old MOVE/HOLD arriving more than 100 ms above the best locally observed
  clock offset is discarded. This measures **excess delay**, not absolute
  one-way latency. The first packet's network delay cannot be inferred, and this
  estimate is not a touch-to-photon measurement.
- Stream resize/rotation during an active gesture cancels its previous contacts.
  Touches starting in letterbox borders are ignored; active gestures are clamped
  to the actual video content rectangle.

## Validation boundary

Run from the source tree:

```sh
python3 experiments/moonlight-v2/transport/android-udp/test_udp_touch_control.py
```

The 13 meaningful pure tests cover multi-pointer conversion, real scrcpy packet
layout, lost DOWN/UP repair, duplicate/late events, contact ID reuse, cancellation,
stationary holds/disconnect leases, relative excess-delay drops, all rotations,
malformed input and writer backpressure. Java compilation against Android API 37
also passed. These checks establish protocol behavior and input packet creation.
They do **not** establish true phone multi-finger acceptance, actual guest touch
processing, touch-to-photon latency, WAN/P2P behavior, or V50 experience.
