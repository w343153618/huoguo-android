# M1 user-requested 8 GiB guest RAM deployment

The user explicitly changed the M1 guest requirement from16GiB to8GiB.
Root completed this bounded maintenance on2026-10-03. Only the existing
`RemoteAndroid17Compare` AVD `hw.ramSize` changed from16384 to8192.
The existing emulator LaunchAgent has no RAM override and remains byte-identical.
A restricted host backup retains the original config and job plist; guest disks,
account data, root image and the phone were not changed.

Before the cold boot, root used the existing authorized account and pinned local
HTTPS certificate for the original UDP registry's exact idle witness, held the
exclusive UDP45965 reservation through maintenance, and checked formal TCP
connections twice. The legacy TCP checks are not an atomic admission gate.
No original gateway or NPS signal was sent. The authorized restart addressed
only `local.remoteandroid.m1compare.emulator`, via launchd bootout/bootstrap.
M5 and unrelated NPCs were not operated.

Actual readback after cold boot:

| Field | Result |
| --- | --- |
| Configured RAM | 8192MiB |
| Guest MemTotal | 8,123,368KiB, about7.75GiB after kernel reservation |
| vCPU count | 6 |
| Guest boot completed | 1 |
| Physical display | 1080×1920, density480, actual30.00Hz |
| New emulator job PID | 65956 at acceptance |
| Original owner gateway | PID26875, source/runtime hashes retained |
| Owned media roles after maintenance | 0 |

The first local assertion incorrectly looked for `30.00Hz` without the space
in the actual `30.00 Hz` display readback. This caused a validation refusal
after the successful cold boot. A separate read-only numeric regex acceptance
confirmed memory, CPUs, boot, display, exact config-only diff and original
service identity. The VM was not restarted again to repair the assertion.
The [actual receipt](m1-ram8-deployment-20261003.json) records that boundary.

This confirms RAM configuration and actual guest allocation, not a performance
improvement. Older16GiB test evidence remains historical. The previous player
PID/start and guest clock identity are invalid after reboot and must be freshly
observed before another experiment. The published App and phone settings remain
unchanged; no new trace or FPS measurement occurred during this maintenance.
