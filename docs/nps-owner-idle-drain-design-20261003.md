# Trusted owner accounts and future idle handover — 2026-10-03

## Implemented source candidate

`udp_nps_gateway.py` accepts repeated **trusted process startup** parameters
`--owner-account`, limited to the closed names `wyw` and `huoguo`, with one or
two unique entries. The default remains only `wyw`. A single explicit `huoguo`
entry admits only that account; two explicit entries admit both. M5 still also
requires its separate `--owner-m5-trial` profile gate.

The registry accepts the same optional constructor policy only in `nps_owner`.
It copies/canonicalizes that policy into an immutable tuple, and the HTTPS
handler refuses construction if its normalized policy differs from the
registry's. HTTP stream fields cannot select accounts, routes or maintenance
state. POST/GET/DELETE still require the unchanged existing password
authentication. Status, cancellation and closed-session tombstones remain bound
to the account that created the reservation; an allowed second account cannot
read or revoke it.

This configuration supports the owner's explicitly authorized use of existing
`huoguo` credentials for a bounded experience trial. Authentication identifies
the account, not the human holding those credentials. It does not establish
friend host/LAN isolation or turn the experimental deployment into a friend
release. Existing account files and passwords are unchanged.

Thirteen new owned offline fixtures and related regression checks passed:
155 checks total. Authentication itself is mocked at its inherited boundary in
the new fixtures; no credential file, listener, phone or cloud service was used.
An actual `main()` fixture confirms that an occupied control45561 causes startup
to fail before any media worker, reaper thread or signal handler is created.
Formal-busy admission exits even before the registry/server. This permits a new
supervised candidate to wait for the old bounded process's natural exit without
signaling it. LaunchAgent/source deployment and live account acceptance remain
separate root-owned steps.

## Future idle-only maintenance design — not implemented

A future private management operation should invoke `try_begin_idle_drain()` on
the existing registry. It must acquire the **same `_lock` that reserves a
session in `create()`**, and only succeed when `_active is None`, no pending
cleanup remains, the quiescence event is set and cleanup has not failed. It must
not expire, revoke, stop or extend an existing reservation to manufacture idle.
If any building/waiting/starting/active/closing reservation exists, it returns
false and the manager must not signal the gateway.

On success it sets a sticky draining flag before releasing that lock. Every
authenticated `create()` must check that flag within the same reservation
critical section and return a safe503 before generating a key or calling its
factory. A POST already authenticating or parsing cannot escape this check.
If create reserves first, drain must fail; if drain wins, create cannot reserve.
Factories, worker starts/stops, file/network operations and process shutdown
stay outside the registry lock. Existing GET/DELETE and owned cleanup retain
their normal account checks; drain is not a public cancellation endpoint.

The management call must have **no HTTP or public network route**. A later
implementation may use a private local control channel in an owner-only
directory, verify its local caller, bind requests to the expected gateway
process instance and frozen source, then return a payload-free accepted/idle
result. Only after successful atomic drain and quiescence may the supervisor
shut down the server and load its successor. A `lsof` read followed by `kill`
does not provide this reservation barrier.

Required future fixtures: both concurrent create/drain orderings; pending
factory and start; revocation with incomplete stop; cleanup failure; stale
management process identity; already-authenticating POST; sticky denial of
later creates; no worker stop on a refused drain; no public management route.
The current allowlist change adds none of this management API or drain state.
