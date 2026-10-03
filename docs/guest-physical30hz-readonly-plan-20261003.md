# M1/M5 guest physical 30 Hz: read-only findings and maintenance plan

Readback on 2026-10-03 around 09:24 UTC / 17:24 China time. This task made no
guest, AVD, LaunchAgent, phone, gateway or cloud changes and sent no stop signal.
Canonical source reference was `dd43a39f49f6dceb55854fbc6e43367d34f381c3`.

## Actual state

| Field | M1 | M5 |
| --- | --- | --- |
| Host UID / emulator PID | 501 / 41648 | 502 / 49886 |
| AVD / adb serial | RemoteAndroid17Compare / emulator-5556 | phone17-root / emulator-5554 |
| Physical pixels / density | 1080×1920 / 480 | 720×1280 / 320 |
| AVD `hw.lcd.vsync` | 120 | 120 |
| Generated hardware `hw.lcd.vsync` | 120 | 120 |
| SF active physical `vsyncRate` | 120.00 Hz | 120.00 Hz |
| SF `renderRate` at this readback | 30.00 Hz | 120.00 Hz |
| `cmd display get-active-mode 0` | mode1, 1080×1920, 120Hz | mode1, 720×1280, 120Hz |
| `cmd display get-supported-modes 0` | only physical mode1 at120Hz | only physical mode1 at120Hz |
| Current system min/peak refresh setting | 60.0 / 60.0 | 120.0 / 120.0 |
| Existing CPU / RAM | 6 / 16384MiB | 8 / 8192MiB |
| Installed official emulator | 37.1.11.0, build15917651 | same |

`dumpsys display` additionally lists divisors such as30 in
`supportedRefreshRates`, while `supportedModes` has only one physical120Hz
mode. That divisor list, a30Hz render rate, an app frame-rate override or a
30FPS capture limit does **not** establish physical30Hz. M1 demonstrates this
distinction directly in the same readback: render30, physical120.

## Official installed parameter

Both installed emulator executables expose `-help-vsync-rate`:

> `-vsync-rate 30` sets the emulated guest display VSYNC rate to30Hz.

This is local installed-tool documentation, not an assumption about a newer
emulator. The installed `emulator/lib/hardware-properties.ini` also declares
`hw.lcd.vsync` as an integer, default60. The existing running commands have no
explicit VSYNC flag; both AVD configurations currently supply120.

## Smallest reliable physical change

Use a guarded cold boot of the **same** AVD:

1. On the selected host, first stop new admissions through every gateway that
   can reserve this guest. Establish that formal and experimental sessions,
   capture workers and guest-local scrcpy have finished and cleanup is
   confirmed. Preserve admission closure across the idle check and restart;
   a one-time empty status followed by a stop signal has a race. Current
   `UdpLanSessions.begin_idle_drain()` is an in-process live-registry gate, not
   an HTTP endpoint or a shell-callable permission to stop another service.
   Importing a fresh registry in another process cannot drain a running one.
2. Make private exact backups of the selected AVD `config.ini` and its
   emulator LaunchAgent plist, with original permissions, owners and hashes.
   Record original min/peak settings and display readbacks. Do not reset
   userdata, snapshots, account state or root images.
3. Replace the single `hw.lcd.vsync=120` entry in that AVD with
   `hw.lcd.vsync=30`. Add `-vsync-rate`, `30` to the existing emulator
   LaunchAgent `ProgramArguments` before any `-qemu` arguments. Preserve every
   other argument, including M5's rooted ramdisk. The persistent config and
   explicit startup flag should agree; otherwise a later launch path can
   restore a different value. Do not hand-edit generated `hardware-qemu.ini`.
4. Perform one graceful cold restart through the already managed emulator
   job, under the admission reservation. Both jobs already use
   `-no-snapshot`; retain this so an old live display state is not reloaded.
   Do not create a second unmanaged emulator or restart NPS/NPC.
5. Once the same AVD is booted, align guest system min/peak settings to30 if
   needed for the user's fixed30Hz request, after their exact old values were
   backed up. This is a separate framework policy write and must not be
   substituted for step3. It was not performed or tested in this task.
6. Verify physical mode and existing resolution/root/guest identity before
   reopening admissions. Recheck after a real app starts, orientation changes
   and another managed cold boot; no phone global refresh setting is changed.

The external files actually read were:

```text
M1 AVD: /Users/wyw/Documents/ChatGPT/others/android-remote/avd/RemoteAndroid17Compare.avd/config.ini
M1 job: /Users/wyw/Library/LaunchAgents/local.remoteandroid.m1compare.emulator.plist
M1 emulator: /Users/wyw/Library/Android/sdk/emulator/emulator
M5 AVD: /Users/yawen/.android/avd/phone17-root.avd/config.ini
M5 job: /Users/yawen/Library/LaunchAgents/local.remoteandroid.vm17.plist
M5 emulator: /Users/yawen/Library/Android/sdk/emulator/emulator
M5 root ramdisk: /Users/yawen/Library/Application Support/AndroidRemote/android17/ramdisk-ksu.img
```

The command delta is just `-vsync-rate 30` added to each original emulator
argument array. M5's enclosing `/usr/bin/caffeinate -i` stays intact. All CPU,
RAM, disks, resolution, density, root, console/adb/gRPC ports and GPU arguments
stay intact. No change to the phone's own30/60/120Hz modes is part of this plan.

## Runtime alternative and validation boundary

`cmd display set-user-preferred-display-mode` exists, but the current physical
mode list contains only120Hz. It cannot be treated as a proven way to create a
new30Hz HWC mode. Setting min/peak30 or app render30 alone can reduce render
cadence while physical VSYNC remains120, as seen on M1. No safe validated
runtime-only physical30Hz mechanism was found in this read-only task.

After the proposed cold boot, use the selected host's existing adb and serial:

```sh
adb -s emulator-5556 shell cmd display get-active-mode 0
adb -s emulator-5556 shell cmd display get-supported-modes 0
adb -s emulator-5556 shell dumpsys SurfaceFlinger
adb -s emulator-5556 shell dumpsys display
```

For M5 use its adb executable and `emulator-5554`. Acceptance requires
`activeMode.vsyncRate` near30Hz and matching physical dimensions, a30Hz physical
mode in the active/supported-mode readbacks, and generated hardware config30.
Read `renderRate` separately. A SurfaceFlinger refresh period, if available,
should be near33,333,333ns, but alone is insufficient physical-mode evidence.
Video reception, unique content FPS, panel presentation and touch/audio delay
remain separate tests. A60FPS App cap cannot create60 unique source frames
from a guest fixed at physical30Hz; it may cap or repeat transport frames.

## Rollback

While admissions remain reserved and the guest is idle, restore the exact
private `config.ini` and LaunchAgent backups and perform the same managed cold
boot. Restore original min/peak values exactly: use deletion for an originally
absent setting, rather than inventing a default. Reconfirm physical120 and the
same display dimensions before reopening. Do not restore old userdata or
revert unrelated later source changes.

`scripts/probes/trial_physical_vsync.py` is **not** ready to apply this plan:
its parser and replacement policy accept only60/120, it assumes M1's exact
resources, and it sends `adb emu kill` after only checking a legacy
`hardware_stream.py` process. It has no complete formal/UDP admission or
cleanup protection. Do not merely widen its choice list and run it on M5.
Adaptation would require explicit selected-host identity, live admission drain,
configuration backup and the physical-mode validation above.

No physical30 cold boot, performance gain, friend/V50 acceptance or rollback
execution is claimed by this document.
