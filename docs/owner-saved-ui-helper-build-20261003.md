# Frozen alpha8 saved-UI helper build

The independent offline build succeeded. The helper implements the contract in
[owner-saved-ui-helper-contract-20261003.md](owner-saved-ui-helper-contract-20261003.md).
Actual commands, durations, selected class hashes and signature readback are in
[the build receipt](owner-saved-ui-helper-build-20261003.json).

Only the local frozen checkout
`/private/tmp/huoguo-gold39-saved-ui-i2pjvea3/source` was used. Its App source is
artifact commit `73d196a8193394c9362250aa0e8be92fae15e125`; the sole tracked
modification is `experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java`
copied from `a83b6685dedb98a3248e0503091ea0ba94c7b1ff`. That helper source SHA256
is `2e652e78c9f65c5b2f70cb170e070c5534f89b4e46abc434a7908c0f7d8a6ee2`.
No App source, password store or user data was copied or modified. No credentials
were copied into this clone. Existing native source pins and the local SDK/JDK
were used; no new tool or dependency was downloaded.

From that checkout, with existing OpenJDK21 and Android SDK configured, the exact
classpath build was:

```sh
./gradlew --offline --no-daemon --max-workers=2 \
  :app:compileDebugJavaWithJavac \
  -PauthenticatedLanUdp=true \
  -PprobeApplicationId=local.remoteandroid.direct.experiment \
  -PexperimentalVersionName=1.31-alpha.8 \
  -PexperimentalVersionCode=39
```

It completed in12.518seconds. Generated BuildConfig independently confirms the
isolated package, experimental UDP flag, alpha8 versionName and code39. The104
compiled class files have aggregate SHA256
`2861eca18868958adaa1a1c20b3c07bd1d8c4023d4adc5f1b45ca2d5bfa64697`;
the receipt defines the sorted-path/file-hash aggregation contract. These are
**gold39 debug classes for helper compile ABI**, not a byte comparison with the
installed release APK. They are separate from alpha9/code40 classes.

The frozen checkout's existing builder then ran:

```sh
python3 scripts/probes/build_authenticated_lan_helpers.py \
  --output /private/tmp/huoguo-gold39-saved-ui-i2pjvea3/helpers
```

It completed in5.54seconds. The builder produces its usual UI, V50 UI and receipt
artifacts; none were installed. The new saved-UI helper to use for the next owner
test is:

- Path: `/private/tmp/huoguo-gold39-saved-ui-i2pjvea3/helpers/ui/helper.apk`
- Size:61,843bytes.
- APK SHA256:`83722157753e50fc22c41099ea705521c43e4505d65a666af064097254f55b2a`.
- Verified original signer certificate SHA256:
  `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`.
- Required saved-mode field names are present in the built DEX.

The first independent signature-verification call failed because its subprocess
did not explicitly have JAVA_HOME. It is retained as a tool environment failure,
not a signature verdict. Repeating only verification with the existing JDK gave
exit0 and the expected certificate digest; no keystore was copied to the clone.

No App APK or helper was installed or published, and no phone, source player,
emulator or server operation was performed. This helper is ready for root's next
bounded owner test against the exact existing alpha8 target after fresh target
APK/hash, absent-PID and host-session preflight. It does not certify Android UI
restore, live-session lifecycle, media performance or alpha9 compatibility. The
absent-target-PID gate and no-force-stop rules from the contract remain required.
