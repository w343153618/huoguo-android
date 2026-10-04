# M1 / M5 NPC client encryption deployment

The user's request to enable encryption on both NPC lines is completed. The
existing M1 client1466 and M5 client1468 now have `Cnf.Crypt=true`, verified in
persisted clients, authenticated runtime tables and each web edit form. Both
remain online in `quic,quic` mode. The UDP tasks1412/1413 still use public
15556/15558 and loopback45965, and their runtime client configuration reads
encryption enabled. Compression remains false.

Evidence: [redacted actual results](nps-mac-client-encryption-results-20261004.json).
The deployed helper SHA is
`927b7eb927890d450b3c87029a0bcb6781115ee4c2f12d2dedf51aed3fc23f4c`.

## What the checkbox means

The old unchecked checkbox was NPS's optional tunnel wrapper encryption. It
did not mean the existing QUIC traffic was plaintext: QUIC protects application
data using TLS-derived session keys. See [RFC9001 packet protection](https://www.rfc-editor.org/rfc/rfc9001.html#section-5).

Official v0.34.7 [client edit](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/client.go)
updates the existing shared client's `Cnf.Crypt` and saves clients. It does not
restart the client or NPS. [UDP forwarding](https://github.com/djylb/nps/blob/v0.34.7/server/proxy/udp.go)
copies this flag into each new link; NPC reads the link flag for its matching
wrapper. This is separate from App media authentication, integrity and replay
protection, and is not host-file/LAN isolation acceptance.

## Actual change and preservation

The root-only [scoped helper](../scripts/deployment/enable_mac_nps_encryption.py)
defaults to authenticated inspection. Explicit `--enable` first backs up every
regular configuration file, checks both clients/tasks idle, then submits the
complete original client forms with only `crypt=true`. Identity, web credentials,
limits, blacklist, compression and config-connection permission are retained;
flow counters are not reset. Fields that would be rewritten by HTML escaping,
TOTP normalization or nonzero-expiry conversion are refused. Between the two
edits it verifies the original process, rules, online clients and all static
client/task configuration, rather than overwriting a JSON table.

Fresh restricted backup,9 configuration files individually hash-verified:
`/root/nps-backups/client-encryption-20261004T081125Z-63ca92f2/`.
Secrets remain only in the cloud's existing restricted locations and backup.
The public helper deployment and redacted apply receipt are at
`/root/.config/huoguo-nps-client-encryption/20261004T081017Z-ad165a6d/`.

Actual maintenance result:

- Both client edit requests accepted; persistent, runtime and web selections true.
- All6 existing selected tasks running; IDs, targets, protocols and ports retained.
- All40 NPCs online before maintenance still online afterward, missing0.
- Formal NPS PID3412973/start135647986 and official core SHA9b9a36a2 unchanged.
- Domestic `geo_in`/`geo_fwd` rules and references unchanged; unrelated config
  and static client/task settings preserved. No service signal/reload/restart.
- M1 NPC43079 and its relay41372 unchanged. Optional SSH inspection of the
  historical M5 LAN address timed out before any command; cloud runtime and
  M5 public health succeeded. A local M5 process PID comparison is not claimed.

One initial read-only preflight wrongly applied the root-owned configuration
reader to the official executable, whose preserved archive owner is UID1001.
It refused before backup/mutation. The corrected binary verifier binds the
open no-follow file to the running executable's actual inode and checks the
known official SHA; it does not change executable ownership. The final frozen
helper above completed with exit0.

## Validation and boundaries

9 new inert checks plus44 existing dual-task checks passed:53 tests/0.051s.
They cover full form preservation, rejected side-effect cases, active client
and task refusal, exact web selection, unexpected static changes, backup before
edits, two-client scope, read-only default and idempotent retry. No fixture
operates a production service.

After the configuration update, new public TLS connections through both
legacy gateway tasks were successful: M1 TCP15556 and M5 TCP15558 `/ping`200,
TLS1.3 and fixed existing certificate pins. Each outbound socket was bound to
M1 physical en7 using IP_BOUND_IF, with no proxy fallback. This validates new
encrypted NPS-link compatibility and endpoint health; these small HTTPS checks
are not UDP media, FPS, cellular/V50 or physical-latency acceptance. This round
did not create a phone media session or bind a UDP media target. No APK or
update manifest changed. NPS's QUIC stream remains distinct from native QUIC
Datagram/P2P and does not establish elimination of head-of-line blocking.

If rollback is necessary, use each existing client edit form to change only
encryption to No, retaining the current complete form. The original values are
available in the restricted backup. Do not restore whole live JSON databases,
restart NPS or disturb unrelated NPCs for this checkbox. The maintenance is
complete; resume the M1 source/current-lease and public-media investigation.

Exact pushed source4f4404b72819f3d222d8bb6fc7d52055438e9e92 subsequently
completed overall/build/udp_candidate success in GitHub run37188401639.
This is source/build validation, with no new signed App release.
