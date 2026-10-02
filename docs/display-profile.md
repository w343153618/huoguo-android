# Opted-in physical display profiles

`display_profile.apply_540_profile(adb, *, environ=None)` retains its original
public name and signature for existing callers. It supports three explicit
`DIRECT_PHYSICAL_DISPLAY` values:

| Environment value | Required physical size | Required physical density |
| --- | --- | --- |
| `540x1200` | 540 × 1200 | 210 dpi |
| `720x1280` | 720 × 1280 | 320 dpi |
| `1080x1920` | 1080 × 1920 | 480 dpi |

The helper checks Android boot completion and reads `wm size` / `wm density`.
It requires the physical geometry above. A logical override cannot stand in for
that physical mode; a conflicting size or density override raises
`PhysicalDisplayProfileError` before any overlay change. The helper never writes
`wm` size/density, changes AVD configuration, reboots Android, or alters emulator
CPU/RAM settings. Configuring and cold-booting an AVD is a separate deployment
action requiring subsequent actual geometry readback.

After verification, it attempts to disable only the two existing Pixel 6
emulation overlays, reads both display-cutout resources, and records status-bar
heights. Decoration failures return a warning so remote access remains usable.
Attempts are cached by kernel boot ID and selected profile; changing the profile
cannot bypass geometry verification through an earlier cached result. Other
environment values skip this adjustment. The gateway's startup preparation
recognizes each supported value, while its existing connection-time calls
continue through the same compatibility entry point.

Offline regression checks:

```sh
python3 -m unittest discover -s tests -p 'test_display_profile.py'
python3 -m unittest discover -s tests -p 'test_hardware_gateway.py'
```

These checks verify the helper's behavior and preserve existing gateway session
handling. They do not confirm deployment, actual AVD geometry, root access,
streaming performance, or real-phone presentation.

The M1 test AVD was cold-booted at 1080×1920 / 480 dpi on 2026-10-01 at the
user's request. CPU/RAM remain 6 cores / 16 GiB. The 720p and 1080p density
choices both retain a nominal 360×640 dp layout. This is a persistent physical
configuration, separate from the client's selected encoding size; see
[verified configuration](evidence/pre-latch-20261001/m1-1080-config-change.json).
