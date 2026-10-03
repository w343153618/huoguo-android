# Atomic idle drain source candidate — 2026-10-03

`UdpLanSessions.begin_idle_drain() -> bool` reserves an idle **live registry**
against concurrent `create()` under the registry's existing reservation lock.
It succeeds only when no active/building/waiting/starting/closing reservation
exists, the pending-cleanup list is empty, the completion event is set and no
cleanup failure has occurred. It never calls reap, expires a lease, revokes a
session, stops a worker or performs I/O to manufacture an idle result.

Success sets a sticky `_draining` flag. A later valid authenticated create
returns `503 / udp_registry_draining` inside that same critical section before
generating session credentials or entering its factory. Repeat successful drain
is idempotent, including a later idle close. A closed registry without a prior
successful drain returns false. Busy failure leaves admission unchanged; normal
READY/ALIVE, touch, account ownership, cancellation and cleanup continue. This
candidate makes no host/friend isolation claim and does not authorize stopping
formal services.

The method is an in-process maintenance API only. Neither gateway gains a route
or settings field for selecting drain. Closed NPS request schemas reject such
fields; the legacy LAN adapter may ignore unknown settings, but they cannot
invoke maintenance. The default path never calls the new method.

Owned fixtures use the actual registry and HTTP adapters with inert workers and
fake authentication. They cover both deterministic race orderings and a
simultaneous thread barrier, pending factory/start/stop, cancellation before
factory completion, deferred cleanup, cleanup failure, unconfirmed completion,
expired-but-not-reaped reservations, sticky rejection, idempotence and an
already-authenticating POST. No encoder, phone, network listener or service was
started. These are source/concurrency checks, not a deployed handover result.

## Future exact-instance receipt — design only

An external maintenance script cannot import this module and create a fresh
registry as proof that an existing gateway is idle. It must invoke this method
on the **same object that accepts that gateway's POST requests**. A future
gateway-owned Unix-domain control socket can provide that operation inside an
owner-only directory (0700, socket 0600), verify macOS local peer credentials and
use a closed single-operation protocol. Do not expose it through HTTP, NPS,
Tailnet or another public listener.

Bind a request to the expected per-start gateway instance identity, frozen
source digest and process start identity, plus a fresh caller nonce. The
gateway should return a small payload-free receipt containing those exact
identities, echoed nonce, a typed accepted boolean and monotonic observation
time. Do not include accounts, passwords, keys, session IDs or guest/file data.
The supervisor verifies the receipt against the expected owned process and
source before requesting ordinary shutdown. A rejected, missing, malformed or
stale receipt authorizes no signal. A successful sticky drain prevents later
session creation; it does not establish any other gateway or formal service is
idle. This local control channel, receipt protocol and supervisor are **not
implemented by this change**.

## Compatibility with the existing running gateway

The currently recorded `a776828` gateway lacks this API. A receipt from this new
source does not describe that old process. Do not inject new Python into it or
use `lsof -> kill` as an atomic handover substitute. There is no public drain
endpoint added here and no migration or signal was performed.

The existing bounded 3600-second process lifecycle remains the compatibility
path: a successor supervised job can attempt its exact owned control bind,
fail before media/guest work while the old listener holds it, and retry under
the existing throttle after the old process exits naturally. The previous
startup fixture already proves this bind-before-worker ordering. Recheck it
when composing a new supervisor. Preserve the formal gateway/NPC and other
candidate identities throughout.

Natural process expiry is an existing lifetime boundary, not a proof that all
sessions were idle: a newly admitted 120-second session near that deadline can
be ended by the old gateway's ordinary final shutdown. Therefore this path must
not be advertised as an absolute no-interruption guarantee. Exact idle-only
handover requires the future same-instance channel above, or another explicitly
authorized admission barrier with equivalent in-flight-request semantics.
