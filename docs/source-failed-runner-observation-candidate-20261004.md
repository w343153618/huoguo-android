# Explicit failed-runner lifetime observation candidate

The default-OFF source runner now has a separate readonly observation of its
two C lifetime receipts after a nonzero launcher result. The normal six-file
success observer still rejects missing XML or missing Java retirement; this
candidate does not weaken that contract or replace a reader/factory.

Previously, a nonzero launcher result prevented collecting even an available
`started`/`waited` pair. A timeout or cancellation could therefore hide useful
main-child lifetime information behind the overall unqualified UI result.
The new [observer](../scripts/probes/source_failed_runner_observation.py)
reads only an already registered exact scope after local ADB reap and an
independent actual exec-parent header. It requires a trusted current cleanup
authority callback before reading and after parsing. Lost authority, missing
header, missing wait, incomplete collection or metadata mismatch leaves the
original scope registration intact.

The fixed4096-byte envelope contains scope metadata, two distinct owned regular
0600/single-link file identities, and only `started`/`waited`. Deployed native
and JAR inode/mode/hash checks remain before and after the read. Both receipts
must match the expected parent PID/start, sole child UID/start and actual scope
device/inode. No log, XML, input, process search, signal, deletion or launch is
in the generated read plan. The observer creates no registration on its own.

An append-only private `failure-<nonce>.json` journal records only closed scalar
fields: reason, exit kind/value, TERM/KILL flags, wait/EOF/output length, exec
gate and ownership-error status. Natural0 also remains an unqualified lifetime
observation when the normal framework-retirement chain is unavailable. Every
result keeps `remote_scope_may_exist` and `lease_release_unaccepted` true, and
actual execution/retirement/permission/source/removal fields false. Arbitrary
XML, logs, credentials, extra fields, boolean counters or promoted authority
are rejected. Journal replay cannot replace the original outcome or close a
scope.

9 new checks plus37 affected observation/journal/binding checks passed:
46 checks/1.551s. These use fake remote reads and an actual local private journal;
local `sh -n` only parses the read plan. They cover natural0/nonzero/exec127,
TERM/KILL/cancel, output-limit or retained-pipe states, unreaped/ownership-error
rows, partial/foreign/header/metadata rejection, current-authority loss, buffer
clearing and outcome replay. They are not an Android runner or UI-service test.

Readonly resumption checks found the phone experimental App/helper absent and
candidate45560/45963/45965 without listeners; no reservation or service signal
was issued. The M1 guest, source App, M5, NPS and phone data/settings were not
changed. App/JAR/native artifacts and published manifests are unchanged.

This closes a diagnostic gap in the failed path, not the actual permission-lease
or global UiAutomation-retirement boundary. The observer does not hold a server
lease itself. Current App/source authority, failed remote service retirement,
qualified exact scope removal and fresh post-maintenance source identity remain
necessary before device UI execution. Keep the previous3s/6s/15s and byte limits.

Next review how to keep the actual source authority held across unknown remote
retirement. Also assess a screenshot-only gRPC qualification route under a
normal current authenticated App Attempt: it could avoid starting UiAutomation
for source pixels, but requires its own fresh geometry/state and input gates.
Do not use historical coordinates, source identities, formats or old campaigns.
Moving-public UDP diagnosis remains pending; this is not an FPS/latency fix.

The preceding NPS-encryption commit4f4404b72819f3d222d8bb6fc7d52055438e9e92
has its own actual overall/build/udp_candidate success at run37188401639.
The preceding observation44a5b51 also completed both jobs at37187015743.
This new source needs its own precise-SHA CI; neither run is its validation.
