# M1 source renderer restoration and next measurement boundary

The alpha8 follow-up found two distinct problems to address before collecting another phone performance sample: the source player was running the OpenGL HWUI pipeline after a cold boot, and the public video currently playing was a LIVE animation rather than the fixed Big Buck Bunny source. The earlier 135-second public UDP observation remains unchanged; it is not retroactively validated by these new observations.

## Scoped actual restoration

At `2026-10-03T10:51:02Z`, repeated M1 idle checks found no established formal media connection, owner media listener or scrcpy server. A restricted before-state receipt was saved, SHA-256 `6bf4682d90e81a7079629904469f3d3bc695b1ac258b74f0e1325ea6905365f0`. Only the M1 source HWUI target was restored from `skiagl` to `skiavk`; the already-running source process still reported OpenGL after the property change. After another idle check, only the known source package `app.morphe.android.youtube` was stopped and relaunched. Its PID changed from 3391 to 5212, and its actual `dumpsys gfxinfo` pipeline changed to `Skia (Vulkan)`.

The official RenderEngine property stayed `skiaglthreaded`; physical guest mode stayed 1080×1920, density480 and 30.00Hz. The guest, gateways, NPS, M5, phone refresh rate, CPU limits, data and account settings were not restarted or changed by this source-only restoration. Idle checks are repeated observations, not an atomic cross-process admission lock.

The after-state read-only probe completed at `10:55:03Z`, with a stable source PID, all bounded queries successful and no property/pipeline mismatch. See [numeric after-state](source-renderer-after-restore-readback-20261003.json). No automatic boot-persistence repair has been installed yet.

## Layout and actual video format

Fresh ADB PNG and authenticated emulator-gRPC RGBA captures both showed an upright source home page after restoration. Their private PNG hashes are `c26b4980df476348bf073f8011c61ad447811ea4f970ac45a2939d8fbe6154be` and `580699d297a194355a9de3e86ba7f967701b3c4b26b28eb4a559fb18fc742f8c`; raw images are not committed. Both capture paths can share Android compositor/driver state, and the after page differs from the earlier LIVE page. This is neither a controlled same-content A/B nor physical/optical proof of a repaired display.

The public BBB video `aqz-KE-bpKQ` was then opened in the source player. Its visible Stats for nerds independently confirmed itag299, `avc1`, 1920×1080@60 and audio itag251/Opus; quality was 1080p60 at speed1.0. These are content-format values: physical guest refresh remains30Hz. Whitelisted fields and the private screenshot hash are in [the current format readback](source-BBB-format-readback-20261003.json). The diagnostic overlay was subsequently closed. A future sample must seek a fixed motion section; the observed near-credit position is not a suitable comparison window.

No phone media/FPS retest has occurred after this repair. It would be incorrect to claim that renderer drift caused every 265ms gap, that Vulkan alone fixes the prior FPS, or that this validates cellular/remote V50 performance. The next run is a bounded same-alpha8 public UDP30 window with source SF, host AU supply, phone receive and independent phone SF, keeping the limited phone CPU,120Hz and80ms settings unchanged.

## Candidate diagnostics and test safety

Inbox epoch metadata is now exported as bounded numeric event columns by source commit `3ac6774`. A local alpha9/code40 release build and lint succeeded; SHA `8ccd480c7bb1b6cd9f3d57ceac8efca37b6b16b2ff1aaa547426ad117754a78f`, 7,240,254bytes, original signer `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`. JNI remains `981f13d279ffc20232816c1e646256189cf35e02181353e9eaba89041832cd03`. This candidate is not installed, published or substituted into the alpha8 manifest. Diagnostic collection remains off by default; the new report fields cannot be attributed to the already-published alpha8 APK.

Inspection also identified an unsafe test assumption: default Android instrumentation can restart the target process before a helper checks whether its UI has an active session. A separately installed permissionless/headless fixture confirmed that default behavior on the real phone; see [lifecycle result](headless-instrumentation-lifecycle-phone-20261003.json). `--no-restart` retained that fixture's process and static nonce, but does not prove that creating another real MainActivity is harmless: MainActivity `onStop` cancels its active UDP entry. The media helper therefore must retain an absent-target-PID guard rather than treating a post-launch busy check or this fixture as permission to interrupt a live App.
