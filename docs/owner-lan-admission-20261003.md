# Owner LAN trace admission — source helper, 2026-10-03

`scripts/probes/owner_lan_admission.py` adds a library reservation for a bounded
owner M1 LAN diagnostic. Its CLI only describes a dry run: no bind, HTTP,
credentials, device work or signals. No running gateway was changed or stopped.

The old persistent dd43 gateway rejects a new candidate when LAN45560 listens
or UDP45963 exists, including its active-session monitor. Starting that LAN
listener over an existing public UDP session can therefore revoke it. Conversely,
the LAN gateway's formal TCP15556 check does not see the public UDP registry.
Different ports alone do not provide source exclusivity.

The library first exclusively reserves `127.0.0.1:45965`, explicitly binds and
reads back Darwin `IP_BOUND_IF` for `lo0`, and verifies reuse options are off.
An occupied tuple fails without authentication or guest work. The reservation
is held throughout the LAN experiment and its confirmed cleanup; merely starting
the LAN listener is not a reason to release it, because an earlier public POST
may already have passed the old `busy()` check.

Port ownership alone is insufficient. Old `LanMediaWorker.stop()` closes UDP
before waiting for startup and native cleanup. An already-starting worker can
construct `HostHardwareSession` outside its lifecycle lock. A process scan or
fixed sleep does not rule out a child that has not appeared yet.

The trusted caller therefore supplies a finite, existing-account HTTPS witness
to the exact running M1 loopback control45561 with the existing certificate
pin. It sends one ordinary closed-schema `/udp/session` POST while the reserve
is held, never sends READY, and returns only `{status, error}`. Only exact
`503 / udp_worker_unavailable` is accepted. In frozen dd43 this outcome is
produced solely after the same live registry's atomic checks have found no
active reservation or cleanup failure, followed by a failed worker factory.
The reserved fixed bind fails in the constructor before hardware/ADB startup.
409 busy, 503 cleanup failure, 401/403, unknown responses or network failures
all abort. Do not substitute an imported fresh registry or `lsof` for this
live-object witness. This is an intentional failed factory, not a successful
media session or a new maintenance API.

The witness's proof time is the registry's atomic `create()` check: no old
reservation or recorded cleanup failure existed at that point. It does not set
the registry's sticky `begin_idle_drain()` flag or permanently reserve an idle
registry. The continuously held UDP reserve, not the503 result alone, prevents
subsequent and already-admitted public factories from starting guest work.

After that witness the caller must provide a closed readback matching the
expected gateway PID **and start identity**, exact source-entry SHA256, and
frozen runtime manifest SHA256. All four owned process counts must be zero and
coverage complete: gateway descendants, hardware process groups (including
orphaned descendants), packetizers, and owned guest control servers. A current
PPID-only scan is insufficient: `HostHardwareSession.close()` sends killpg only
while its parent is running. The caller owns this source-specific whitelist and
read-only verification; the library never parses full argv, reads secrets or
signals a process. The runtime manifest digest must cover actual selected
source/dependencies, not merely an artifact's name or an unrelated binary.

Integration ordering:

```python
guard = OwnerLanAdmission(expected_gateway, trusted_https_witness, trusted_readback)
with guard:
    guard.mark_owned_lan_starting()  # Before any owned Popen/thread/start action.
    # Start frozen trace-enabled udp_lan_gateway on LAN45560/UDP45963;
    # use existing account/cert files, selected M1 guest and encoder/packetizer.
    # Run one bounded30–45s phone window; normal UI logout and owned shutdown.
    guard.confirm_owned_lan_quiescence({
        'event': 'candidate_shutdown', 'quiescence_confirmed': True,
        'stop_failures': 0, 'gateway_exit_confirmed': True,
        'owned_media_exit_confirmed': True,
    })
```

The final receipt combines the actual LAN gateway shutdown outcome with the
caller's independently confirmed owned gateway and media process exits. Collect
output from the **same Popen instance started
inside this guard**, then confirm that instance's exit and owned media cleanup.
The closed receipt schema contains no LAN PID or start identity: it cannot
itself reject a previous attempt's otherwise valid receipt. A historical JSON
file or the first matching line from a shared log is not sufficient.
On unconfirmed cleanup, context exit retains the socket and preserves any original
experiment exception. Keep the guard and supervisor alive, perform only bounded
cleanup of that owned attempt, then confirm and `close()`. Never manufacture a
receipt or kill an old service to obtain one. An OS process crash releases its
socket; this is not a persistent cross-process lease or crash-proof handover.

The existing formal TCP service can still admit a new M1 connection after a
read-only check. Its worker/monitor checks remain, but this helper does not add
an atomic formal-service admission gate. Use an exclusively scheduled owner
window and skip on formal activity. No M5, NPS, NPC, account or default service
change is authorized by this helper. The eventual trace path is physical LAN,
not a repeat acceptance of public NPS media, V50 performance or optical latency.

The fixtures cover exact witness and identity schemas, busy/cleanup/auth/error
rejections, admission ordering, retained cleanup failure and preserved primary
errors. A Darwin kernel fixture verifies real loopback exclusivity and interface
binding by calling the shared socket primitive directly on its **own ephemeral
tuple** (`127.0.0.1:0`). It never enters the production guard or occupies45965,
or starts media, HTTP, devices or services. Source/kernel fixtures do not validate a live
witness adapter, source-specific process scan, handover or sampling overhead.

`tests/test_owner_lan_legacy_registry_witness.py` independently executes the
hash-pinned dd43 registry and its three parser/profile dependencies in private
module namespaces. It uses the current identical bytes, or requires the exact
local Git object `dd43a39f49f6dceb55854fbc6e43367d34f381c3` after source evolution;
it never substitutes a newer registry or silently skips missing historical
coverage. Four inert thread fixtures prove that normal and eventually failing
inflight stop both return409 before entering the new factory; confirmed stop
allows exact503 failed-factory witness; failed stop returns503 cleanup failure
before entering the factory. These checks open no socket, supply no password,
send no READY and perform no device/service operation. Their synthetic registry
is an offline lock-contract fixture, not the running gateway's idle receipt.
