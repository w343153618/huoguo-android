# Experimental authenticated UDP sender deadline regression

Read-only review after the six-case catch-up matrix found an actual sender
deadline gap. `SocketPacer.video()` checked the deadline before the common
authentication/send mutex and before AES-GCM sealing. A priority sender holding
that mutex, sealing work or scheduling delay could therefore make a packet
expire after its pacing check but before `socket.send`.

The actual sender source with fake clock/socket reproduced both paths: a
10,010,000 us deadline and a 20 ms mutex or seal delay resulted in a send at
10,020,000 us, 10 ms after deadline, without `PacingDeadline`. This was an
offline reproduction, not a measurement of a real audio packet, phone or WAN.
The initial two tests in `tests/test_udp_sender_deadline.py` failed before the
fix.

The sender now checks host `clock_ns` under its own mutex before sealing and
again directly before attempting send. With pacing enabled it requires the
remaining serialization cost for full HGUE/GCM+IPv4/UDP wire bytes, rounded up
in nanoseconds. The original absolute frame deadline is unchanged. The new
exception check is outside the socket `OSError` handler because
`PacingDeadline` is a `TimeoutError` subclass; it must not be reported as a
socket send error. `SocketVideoGate` retains its existing deadline recovery
path for missing original data or optional final parity.

A rejection before seal does not consume a nonce. A rejection after seal
retains the consumed nonce and never reuses it; subsequent authenticated
video/audio packets use monotonically increasing nonces. Sender and pacer
mutex ordering remains sender -> pacer, and pacing waits never hold either
authentication mutex or pacer mutex across the sleep. A rejected packet may
retain a conservative unsent pacing reservation; this avoids incorrectly
rolling back concurrent priority debt.

Each lane has bounded numeric audit scalars:

| Field | Meaning |
| --- | --- |
| deadline_rejections | Rejections at the new authentication/send checks; initial pacing rejections also remain in `socket_pacing.deadline_rejections` |
| max_auth_lock_wait_ns | Maximum time waiting for the sender mutex |
| max_seal_ns | Maximum AES-GCM sealing duration including scheduling |
| max_send_syscall_ns | Maximum interval from the final pre-send checkpoint to post-call sampling, including failed calls; includes check/bookkeeping and intervening scheduling, so it is not pure kernel time |
| send_completed_after_deadline_count | Successful sends whose completion timestamp exceeded their absolute deadline |
| max_send_deadline_overshoot_ns | Maximum successful-send completion overshoot |

Once a syscall starts, this code cannot interrupt it at the deadline or undo
an already posted UDP packet. A deliberately delayed fake send demonstrates
that limitation: a successful call taking 20 ms is counted and reported as
10 ms past the deadline. This is not described as a hard syscall guarantee.
Priority audio/touch/ping sends remain immediate with debt charged to following
video; their aggregate short-window rate is not promised to obey the video
bucket envelope.

Verification from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_udp_*sender*.py'
python3 -m unittest discover -s tests -p test_udp_socket_pacing.py
```

Post-fix results: 8 sender/deadline tests and 19 existing socket pacing/reference
tests passed. The four new tests cover mutex delay, seal delay, sealing that
ends before deadline but leaves insufficient wire time, and an already-started
syscall completing late. These tests use actual sender code, fake clocks and
fake sockets; they do not modify source at runtime except for local mock
callbacks, nor touch any phone, encoder, emulator, service or network. They
do not prove better real video FPS or WAN performance.
