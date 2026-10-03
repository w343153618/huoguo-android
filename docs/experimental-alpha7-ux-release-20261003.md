# Alpha7 saved password and exit confirmation — 2026-10-03

Published test package1.31-alpha.7/code38 uses the original signer and an
independent package from stable1.31/code32. Its immutable source tag is
3387b56f1e6ef2a5530c9f9dcda1663aebfbc349. APK SHA256 is
1f4c6c1b7dc61b2de9dd24dfdc647ee6424d347ad1cde143db20237c6b57f2a7,
size8,347,265 bytes; native library remains
981f13d279ffc20232816c1e646256189cf35e02181353e9eaba89041832cd03.

[GitHub release](https://github.com/w343153618/huoguo-android/releases/tag/experimental-v1.31-alpha.7)
and all three uploaded assets were downloaded and compared byte-for-byte with
the frozen originals; the remote tag matched the exact source. The pinned NPS
mirror's entire metadata and APK body were independently read through physical
M1en7 and matched version, size and SHA. The APK was placed first and experimental
manifest atomically committed afterward. Stable manifest and media stayed intact.

## Product changes

- Explicit Save Password and Clear Saved Password buttons use the existing
  AndroidKeyStore AES256-GCM store, with full endpoint and username in AAD. No
  plaintext password is written to normal preferences or reports. Currently one
  endpoint/account pair is saved; other endpoints do not receive its password.
- Missing/new username defaults to huoguo. Existing valid username choices,
  including wyw, remain preserved rather than guessing whether they were typed.
- Back during a UDP connection opens Continue/Exit confirmation. A single
  generation/object token protects cancellation; repeated Back does not stack
  dialogs, and a stale dialog cannot cancel a later session.
- Original avatar pixels remain; beta has a purple test badge, a separate theme,
  short title and login-only system-bar Insets. Formal resources and streaming
  touch layout remain unchanged.
- Public HTTPS and UDP sockets share one non-VPN Android Network lease. This is
  API binding evidence only; actual VPN-on bypass and domestic packet routing
  are not established by the binding counters.

## Actual OnePlus12 owner UI check

The connected, CPU-limited OnePlus12 ran the exact signed artifact. An existing
huoguo account used normal pinned HTTPS49556 authentication and public UDP15556
media; the friend account's secret was neither recreated nor changed. This was
the owner-authorized nonisolated M1 experiment, same-home Wi-Fi, VPN off.

The matching helper clicked the actual Save button, finished/reopened the
Activity, compared restored fields in memory, clicked Clear, reopened to verify
empty, then saved again and reopened to verify persistence. Every check was true;
only booleans were exported. It then opened the actual Back dialog, repeated Back
and confirmed the same dialog, clicked Continue and observed continued media,
then clicked Exit and observed cancellation of the captured attempt. New login
received media, and the same exit sequence passed again. Both final callbacks
were independently awaited rather than treating performClick as synchronous.

The earlier pre-fix helper reported failure before AlertDialog's posted listener
executed. Its password operation booleans were already true. This was an early
assertion in instrumentation, not a finding that the password store failed.
That report and unpublished earlier APK identity remain separate. See
alpha7-posted-exit-callback-and-insets-20261003.md for the bounded callback fix.

The successful round's independent SF cadence was29.639FPS. Existing guest
content/format/position was not independently pinned in this UX round; a generic
wrapper BBB label does not establish60FPS source content. This is **UI and
authenticated media/reconnect acceptance**, not a controlled FPS improvement,
cellular/V50, physical touch or acoustic lip-sync claim.

M5's existing huoguo authentication was separately accepted through its pinned
public control49558 over physical M1en7; an intentionally unknown session returned
404. No M5 media session was created for that check. Prior alpha6 public-M5 phone
media evidence remains distinct. Both supervised owner gateways had correct-node
pinned local ping200; formal gateway PIDs remained unchanged, no NPS restart or
account file change occurred. Friend host/LAN isolation is still not accepted.

Full source regression:1,242 checks passed, Gradle debug assembly and lint passed,
and matching instrumentation was signed and built. Helper and temporary phone
APKs/one-use login input were removed afterward; stable/beta data and the final
encrypted saved owner credential remain. Restricted evidence is under
docs/evidence/alpha7-ux-public-20261003/; APKs and raw logs are outside Git.
