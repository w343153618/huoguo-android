# Parent teardown observations, 2026-10-04

This candidate observes the existing worker and encoder cleanup when the
existing capture trace is explicitly enabled. It changes neither native
encoding nor the close, poll, signal, wait, or timeout policy. It has not been
deployed or validated with actual media. The previous real auto5 traces and
check totals do not validate this candidate.

## Layer and identity

Two independent files accompany the existing capture path:

- `<capture>.worker-parent.json`: the `HostHardwareSession` parent observes its
  actual worker `Popen`. `cleanup_returned` covers the existing host close path.
- `<capture>.encoder-parent.json`: the worker observes its actual encoder
  `Popen`. `cleanup_returned` covers only the existing encoder cleanup block,
  before audio-control and capture-sink cleanup. It does not certify their exit.

Each reports the actual parent PID and the PID bound immediately after that
parent's `Popen`, plus one trace-only `getpgid` result or a closed failure code.
A PGID observation can race process exit. It is evidence, not a new ownership
credential. The existing group signal still uses the existing worker PID;
none of these diagnostic fields chooses a target or a cleanup branch.

Only the original `poll` and `wait` calls can report an integer exit result.
No extra poll, EOF, wait, drain, retry, or join was added. A recorded method
return from `Popen.terminate` or `Popen.kill` does not establish an internal
signal syscall. A returned `os.killpg` call establishes only that the syscall
returned successfully, not that the intended target received or acted on it.
These observations cannot prove why the historical auto5 encoder footer was
missing.

## Closed numeric receipt

The schema `owned-process-teardown-parent-v1` has exact keys, fixed role and
clock strings, bounded integers, strict booleans, and at most 24 operation rows.
Rows are:

`[operation, begin_ns, end_ns, clock_valid, outcome, result_known, result_int, failure_code, errno]`

Times use host `CLOCK_MONOTONIC`, never packetizer `CLOCK_UPTIME_RAW`, phone
clock, wall time, or a shared playback target. Outcome 0 means the operation
returned; outcome 1 means it raised. An integer returned by an existing poll or
wait may be known; `None` and other return values remain unknown. Rows describe
completed observations, not a guarantee of an uninterrupted process lifetime.

| Operation | Layer | Existing operation |
| --- | --- | --- |
| 1 / 2 | worker parent | channel shutdown / channel close |
| 3 / 6 | worker parent | initial / fallback worker poll |
| 4 / 7 | worker parent | `killpg` TERM / KILL call |
| 5 / 8 | worker parent | worker wait, timeout 5 / 2 seconds |
| 9 / 10 | worker parent | worker stdin / stdout close |
| 11 | encoder parent | encoder poll |
| 12 / 14 | encoder parent | encoder terminate / kill method |
| 13 / 15 | encoder parent | encoder wait, timeout 3 / 2 seconds |

Failure codes are 1 `ProcessLookupError`, 2 `TimeoutExpired`, 3
`PermissionError`, 4 other `OSError`, 5 other `Exception`, and 6 other
`BaseException`. Error text, foreign type names, argv, media bytes, and
credentials are excluded. The original exception is re-raised unchanged;
ordinary observation and publication faults are suppressed. A failed clock
is visible as unknown row timing and an observer error count.

## Publication and acceptance boundary

Each snapshot is at most 4 KiB. Publication uses one daemon thread, with no
join or additional teardown deadline. The directory must be owned by the
current UID and exactly mode 0700. Descriptor-relative, no-follow creation
uses an exclusive mode-0600 staging file, then closes it before an exclusive
hard-link publication; an existing final receipt cannot be overwritten.
There is no fsync or durability claim, nor a hard wall-time guarantee for the
asynchronous filesystem operation. Process exit may prevent publication.

The offline reader checks the directory and file through no-follow
file descriptors, exact UID and modes, a single link, size, stable metadata,
complete bytes, final newline, duplicate keys, and the closed schema. Missing,
partial, racing, oversized, malformed, or uncertain receipts remain `unknown`.
One layer never substitutes for its missing sibling. A reader of an experiment
must separately match the recorded PIDs to that experiment's owned `Popen`
records; a valid JSON file alone is not that association.

Both reader results explicitly retain `native_producer_final: false`.
Neither receipt closes the Swift producer lifecycle, proves callback
quiescence, certifies source/feed coverage, or changes analyzer acceptance.
Capture/footer clean-close and packetizer natural exit remain separate facts.
Trace-off constructs no diagnostic object, file, thread, or diagnostic clock
sample; its host close body is the original cleanup body.

## Validation

The new inert fixtures compare trace-off/on executions of the actual host
close and actual worker finally blocks, including normal exit, already-exited
poll, original TERM timeout/KILL, ProcessLookupError, PermissionError, audio
control, clock faults, publication/encoding/thread faults, repeated close,
role separation, restrictive file access, and malformed/partial receipts.
They execute no service, phone, native encoder, or media. Actual asynchronous
cross-process publication and real-media observation are still to be tested
under the existing protected experiment gate.

Root reran the four relevant modules:44 tests in0.118s, then full repository
discovery:1582 tests in60.204s, OK. Independent review ran20 new inert tests
and verified the final pins below. These are source/owned-fixture results,
not actual asynchronous publication, media teardown or cloud acceptance for
this new candidate.

- hardware_stream.py:a62d85390132804951f8fa160ea84172bcfa36bb117c6bff2b1a800cc8e7eb99
- tests/test_parent_teardown_receipt.py:4ad57e49935c07400722c1d0d6b3fb5c3c64705935416822f0ad6fc6bc7de447

## Subsequent owned-process and cloud observations

The actual HostHardwareSession close path ran against one separately owned
Python sleep process with a new process group. Its asynchronous worker-parent
receipt was read within the fixture's2-second observation bound, matched the
actual Popen PID/PGID, and reported operations3/4/5/9/10 with the actual wait
result-15. This is an owned OS-process fixture, not a Swift encoder/media
exit, a parent-exit publication test, or complete native producer coverage.

Exact source commitb07009b31245050ec28ef2ed1b4c69453c48d0ea completed
[Actions run37145236790](https://github.com/w343153618/huoguo-android/actions/runs/37145236790)
with both build and udp_candidate success. Linux discovery executed1565
tests in66.971s, OK with11 existing platform skips. Stable Android compile/
lint took1m45s; isolated UDP compile/lint took2m12s. This cloud result
validates this source snapshot; phone/APK/native-media acceptance remains open.
