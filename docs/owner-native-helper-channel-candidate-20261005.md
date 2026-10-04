# M1 native helper control channel candidate — 2026-10-05

Default OFF. Fifteen new actual host checks pass within92affected checks
(11.800s); the preceding13-check pass2.789s is preserved. A locked NDK29/API30
arm64 compile succeeds. This is a partial local helper control transport, with
no Android stage, PM, ART, phone, source input, UI, authentication or media run.
It does not change UDP media, NPS, an App/helper APK, JNI or a release manifest.

The preceding owner consumes stdin to EOF during upload. That closed descriptor
cannot later carry driver or uninstall commands. The NEW channel keeps one
actual owned input FD open: a112-byte closed header selects the exact pinned APK
length/hash and nonce/sequence, exact payload bytes follow, then a distinct SEAL
record ends upload. Extra bytes corrupt SEAL and refuse. The actual sole APK
writer closes before readonly hashing while the control FD stays open. No extra
file writer, shell-owned stage, PID adoption, password or private-file fallback
is introduced. The existing root-only owner source/header and all old frozen
layouts/contracts remain unchanged and incompatible with old tmp/root:shell
retirement receipts.

The112-byte header contains magic HGHC0001, closed kind, zero flags/reserved,
4-byte payload length,24-byte lowercase hex nonce,8-byte sequence and64hex SHA.
Only UPLOAD has a payload; the production library binds86419bytes/helper28db.
Other commands have zero payload and zero SHA fields. Nonce, sequence, length,
kind, reserved bytes, hash and phase must match. There is no header/environment
way to change the caller's finite monotonic read deadline, capped at3000ms.
The3s command budget is retained; filesystem/process creation is not a hard
wallclock guarantee. Request parsing has no arbitrary command, PID or network
endpoint. The helper nonce is a framing identity, not operator authorization.

After SEAL the parser accepts ordered INSTALL, DRIVER_DONE, UNINSTALL and RETIRE
requests or a closed CANCEL. These are only requests. Even a complete sequence
performs no PM, driver cleanup or scope deletion, and all operator/lease/release
fields remain false. Advancing parser state never acknowledges that an operation
occurred. A future whole trusted caller must separately bind each request to
actual held native children/driver, exact independently observed package, current
normal App Attempt, operator/server lease, helper/input/scope cleanup and the
original admission gates. There is no complete live caller here. Unknown work
still needs actual held owner/supervisor/socket; production owner close continues
to refuse a possible created scope. The Android executable is inert without
arguments and rejects all activation arguments.

Actual host fixtures keep stdin open after upload and receive writer-closed
readback before sending later requests. They also cover fragmented input, maximum
bounded payload against independent hashlib, bad headers/nonce/sequence/size/hash,
truncation, missing SEAL, extra payload, skipped/replayed phases, EOF after SEAL,
partial-frame timeout, closed CANCEL and cancellation of the exact held parent
Popen. Every created scope remains; fixture handles closing is not remote cleanup.
Valid wire JSON does not prove permission, PM server state, FPS or performance.

The compiled arm64 binary is23280bytes, SHA
87506466b07bfd06243e380aca2793d31cbdbde7cc091a3ba694c58ecd5cb438.
Actual input/source/header/compiler pins are recorded privately and in the build
metadata. It was neither staged nor executed on Android. One fresh phone query
returned device-not-found; no repoll or mutation followed. First qualification
on actual device return remains the unchanged reviewed f0/d043/helper82f normal
saved-UI/current-Attempt frame-only round, before any new artifact/LAN experiment.

Preceding dca400b/run37215217607 independently passed overall/build/UDP with
2057Linux tests/93.735s/11existing skips. It does not cover this new channel.
Read this new SHA's exact CI after the meaningful push, then continue actual
owner/PM/driver lifecycle binding fixtures. Do not execute a partial installer
or relax saved-credential/operator/admission/package/scope gates for progress.
