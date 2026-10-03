# Owner UDP supervised-service preparation — 2026-10-03

This checkpoint prepares durable, independent owner-trial jobs. **Neither job
has been bootstrapped, and neither existing gateway was signaled.** The latest
user was using the experimental app; both formal and experimental owner sessions
are protected. A point-in-time empty UDP socket is not an atomic no-new-session
handover guarantee.

## Prepared jobs

| Host | Dedicated LaunchAgent | Current bounded gateway PID | Formal gateway PID |
|---|---|---:|---:|
| M1 | `local.huoguo.m1.udp-owner-trial` | 69199 | 41650 |
| M5 | `local.huoguo.m5.udp-owner-trial` | 83277 | 75248 |

Both plists use `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=60`, the
same frozen Python source commit `924879df231d708224b5a3e70ae941681995c35b`, the
same process limit3600 seconds and existing session limit120 seconds. Formal
plist environments are retained with explicit node guest selection and a
separate private state/diagnostic directory. M5 retains `--owner-m5-trial`.
Only `wyw` may authenticate; this does not admit the friend account or establish
friend host/LAN isolation.

The launch jobs bind their existing private HTTPS control to
`127.0.0.1:45561`/`lo0`; authenticated media workers still use
`127.0.0.1:45965`/`lo0`. Existing cloud control49556/49558, UDP media15556/15558,
formal TCP tasks and independent NPC identities are unchanged. Existing formal
busy admission and competing-candidate45560/45963 guards remain in the frozen
source. A busy startup may exit and launchd may retry; it must not touch or
restart the formal guest, formal gateway or NPC.

M1's former source, hardware runtime, packetizer and encoder were located in
`/private/tmp`, so retaining those paths would not provide reliable later-login
availability. Their frozen bytes were copied into
`~/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003`.
The JAR and capability pin match. The venv, generated proto and baseline encoder
links resolve to existing persistent M1 canonical runtime paths, without a tmp
dependency; `pyvenv.cfg` also has no tmp reference. M5 already uses
`~/Library/Application Support/AndroidRemote/udp-owner-20261003T065238Z`, which
is retained. The existing private account and TLS files are referenced by path;
their contents were not copied or printed.

## Validation boundary

Preparation validated all source snapshot hashes, executable availability,
JAR/capability agreement, venv configuration and grpc/protobuf/cryptography
imports. Plists round-trip and pass `plutil -lint`. Formal gateway plists retained
their exact original hashes. Both current candidates returned trusted local
HTTPS `/ping`200 with the correct node and UDP scope; two inspections found no
UDP45965 worker, but this is only an inspection boundary. New launchd jobs are
not loaded. No media session, phone operation, NPC or cloud mutation occurred.

Restricted receipts, exact command paths and plist hashes are in
`docs/evidence/owner-udp-launchagents-20261003/`. These preparation checks do not
establish supervised uptime, login/reboot acceptance, end-to-end media quality or
friend deployment safety.

## Next handover

Before activation, recheck the gateway's exact PID/command, UDP45965, current
children and control connections, and preserve active owner sessions as well as
formal sessions. Avoid canceling a newly admitted session between an idle read
and SIGTERM: the present candidate has no atomic idle-only admission-close API.
Prefer activation after the old bounded process exits naturally, or a controlled
idle handover whose admission and drain are independently established. Load only
the two dedicated prepared labels, then read back job PID/command, listener,
certificate/node health and unchanged formal/NPC state. Do not load another
watcher or replace the original gateway/NPC labels.

`RunAtLoad` here is a **user LaunchAgent login** action, not a pre-login system
daemon. `KeepAlive` prevents an exited bounded process from leaving a permanently
dead entrance once these jobs are loaded; it does not guarantee uninterrupted
media across the existing process TTL or machine sleep/reboot. These limits must
remain explicit until later lifecycle and boot acceptance is measured.
