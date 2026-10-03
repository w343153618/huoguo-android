# Alpha6 public owner UDP App and update-channel source checkpoint

This checkpoint covers source and owned fixtures only. No APK was built, signed,
installed or published by this source task, and no phone, Mac service or cloud
service was operated. Root retains deployment and real public-path acceptance.

## Phase A: isolated public M1/M5 owner trial

The isolated authenticated UDP variant defaults to `1.31-alpha.6` / code37.
The formal build default is `1.31` / code32 for a separately built updater-only
maintenance release; formal media remains TLS/TCP and its package ID remains
`local.remoteandroid.direct`. Explicit historical experimental version properties
still override the new candidate default.

The existing saved scope indices remain 0 for LAN M1 and 1 for registered Tailnet
M1. New explicit choices are 2 for public M1 and 3 for public M5. A fresh
experimental install selects public M5. Public address fields are fixed and are
not generic routing inputs:

| Scope/node | HTTPS control | Authenticated UDP media | Certificate resource |
|---|---|---|---|
| `nps_owner` / `m1` | `146.56.249.175:49556` | `146.56.249.175:15556` | M1 existing leaf |
| `nps_owner` / `m5` | `146.56.249.175:49558` | `146.56.249.175:15558` | M5 existing leaf |

The exact selected node leaf SHA256 is checked after TLS handshake. App descriptor
validation binds node, scope, login host and control port to the fixed media tuple.
The parser repeats the media tuple check. LAN/Tailnet continue using HTTPS45560 /
UDP45963. Standalone old probe15961/15960 is unchanged. Descriptor `bind_port=0`
means the phone's ephemeral UDP socket; the host's private bind/guest target is
not selected by the App. HTTP is used only for HTTPS authentication, descriptor,
status and revocation, and media does not fall back to TCP.

Public owner trial credentials use a separate saved username key `nps_username`,
initially `wyw`; old LAN/Tailnet `username` remains unchanged. Programmatic
spinner/field restoration cannot overwrite either username. Explicit user edits
are retained; passwords are not persisted. UI states that `huoguo` friends should
continue using the stable version. Server-side owner authentication and explicit
M5 owner-trial enablement are independent controls, not bypassed by these labels.

App requests use `node`, `network_scope`, `max_size`, `video_bit_rate`, `max_fps`,
`buffer_ms`, `seconds`, `audio_enabled`, `touch_enabled` and
`surface_submit_lead_ms`. No client-selected bind/target/proxy routing fields are
sent. Existing startup-gate, clock, decoder, FEC, cancellation and runtime defaults
were not modified by this parser/profile task.

## Phase B: manual stable/test update selection

Manual `check(true)` first displays stable/test channel selection. Automatic
checks stay on the currently running package's own channel. Both use HTTPS
metadata with details and changelog, and downloading requires the user's update
confirmation. Selected package visibility is limited to the two existing package
IDs, with no broad package-query permission.

- Stable manifest: `https://146.56.249.175:15556/updates/update.json`.
- Test manifest: `https://146.56.249.175:15556/experimental/experiment.json`.
- Stable and experimental packages remain separately installed and retain their
  own preferences. Beta code37 is not compared to stable code31/32.
- Comparison uses the installed version of the selected target package. An
  absent/older other package may be installed. An already-current/newer other
  package offers `打开稳定版` or `打开测试版` instead of a downgrade attempt.
- Same-package downgrade is refused. Archive package, exact version, single
  original signer, hash, size and HTTPS URL are checked before handoff and again
  when resuming from unknown-source permission. Pending metadata includes the
  channel and target package; incomplete legacy pending metadata fails closed
  and requires a new update check.
- The former `presentUpdate(...)` method remains for the existing update-dialog
  acceptance helper. Runtime channel-picker UI/installer acceptance is separate
  from the source checks here.

APK downloads are bounded to64MiB. Metadata is bounded to64KiB. Redirects remain
HTTPS-only and bounded, GitHub uses normal public CA validation, and the existing
private update gateway uses the existing pinned certificates. No mutable raw
GitHub branch URL is introduced. Original signing key material was not read.

## Process-wide updater ownership follow-up

A read-only peer review found that the original per-Activity `busy` guard did not
serialize the package-shared `update.part`, `update.apk` and pending preferences
when Android recreates an Activity. The candidate now uses one process-wide
`UpdateOperationGate` with monotonic tokens. Expected-phase transitions and
releases must own that token, so an old worker or dismiss callback cannot unlock
a later operation. The gate monitor performs no I/O, Android calls or waits.

The lease spans chooser, metadata, details, download, archive verification,
unknown-source permission and installer handoff. Resume can transfer a token only
from permission or installer states; live network/file verification cannot be
adopted by a recreated Activity. Another still-live Activity cannot adopt the
current owner's handoff. `AppUpdater.close()` cancels its owner and releases only
chooser/details immediately; active I/O holds the lease until that worker exits,
so a newly created updater cannot race its files. Root must wire this hook from
`MainActivity.onDestroy()` in both builds. Pending metadata `.commit()` failure
stops before installer handoff. The existing APK digest/package/signer validation
still executes again after a permitted resume transfer.

This is a process concurrency fix, not an Android installer lifecycle acceptance
claim. Pure fixtures exercise actual token ownership, stale worker release,
permission/installer transfer, non-adoptable active phases and 16 concurrent
acquirers. Real Activity recreation and install confirmation remain device tests.

## Validation performed

46 relevant owned unittest checks passed: new public App contract5, old LAN
contract1, actual App/standalone parser2, new update-policy/source checks8,
existing download-delivery12, existing experimental-release11 and process-operation gate7. The new updater
check compiles the actual AppUpdater and policy against the already installed
API37 `android.jar` with small compile-only application stubs; it is not an APK
build or Android runtime test. Pure fixtures execute the actual contract and
policy, including cross-package version comparison, fixed routing/node pins,
legacy saved preferences, downgrade rejection and archive identity boundaries.
`git diff --check` passed.

Root's next layer is actual stable/experimental assemble and lint, original
signer APK identity verification, then real OnePlus15 public M5 UDP authentication,
media, touch, cancellation and reconnect. Public NPS delivery, displayed FPS,
physical latency, audio timing and friend isolation are not established by this
source checkpoint. The old stable1.30 does not have the new chooser until a new
stable APK is installed.
