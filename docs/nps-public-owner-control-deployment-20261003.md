# Public owner UDP control deployment, 2026-10-03

The existing official NPS v0.34.7 process was not restarted or replaced. The
original38 online NPC identities remain online. Existing QUIC bridges and
TCP15556/15558 formal tasks remain intact; independent UDP tasks1412/1413 use
those public ports in the UDP namespace and fixed loopback45965 backends.

Two authentication-only TCP tasks were added:1414 exposes M1 HTTPS49556 to
loopback45561;1415 exposes M5 HTTPS49558 to loopback45561. They do not transport
App media. The candidate handler has no CONNECT/TCP-media route. Existing
accounts and session GCM, integrity and anti-replay remain required.

Before each mutation a full nine-file NPS configuration backup was made in the
restricted cloud backup location. The initial guarded addition stopped after
observing task1398 IsHttp true-to-false. The official pinned source proves this
field is derived from the first observed payload and serialized into the task
file, rather than a proxy protocol switch. Preservation now normalizes only an
existing strictly boolean IsHttp value, retaining key presence and rejecting
nonboolean values. Mode, HttpProxy, Socks5Proxy, targets, ACLs and all other
configuration fields remain guarded. The preexisting matching M1 task was read
back and retained; only the missing M5 task was then created.

For owner-only public testing, M1's existing permanent wyw account was restored
from the matching existing M5 record, with an atomic merge and restricted backup.
The existing huoguo record was preserved. No temporary account or new password
was created; no gateway restart was required. Public candidate admission remains
wyw-only. M5 friend use stays on the existing formal stable service.

Source fixtures verify fixed profiles, account admission, closed settings,
loopback placement, cancellation and preservation. Actual public UDP echo tests
predate these control tasks and are not phone media acceptance. The candidate
App build and real-phone public-media verification are recorded separately.
NPS internally uses reliable QUIC streams; this does not claim a QUIC Datagram
relay or cellular/V50 performance acceptance. Cloud domestic-source filtering and
physical outbound routing were not changed.

Private deployment receipts, complete backup locations and unredacted logs stay
in the existing restricted locations and ignored evidence directory.
