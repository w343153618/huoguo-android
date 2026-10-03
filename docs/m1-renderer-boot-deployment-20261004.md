# M1 HWUI boot persistence deployed, 2026-10-04

The owner-only M1 guest now boots with HWUI `skiavk` rather than reverting to
`skiagl`. The actual protected cold boot passed. This fixes the startup
configuration's persistence; it does not yet accept the source App's actual
Vulkan pipeline, upright capture layout or an FPS improvement. See the
[derived numeric receipt](m1-renderer-boot-deployment-20261004.json).

Only the existing M1 emulator LaunchAgent's arguments gained this pair:

```text
-append-userspace-opt
androidboot.debug.hwui.renderer=skiavk
```

The installed plist changed from SHA1bf65202b9922c7008370b790b1e350062ea8d56a9ad10bc2636a06d958f1287
to88272ea90c90f2a67bfe3fae460550cc978ec604f5e94e0681f2da1cd03c9674.
Its other semantic fields are unchanged. A fresh restricted backup contains
the original plist, unchanged AVD configuration and candidate plist; its exact
location remains in the private checkpoint. AVD SHA
d2ffdd200b96e51102119087430ff31e881c196606b001f59e904632b1124d2e
remained byte-identical.

## Actual protected deployment

Root and independent review each ran38 inert fixtures against frozen private
script e6ad144b5beb7be162b848eb68d5692563ca9a8ed29f4b9c44d147fadb571df6.
Independent review first closed actual rollback ownership/absent-job exit
and unknown `ps` output classification gaps. Execution then performed one
maintenance, with existing huoguo authentication retained only in memory,
trusted original HTTPS certificate, continuous UDP45965 reservation, exact
live registry503 witness, original identity/four media-role-zero checks and
two formal TCP zero observations before stopping the exact M1 emulator job.
Formal TCP observation remains explicitly non-atomic.

Actual execution completed in18.182 seconds, exit0. All88 owned local clients
were reaped, with0 timeout terminations. The original emulator65956 exited
and exact new instance48576/new boot was registered. After-state was:

| Field | Actual before | Actual after |
| --- | --- | --- |
| Boot HWUI / runtime HWUI | skiagl / skiagl | skiavk / skiavk |
| RenderEngine | skiaglthreaded | skiaglthreaded |
| Guest memory | 8123368 KiB | 8123368 KiB |
| CPU cores | 6 | 6 |
| Physical screen / density | 1080×1920 /480 | 1080×1920 /480 |
| Actual display refresh | 30.00 Hz | 30.00 Hz |

Original owner gateway identity, source/runtime manifest and all four media
roles were verified again. Reservation was explicitly released only after
confirmed protected quiescence. M5, NPS, gateway and phone received no signal
or operation; guest data/root/resources were preserved. No source App launch
or force-stop was performed by this maintenance.

The270-second value is an operation budget, not a guarantee that filesystem
syscalls obey an absolute wall-time bound. No failure or rollback occurred in
this actual execution. The reviewed unknown-rollback/socket-release boundary
remains a limitation of the unused failure path, not an actual safe-release
claim for that path. Do not repeat this cold boot or append the pair again.

## Separate source acceptance remains open

A subsequent bounded read-only renderer collector read `skiavk`, unchanged
RenderEngine, rotation0 and1080×1920/density480/30Hz on one stable boot. The
source had a fresh stable PID3470, but its actual graphics pipeline was
unavailable. Collector exit2/partial is retained, although every individual
query succeeded. A renderer property must not substitute for the App pipeline
or pixels. Old PID3553/start2521 and old content evidence are invalid after
this cold boot.

Next use a separately protected, bounded launch of the existing source App,
read fresh PID/UID/start and actual pipeline, inspect independent ADB/gRPC
captures, and perform one bounded native in-App source selection. BBB, codec/
itag, current decoded dimensions and rendition FPS require actual current
readback; desired URL/menu text and historical299 are insufficient. Public/
phone alpha8/code39 and stable1.31/code32 remain unchanged.
