# Offline code40 owner helper build

The existing saved-UI instrumentation helper compiles successfully against the
frozen alpha9/code40 Java ABI. This is preparation for a later phone test; no App
or helper was installed or published, and no device, emulator, source player,
M1/M5 gateway or NPS service was operated. Actual commands, durations, class
hashes and signature readbacks are recorded in
[the build receipt](code40-owner-helper-build-20261003.json).

## Frozen source and artifacts

The private checkout is
`/private/tmp/huoguo-code40-owner-helper-fj0bbkqg/source`, detached at
`1df2ecd3cb28fabad69006c5210485e28309db45`. Initial and final tracked diffs are
empty. The existing helper source is unchanged from
`a83b6685dedb98a3248e0503091ea0ba94c7b1ff`, SHA256
`2e652e78c9f65c5b2f70cb170e070c5534f89b4e46abc434a7908c0f7d8a6ee2`.
No encrypted password store, user data, account, media key or signing key was
copied into the checkout. The builder accesses the existing restricted signing
location; it does not copy that key into source or evidence.

With the existing OpenJDK21 and Android SDK, this exact offline command completed
in12.770seconds:

```sh
./gradlew --offline --no-daemon --max-workers=2 \
  :app:compileDebugJavaWithJavac \
  -PauthenticatedLanUdp=true \
  -PprobeApplicationId=local.remoteandroid.direct.experiment \
  -PexperimentalVersionName=1.31-alpha.9 \
  -PexperimentalVersionCode=40
```

The generated BuildConfig verifies the experimental package, UDP flag,
alpha9/versionCode40. The106 compiled classes have aggregate SHA256
`b604f02c6c19071fd433c9aeb57047960c9b736a55a5f6e06e3a2fa5f7a00e3f`.
The aggregate is the SHA256 of sorted relative UTF8 class paths, a NUL separator,
and each binary file SHA256 digest. These are debug classes for helper compile
ABI, not byte identity with the candidate release DEX. The receipt separately
hashes `UdpVideoProbe$CompletionReceipt` and the updated `AppListener` interface.

The unchanged builder then completed in5.799seconds:

```sh
python3 scripts/probes/build_authenticated_lan_helpers.py \
  --output /private/tmp/huoguo-code40-owner-helper-fj0bbkqg/helpers
```

The UI helper is
`/private/tmp/huoguo-code40-owner-helper-fj0bbkqg/helpers/ui/helper.apk`,
61,843bytes, SHA256
`474434fbcf1534c895d02ec2529b04ab171846c0d0fdf939187c90b82cbe54af`.
Existing SDK `apksigner verify --verbose --print-certs` returned0 with original
certificate SHA256
`0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`.
Saved-mode contract names are present in the DEX. It contains no JNI library.
The builder also produced its usual V50 and touch-receipt helpers, without
installation; the UI artifact above is the dependency for normal saved-UI tests.

## JNI and exact local candidate boundary

The existing frozen local release APK was independently re-read this round:
SHA256`030126da900e16a11fb46dccbd1ed54edd5e0a7757a42503ca0ae40047c3d570`,
7,240,254bytes, JNI SHA256
`981f13d279ffc20232816c1e646256189cf35e02181353e9eaba89041832cd03`.
Its signature also verifies with the same original certificate. It is still a
local candidate, not the installed alpha8 phone artifact.

The new temporary clone's native build produced JNI SHA256
`b0a8cd239af2c682a968c33660b433f3157f15eca552848f83cdd076dc07696f`,
which differs from the candidate's complete JNI hash. Read-only ELF comparison
finds differences only in `.debug_info`, `.debug_line`, `.debug_str`, `.symtab`
and `.note.gnu.build-id`; all remaining sections, including executable and
allocated data sections, match individually. This is consistent with build and
debug metadata varying across source/build locations. It is not proof of full
binary reproducibility. The receipt keeps both hashes and the exact section
comparison; the fresh JNI is not substituted for the original candidate.
The helper only references Java compile ABI and does not carry this JNI.

## Acceptance gaps and smallest follow-on

The existing helper can exercise normal Start, accepted-report readback, exit
confirmation, bounded audio-thread liveness and normal reconnect. This build
has not run those flows on Android. Before any use, root must re-read the actual
installed target version/hash and signature, use the absent-target-PID and
host-session gates, and preserve the phone's CPU limits, data and other App.
The saved-UI contract in
[owner-saved-ui-helper-contract-20261003.md](owner-saved-ui-helper-contract-20261003.md)
continues to apply; no credential fallback or force-stop is permitted.

The unchanged `verifySurfaceReadback` demands fields from a complete accepted
performance report. Code40 deliberately stores only `completion_receipt` when
statistics are rejected. Consequently this helper will fail that readback on
the rejection path and cannot certify report-rejection/reconnect. It also does
not observe the actual typed callback receipt; thread liveness alone does not
certify AudioTrack/codec resource release.

A minimal follow-on could add a separate owner-only, default-off completion
observer that copies the actual Probe-generated receipt's numeric fields before
delegating to the existing callback. Correlate it with the captured attempt and
generation, keep it bounded, and never reconstruct cleanup or expiry from stats.
For rejected reports the helper should validate the closed receipt schema,
confirmed cleanup and idle `current`/`retiring` state, then use the ordinary Start
UI for a fresh authenticated session. It should skip performance-field
verification for an explicitly rejected report, without weakening accepted
report verification.

A report-limit fault test should be a separately labeled, explicit owner opt-in:
after the real media runner closes owned audio and obtains its actual cleanup
state, but before the existing `numericAppSummary` check, add a bounded fixed
numeric subtree large enough to trigger the real64KiB rejection. Keep the
original cleanup, receipt construction, listener and `finished` path intact;
clear the hook after one owned completion. This would validate Android UI
retirement following real media plus injected report failure. It would not prove
natural reports exceed64KiB or measure ordinary media performance. Directly
fabricating a receipt or calling `finished` is only a component fixture and must
not be described as full runner acceptance. No such observer or injection was
implemented in this task.

No real phone, MTK/V50, public network, optical/acoustic delay or one-hour soak
claim follows from this offline build.
