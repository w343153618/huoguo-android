# M1 renderer boot-persistence review

The historical source-only restoration did not install boot persistence. The
RAM8 cold boot returned to the emulator's generated HWUI boot value `skiagl`;
this is configuration ordering, not evidence that RAM8 causes a graphics bug.
The current compressed/flipped capture is consistent with earlier OpenGL
layout observations, but no current same-content renderer A/B was performed.

## Fresh read-only evidence

The existing renderer preflight completed in 559.647084 ms on the explicitly
authorized `emulator-5556`, with all queries successful, a stable PID 3553 and
the same boot before/after. It read HWUI `skiagl`, actual source pipeline
`Skia (OpenGL)`, RenderEngine `skiaglthreaded`, physical 1080×1920, density480,
rotation0 and actual30Hz. No property, source process or device was changed.

Additional bounded reads completed in 62.820833 ms and 54.090167 ms, with all
owned local children reaped and each dump below1MiB. They independently read:

- `debug.hwui.renderer` context: `u:object_r:debug_prop:s0`.
- `ro.boot.debug.hwui.renderer`: `skiagl`.
- Installed `/vendor/etc/init/hw/init.ranchu.rc` uses the boot renderer value,
  with `skiagl` as its fallback, to initialize `debug.hwui.renderer`.
- The installed vendor script independently initializes RenderEngine using
  its own boot property and `skiaglthreaded` fallback.

The actual M1 emulator LaunchAgent directly runs the emulator with the existing
AVD, port5556, host GPU, no-snapshot and vsync30 arguments. It has no HWUI
boot override and no renderer environment. Its before-state SHA-256 is
`1bf65202b9922c7008370b790b1e350062ea8d56a9ad10bc2636a06d958f1287`.

The separate formal gateway job does have `DIRECT_HWUI_RENDERER=skiavk` and
`DIRECT_REFRESH_RATE=60`. Canonical [performance_profile.py](../performance_profile.py)
lines9–38 applies these after a boot ID read when invoked, memoizing success
per boot/signature. [gateway.py](../gateway.py) invokes it at `ensure_android`
entry and in a one-time startup preparation thread when a physical profile is
configured. That long-running gateway does not continuously observe independent
external-emulator cold boots. Independent UDP experiments bypass this formal
entry. Even a later property application cannot prove an already initialized
source process changed its actual HWUI pipeline.

This explains why a persisted gateway environment and the historical runtime
`setprop` are insufficient to establish renderer persistence at source startup.
Do not inherit the gateway's60 preference or invoke its full physical-profile
preparation solely to repair HWUI.

## Minimal existing boot-contract candidate

Append only this argument pair to the existing M1 emulator job:

```text
-append-userspace-opt
androidboot.debug.hwui.renderer=skiavk
```

The installed emulator is37.1.11.0/build15917651; its `-help-all` and
`-help-append_userspace_opt` expose this option. The current upstream
boot-property implementation appends these options
after generated defaults and keeps the last value for each key; it separately
generates HWUI and RenderEngine keys. This makes a single HWUI boot override a
supported implementation candidate without introducing a guest hook. The
reviewed upstream blob is `0c2278e4498bb815549ed15e1bcc0dff9d3cca80`, not a
byte-for-byte match to the installed executable. [Upstream userspace boot
properties](https://android.googlesource.com/platform/external/qemu/+/emu-master-dev/android/android-emu/android/userspace-boot-properties.cpp).

The installed vendor-init readback provides the guest side of that contract:
the boot HWUI value is consumed before normal App drawing. The upstream
Goldfish init also documents this independent HWUI/RenderEngine initialization.
[Goldfish init](https://android.googlesource.com/device/generic/goldfish/+/f9826571438a02661707c4a847535d1963c518be/init.ranchu.rc).

Do not substitute `-systemui-renderer skiavk`: the upstream implementation
sets both HWUI and RenderEngine from that option, broadening the change.
Do not use a blind `-prop debug.hwui.renderer=skiavk`: official documentation
requires a `qemu_prop` property label for that option, whereas the actual guest
HWUI property is `debug_prop`. The local generic help omits this restriction.
[Official emulator command-line contract](https://developer.android.com/studio/run/emulator-commandline#common).

No boot override has been deployed or reboot-validated by this review. The
candidate preserves GPU, RenderEngine, resolution, RAM8,6cores,30Hz, root
ramdisk, data, snapshot policy and existing service identities. Its acceptance
must be a separate protected renderer maintenance, not a mixed-variable media
experiment or an asserted FPS repair.

## Scoped acceptance

1. Preserve a restricted before-state backup of the M1 job/config and verify
   formal/owner session protection with the existing continuous reservation,
   live registry witness and role scans. Do not operate M5 or formal NPS.
2. Verify that the only intended job change is the argument pair. Reloading or
   cold-booting the M1 emulator must happen only in the protected maintenance
   window; editing a disk plist alone does not update an already cached job.
3. After that cold boot, read actual `ro.boot.debug.hwui.renderer=skiavk`,
   `debug.hwui.renderer=skiavk` and unchanged RenderEngine/physical resources.
   A newly initialized source PID must independently show `Skia (Vulkan)`.
4. Check upright layout through both fresh ADB and authenticated gRPC captures
   before attempting native in-App source selection. Capture-path agreement
   alone is not a same-content visual A/B or physical/optical proof.
5. If startup or readback fails, restore only the backed-up M1 job arguments in
   a protected window and retain the failed candidate evidence. Do not broaden
   changes to RenderEngine, system images or cloud paths.

For immediate layout verification without a guest cold boot, the existing
renderer-only profile function can be used with an explicit environment
`{'DIRECT_HWUI_RENDERER': 'skiavk'}` in a separate protected source-maintenance
operation. It only sets/readbacks HWUI, but an existing OpenGL source process
still requires a permitted source-only restart and actual pipeline readback.
That immediate operation does not constitute boot-persistence acceptance.

This review wrote only this document. It did not edit startup arguments, write
guest properties, restart/stop source or guest, touch a UI, start media, or alter
phone, M5, NPS, accounts or original gateway state. It establishes no new FPS,
layout-repair acceptance or capture30→submit19 causality.
