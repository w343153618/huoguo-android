# Optional bounded catch-up in the common native Pacer

This is an explicit experiment in the common `media_datagram.hpp` Pacer. It
does not change the default strict mode, phone buffering, 80 ms complete-frame
deadline, encoder rate, wire budget or transport. It is not WAN congestion
control, available-bandwidth estimation, retransmission or a guarantee against
scheduler stalls.

## Interface and integration contract

```cpp
Pacer pacer(wireBitrateBps, burstBytes);  // second argument defaults to 0
// Accepted burstBytes range: 0..4096, in the same complete wire-byte accounting
// passed to reserveBefore. Root's initial experiment uses 2048.

auto slot = pacer.reserveBefore(nowUs(), packetWireBytes, frameDeadlineUs);
if (!slot) {
    // Reservation failed: state/credit unchanged.
} else {
    // Wait for slot, then write. Existing deadline checks still apply.
    if (successfulCompleteWrite) pacer.sentAt(actualWriteCompletedUs);
    else pacer.cancelReservation();
}
```

For a nonzero bucket, every successful reservation must be settled with
`sentAt` before another reservation; missing settlement throws. Actual write
time cannot precede the reserved slot. Cancellation rolls back the unsent
reservation's debt. A partially written IPC record still uses the caller's
existing fatal handling; it must not be continued as a valid record.

With the default `burstBytes=0`, legacy `slot`, `readyUs` and `reserveBefore`
sequences are unchanged. `sentAt` and `cancelReservation` are no-ops in this
mode. Thus the default preserves its historical behavior, including the fact
that it did not settle scheduling oversleep against actual writes.

`readyUs(now)` always returns the **actual next slot** `max(now,nextUs)`. This
matches the slot returned by `reserveBefore`. Full-frame admission remains
conservative: it does not subtract catch-up credit from the frame's theoretical
serialization duration or grant a later deadline.

## Debt and burst bound

For nonzero bucket B, the allowed historical credit is
`floor(B * 8 * 1e6 / bitrate)` microseconds. It must round down; round-up at
40 Mbps could grant four extra bytes in a tight all-small-packet window.
Each packet's serialization cost continues to round up.

The virtual schedule is clamped no earlier than `now-credit`. Actual slot is
`max(now,virtualSchedule)`, and the reservation advances virtual schedule by
packet cost. After a write, `sentAt` clamps the schedule again to at least
`actualWrite-credit+packetCost`, charging the overdue packet at its real time.
Without this second step an overdue packet plus a fresh full catch-up bucket
could exceed the desired burst envelope.

The intended actual-write envelope is:

```text
bytes in a contiguous observation window
    <= rate * window_duration + bucket_bytes + maximum_packet_bytes
```

The one-packet term accounts for packet boundaries. Credit is finite: long
idle time or a long scheduler stall does not accumulate unlimited catch-up
capacity. A missed deadline still cancels output. For native integration,
`actualWriteCompletedUs` describes completion of the native IPC write; it is
not the network packet arrival time. Socket scheduling needs its own matching
accounting, and real phone measurements remain required.

## Reproducible offline checks

From the repository root:

```sh
python3 scripts/probes/check_udp_pacer_catchup.py
```

This compiles the actual common Pacer in an isolated temporary directory,
verifies the existing external dependency pins/cleanliness, and runs pure
clock arithmetic. It opens no socket, codec, phone, emulator or service.
The tests use **actual simulated write times**, not only planned slots, and
check 229,211 contiguous sliding windows across rates 0.5/1/16/40 Mbps,
bucket sizes 1/2048/4096, packet sizes and long scheduler stalls. Legacy
strict mode is checked for identical sequences and rejection semantics; its
actual burst envelope after large oversleep is not claimed as a new guarantee.

The 2026-10-01 result has 1,608 passing checks:

| Model, 120 requested packets of 1148 wire bytes at 16 Mbps | Strict default | 2048-byte bucket |
| --- | --- | --- |
| 100 us sleep overshoot per packet, 20 us processing after each write | 120 packets, 68.406 ms | 120 packets, 67.482 ms |
| Additional 2 ms stall every tenth packet, phase 4 | 109 packets, last at 79.098 ms | 118 packets, last at 79.246 ms |
| Additional 2 ms stall every tenth packet, phase 9 | Not used as acceptance baseline | 119 packets, last at 78.744 ms |

The ordinary 100 us model already completes in strict mode. It would be wrong
to claim that 100 us alone necessarily accumulates into an 80 ms failure.
The stronger models show improved progress with bounded credit but also show
remaining deadline failures. They deliberately retain failure cases rather
than implying that 2048 bytes guarantees every 120-packet frame completes.
No result here measures real video smoothness, phone FPS or WAN performance.
