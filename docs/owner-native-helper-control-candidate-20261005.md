# Retained host/native control scheduling candidate

This default-OFF source candidate adds explicit START/POLL scheduling after the
native install phase. It has no production activation entry, device installer,
gateway/admission integration, cleanup/release wrapper or new App/helper/JNI.
Its host controller only accepts a separately pinned host fixture binary and
fixed fixture grammar. Actual Android callbacks and whole live binding remain
unimplemented; do not stage or execute this partial candidate on the phone.

The host constructs its own actual Popen, fixed control pipe and two continuous
drains under the exact selected environment. Possible-start and possible-request
checkpoints precede creation/writes. Requests require fresh callbacks on the held
host object. The native scheduler separately constructs its own native graph;
it invokes the existing native package/driver callbacks on that actual graph.
Host booleans are never encoded as native qualification. A closed event only
schedules a next request, and an idle host poll is not an acknowledgment.

The new 48-byte HGDS0001 START/POLL header has zero reserved bytes, the exact
24-hex nonce and its own ordered 64-bit sequence. Upload/SEAL/install/DRIVER_DONE
retain the existing HGHC0001 sequence. Every operation has one outstanding
request; malformed/replayed/skipped/queued input or trailing readiness refuses.
Each command/poll remains at most three seconds. An actual five-second child
spans multiple polls under its original native constructor/driver ceilings.
Host preparation also counts in its original thirty-second fixture ceiling.
New requests or delayed collection cannot renew those deadlines.

The host retains each pipe's first 64 KiB while draining all output; overflow,
stderr, malformed/duplicate/extra/unordered events, late replies or unknown EOF
refuse the entire result. Local parent exit zero plus actual dual EOF and the
closed fixture footer proves only closure of that actual host transport.
It does not prove remote package/PM/App cleanup, scope retirement or release.
Timeout, interrupt and failed post-start checkpoint retain the actual Popen,
control and drains without signals. Unknown native phases remain sticky and
retain the native graph; there is no destructor/recovery by JSON/PID. The test
fixture itself exits into its disposable host namespace; this is not a live
Android unknown-owner supervisor or a remote retirement receipt.

Validation counts, the initial host-fixture failure, final checks and exact
eight-input NDK29/API30 compile pins are in the adjacent JSON. Host tests use
real child processes, control pipes, wait/EOF and elapsed five seconds, with
synthetic Android/operator/Attempt callbacks. The Android ELF is compiled only,
never staged or run. The phone was checked once and was unavailable; original
host readback/reaped read clients are snapshots, not permission or server lease.

Next bind the actual native-owned helper output/current captured Attempt and
normal codec/audio/input cleanup to concrete same-process qualifiers, then
matching package/independent absence/privileged-writer exclusion and same-created
FD retirement. Host source/operator/phone/admission/gateway gates must use their
actual held objects separately. Wire events, numeric JSON, account and pins
cannot supply those gates. Actual gateway/reaper/full roles/original/formal plus
independent phone/helper/input/scope cleanup still project only the original
five release fields. On phone return, unchanged reviewed f0/d043 frame-only
qualification remains first. No partial activation, larger timeouts or buffers.
