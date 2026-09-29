# Android 17 root / Xposed-compatible framework deployment

Validated on Apple M1 Max and M5 Max on 2026-09-29.

## Verified stack

- Google official SDK package `system-images;android-37.0;google_apis;arm64-v8a`, revision 6, stable channel. Guest reports Android 17 / API 37 / REL / preview SDK 0.
- Stock emulator kernel `6.12.58-android16-6-gccafb60de224-ab14828483-4k`, 4 KB pages. Kernel branch name is not the Android userspace version.
- KernelSU 3.3.0 (32601), LKM loaded early through a separate patched ramdisk. Stock SDK kernel and ramdisk are preserved.
- Zygisk Next 1.5.0 (843) and Vector 2.2 (3080), libxposed API 102. Vector is JingMatrix's maintained Xposed-compatible successor/fork; this is not the archived LSPosed 1.9.2 build.

Official releases:
- https://github.com/tiann/KernelSU/releases/tag/v3.3.0
- https://github.com/LSPosed/ZygiskNext/releases/tag/1.5.0
- https://github.com/JingMatrix/Vector/releases/tag/v2.2

Module SHA-256:
- Zygisk Next: `474d58abc208c0779e7f8f1d8449db755a874d475c13b9cf8decf74bc171933b`
- Vector release: `9ee8323575d615f7b3f1076ff60b2a63a49390ef11881b52632311a37f6f79cc`

## Acceptance

A non-root Android test App was granted root in KernelSU and ran `/system/bin/su -c id`: original UID 10232, exit 0, child UID 0, SELinux context `u:r:ksu:s0`.
A separate Xposed module was scoped only to a custom test App. Its method returned `UNHOOKED` before enablement and `HOOK_OK_ANDROID17` afterward. Both checks passed again after a fully cold emulator start on each Mac. The Vector manager showed Active, version 2.2 / API 102. This validates the root/framework mechanism, not compatibility of every third-party module.

## Gateway configuration

`DIRECT_AVD` selects the migrated AVD. `DIRECT_RAMDISK` provides the separate matching KernelSU ramdisk. With `DIRECT_EXTERNAL_VM=1`, the gateway waits for a separately supervised emulator instead of starting another copy.
The M5 emulator is supervised by a user LaunchAgent, starts headless with host GPU and without snapshots, and uses caffeinate while running. LAN bindings, TLS certificate, credentials, NPS forwarding and phone client are retained. Existing 540p/720p/1080p streaming presets remain available. Real LAN and public NPS TLS checks received H.264 at 720x1600 and AAC; a test App received touch DOWN/MOVE/UP events and HOME returned to the launcher. Unauthenticated and wrong-password requests returned 401. Physical-phone mobile-network playback and frame-rate/latency acceptance remain separate checks.

## Data and recovery

The original Android 16 AVD and SDK image are retained. A clone was used for the upgrade experiment. This emulator image did not directly inherit the original application data disk, so original APKs and per-app directories were exported from a read-only Android 16 boot and restored into Android 17 with new ownership and SELinux labels. Keystore-backed login state is not guaranteed to migrate across this path.
The installation includes a rollback script and original gateway/plist backups on the host. Do not point the original Android 16 instance at a data disk already upgraded by Android 17.

Never commit host credentials, private keys, virtual-machine data disks, app data or SDK images to this repository.
