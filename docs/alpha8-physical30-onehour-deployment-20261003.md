# Alpha8 physical30Hz and one-hour public owner deployment

Deployment checkpoint supplied by the root task on 2026-10-03. This document
records the completed bounded maintenance and its acceptance limits. The
documenting task did not operate devices or services, and does not change
`docs/current-testbed.json`.

## Completed guest display change

| Field | M1 | M5 |
| --- | --- | --- |
| Environment | original UID501, owner nonisolated | existing M5 guest retained |
| Physical pixels / density | 1080×1920 / 480 | 720×1280 / 320 |
| CPU / RAM | 6 cores / 16GiB | 8 cores / 8GiB |
| AVD `hw.lcd.vsync` | 30 | 30 |
| Original LaunchAgent flag added | `-vsync-rate 30` | `-vsync-rate 30` |
| After cold boot: active physical display mode | 30.00Hz | 30.00Hz |
| System min / peak refresh policy | 30 / 30 | 30 / 30 |

The actual cold-boot `cmd display` mode readbacks establish physical30Hz;
this is not merely the earlier M1 render30/physical120 state. Resolution,
density, guest data, root image and resource settings were retained. Phone
global refresh settings were not part of this guest display change. The
read-only rationale and rollback plan are in
[guest-physical30hz-readonly-plan-20261003.md](guest-physical30hz-readonly-plan-20261003.md).

## Public owner session policy and retained services

Both public owner gateways use source frozen at
`dd43a39f49f6dceb55854fbc6e43367d34f381c3`:

| Field | M1 | M5 |
| --- | --- | --- |
| Experimental owner gateway PID at this checkpoint | 26875 | 55681 |
| Gateway process max-runtime | 0, proactive process TTL disabled | same |
| Public NPS maximum session duration | 3600 seconds | 3600 seconds |
| Formal gateway PID retained | 41650 | 75248 |

The one-hour limit is specific to **public NPS owner sessions**. Independent
LAN/Tailnet sessions still retain their120-second limit. A process max-runtime
of0 does not disable session leases, authentication, anti-replay, cancellation
or cleanup. The change is not a one-hour real-phone acceptance result.

Formal NPS PID3412973 was retained without a restart. Formal QUIC/NPC
identities and their routes were retained. This guest/gateway maintenance did
not authorize a further NPS restart or change unrelated connected NPCs.

## Maintenance and exceptional cleanup

Fresh private backups were made in the restricted runtime directory named
`alpha8-onehour-physical30-20261003`. Backup contents and credentials are not
included in Git or this document.

A short temporary admission pause covered new TCP SYNs on the four selected
maintenance ports; those temporary rules were removed after maintenance.
UDP/NPC admission was not changed. The launchd bootstrap step returned a
transient status5 while asynchronous teardown was completing; a bounded retry
subsequently succeeded. This is recorded as a transient retry, not a clean
first-attempt bootstrap.

Old M1 owner PID67034 required exceptional cleanup. It was identified as the
exact orphan PID with PPID1, a `session_closed` log and no remaining listener.
TERM did not end it, so KILL was sent to that exact PID. Its exit must **not**
be described as natural termination or successful graceful cancellation. No
claim is made that this exception validates the general shutdown path.

The complete firewall hash cannot be called byte-identical: counters and
provider-managed chains drifted. A separate readback of the relevant domestic
`geo_in` / `geo_fwd` rules is pending at this checkpoint. Preserved NPS PID and
the absence of an intentional domestic-filter change do not replace that
independent filter verification.

## Actual phone acceptance available at this checkpoint

M5 was tested with the latest alpha8 artifact whose recorded SHA begins
`a663c4`. This abbreviated identifier is not a replacement for the frozen
artifact's full SHA in its build evidence.

The real-phone helper completed a20-second **same-home Wi-Fi, public NPS**
media session plus exit and reauthentication/reconnect. It read back
540×960,30FPS and80ms. The independent SurfaceFlinger observation reported
29.999FPS cadence and a49.735ms maximum observed gap. The bounded helper and
reconnect checks passed.

This result is not actual V50 testing, cellular or distant-network acceptance,
60FPS source-content acceptance, optical touch latency, acoustic audio/video
sync, or an hour-long stability measurement. A physical30Hz guest cannot
produce60 distinct presented source frames per second just because the client
offers a60FPS transport cap.

**M1's135-second real-phone test was still running when this checkpoint was
written. Its outcome is pending.** No M1 long-window FPS, cleanup or pass result
is inferred here. The root task should add a separate completed-test record
once its actual evidence is available, rather than silently rewriting this
pending checkpoint as historical success.

## Remaining acceptance

- Complete and record the M1 test, including actual path, media parameters,
  frame evidence, report coverage and cleanup.
- Independently read back the relevant domestic filter chains after the
  maintenance.
- Treat one-hour session support as a policy capability until a bounded
  one-hour test has actually completed.
- Preserve friend/host/LAN isolation gates; the M1 owner environment remains
  explicitly nonisolated. Same-home owner testing does not certify friend
  deployment or V50 performance.

## Completed follow-up

M1 actual public UDP UI completed135-second sampling (137.043seconds helper window), exit confirmation and reconnect with the final alpha8 APK a663c4d046f1048ae32b23875f8614039c73227a484835d6af024d71a8744f51. Independent phone SF cadence26.423FPS/full window26.310, maxgap265.225ms,78 gaps over100ms; trailing429.435ms unknown. It crossed the previous120-second cap but is not stable30 or an hour-long soak. Both current public descriptors requested3600seconds. Source BBB format was not independently reverified in this round.

Cloud read-only follow-up found all26 `geo_in`/`geo_fwd` rule lines identical, temporary owned admission rule absent and formal NPS PID3412973 present. This excludes dynamic provider chains and counters; no whole-firewall byte identity claim.

Current regression1301 checks passed unrestricted, Gradle assemble and lint passed. The restricted first attempt had local socket permission failures and is not reported as passing. Earlier unpublished e119e3 APK M5 run reported no_authenticated_media; final diagnostic APK repeated the existing-account path successfully. This is not evidence of a causal performance or connection fix.

Phone lifetime is locally measured from the first authenticated video datagram; server lease begins at authenticated READY. One-hour reason1 is a local deadline, not a server expiry notification. End-reason3 can precede it under abnormal timing/network conditions. Array details are bounded prefix samples, not full-hour sampling.
