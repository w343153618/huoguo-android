# GitHub build verification failures: diagnosis and fixture repair

The email for experimental commit `780d863b3549a5a0db1f771db2d244804dcbdf3b`
corresponds to [Actions run 37133401441](https://github.com/w343153618/huoguo-android/actions/runs/37133401441).
The Android `Compile and lint` step passed. The subsequent Python test step
failed: 1443 tests, 10 errors, 2 failures, 11 existing platform skips. The
dependent `udp_candidate` job was skipped. Three additional recent runs were
examined; the same ten errors and canary fixture failure repeated, while the
duplex socket failure was intermittent. This does not prove every historical
failure has the same cause.

## Actual cloud completion, 2026-10-04 Asia/Shanghai

- Experimental repair `7894bb760253921d3613512af514e961ab091df0`:
  [run37135320411](https://github.com/w343153618/huoguo-android/actions/runs/37135320411)
  completed `success`; both `build` and `udp_candidate` succeeded. Linux
  discovery executed 1445 tests in 87.718 seconds, `OK (skipped=11)`; these are
  the existing platform-specific skips. Android and isolated UDP compilation,
  lint, pinned dependency validation, APK package/JNI/signature checks passed.
- The latest stable failure was independently examined: `86189ce`,
  [run37106309068](https://github.com/w343153618/huoguo-android/actions/runs/37106309068),
  Android compile/lint passed, one updater SDK-path test error. Only the same
  updater fixture discovery change was backported to main; no experimental
  production implementation was merged. Local stable discovery passed 656
  tests in 15.561 seconds. Stable repair
  `f5b976a099c0a0372d3d79f32f7240dde1d81d9c`:
  [run37136211052](https://github.com/w343153618/huoguo-android/actions/runs/37136211052)
  completed `success`; `build` succeeded, `udp_candidate` was skipped by its
  existing branch policy.

Both pushed repair commits were verified by final Actions API reads, not just
local test results. Remaining action/runner migration annotations are warnings
in successful runs, not the failed test causes above.

## Follow-up: owned DNS accounting race, 2026-10-04

The later documentation-only commit
`c19c3fe78ddad2c3e9b5aec507f246697f38980f` failed in
[run37146375219](https://github.com/w343153618/huoguo-android/actions/runs/37146375219).
Its sole failure was
`DNSOutletTests.test_owned_upstream_randomized_id_and_bad_replies_then_valid`:
the client received the correct reply, but the immediate `replied` snapshot
was zero. Linux ran 1565 tests in 80.848 seconds with the same 11 platform
skips. The dependent UDP job was skipped. This commit changed only documents;
the preceding same-source `b07009b` run37145236790 passed both jobs.

The real worker sends the UDP reply before incrementing `replied`. Client
receipt therefore does not establish that post-send accounting has completed.
The fixture now deliberately holds that accounting point with two bounded
events while using actual owned loopback sockets. It verifies the restored
client ID, randomized upstream ID, rejected malformed replies, physical-bind
calls, and the pre-accounting zero snapshot. It then releases the worker and
checks the final count of one after the existing owned guard has joined every
worker. A gate timeout remains a failure; there is no arbitrary delay, new
skip, or weakened count assertion.

Only the test fixture changes. Production DNS validation, resolver binding,
worker ordering, isolation guards and the workflow remain unchanged. The
focused module passed 25 tests in 1.061 seconds; root independently ran the
full repository suite: 1582 tests in 58.420 seconds, `OK`. The repair was pushed
as `73bc80f97ff2a9d22efcc7e37b980a277d9cda0d`. Its exact
[run37147182008](https://github.com/w343153618/huoguo-android/actions/runs/37147182008)
completed `success`, with both `build` and `udp_candidate` successful. Linux
ran 1565 tests in 85.910 seconds, `OK (skipped=11)`; stable Android compile/lint
took 2m15s and the UDP candidate 1m35s. These are actual cloud results for this
SHA. They do not publish a new APK or assert a real-phone performance gain.

## 2026-10-04: helper compilation dependency list repaired

The exact source-input helper commit `6382bf59e2990715625778019d47a5c9d94f7131`
failed [run37168197526](https://github.com/w343153618/huoguo-android/actions/runs/37168197526).
Android compile/lint succeeded, but the separate API37 Java compilation fixture
passed `LanUiAcceptance.java` without its new `OwnerSourceTap.java` dependency.
The compiler reported ten missing-class references; Linux finished 1722 tests
with one failure and the same eleven platform skips. The UDP job was skipped
after that dependency failure, not accepted.

The actual signed-helper builder already included both sources. The repair
adds the missing dependency to the separate fixture's explicit compiler input
list. No production code, source-input ownership guard, workflow or skip policy
changes. The formerly failing module plus the helper transaction, target,
same-snapshot observer, Stats and callback suites passed 56 tests in 3.301s,
including actual API37 compilation. The repair `6142e0ed71903ea8045a2ba43afa757790e8d668` completed its own
[run37168471495](https://github.com/w343153618/huoguo-android/actions/runs/37168471495)
with overall, build and udp_candidate all successful. Linux ran 1722 tests in
79.717s, `OK (skipped=11)`. These are actual results for the repaired SHA;
the preceding green runs do not validate this change. No APK/manifest, phone,
guest, gateway, M5 or NPS operation is part of this repair.

## Confirmed causes and scoped changes

- Four test modules used the owner's absolute Mac JDK/Android SDK paths.
  They now discover `JAVA_HOME`, `ANDROID_HOME`, or `ANDROID_SDK_ROOT` and
  retain actual compilation against the pinned API37.0/build-tools36.0.0.
  Missing dependencies still fail; no new skip is introduced.
- The token-broker fixture passed Linux anonymous-pipe metadata to a Darwin
  `nlink == 0` guard. Only its owned writer metadata is modeled as Darwin;
  real pipe I/O remains. Linked-FIFO and read-end rejection cases remain
  explicit. Production guard code is unchanged.
- The receipt fixture modeled root ownership but left Linux `/tmp` writable
  in a simulated immutable-root deployment. Only the preidentified scaffold
  ancestor inodes are modeled immutable. Actual candidate-root permissions
  remain checked, including new 0777/0775 rejection cases.
- The offline canary fixture expected `/private/var/tmp`, absent on Ubuntu.
  Its single fresh nonce base is relocated into its owned temporary tree.
  All 18 case layers and inode-aware cleanup remain exercised. This is an
  offline fixture, not host/LAN isolation acceptance.
- The duplex socket fixture could leave an `ALIVE` response queued after both
  receive threads joined, so the final ownership assertion read that response
  instead of its marker. It now drains only known responses within a packet
  count bound and deliberately queues one response to cover this case every
  run. Reader ownership, writer close, real socket I/O and accounting remain
  checked.

## Validation and operational boundary

The five toolchain/socket modules passed 30 actual compile/loopback tests;
eight additional compile checks used separate private SDK/JDK paths to verify
environment discovery. The three safety fixture modules passed 105 tests.
Root independently reran the full repository suite: 1462 tests in 64.508
seconds, `OK`, with the sandbox lifted for owned loopback/process fixtures.
No new skips were added. GitHub's Linux result must be checked for that exact pushed
commit; local tests cannot substitute for cloud verification. The workflow
remains enabled and its checks are not suppressed.

This repair changes test fixtures only. It does not publish an APK, change a
download manifest, modify guest memory/rendering, restart a gateway/NPS, alter
NPC identities or operate a phone. Published alpha8/code39 and stable
1.31/code32 remain separate from this CI repair. Neither local nor cloud test
success constitutes real-phone FPS, acoustic/optical latency, WAN/V50 or
friend/public isolation acceptance.

## 2026-10-04: shallow checkout lacks the exact original registry fixture

The diagnostic-selection source4f882eb112089879b91241392c83885a80f0f167
failed its own [run37198085659](https://github.com/w343153618/huoguo-android/actions/runs/37198085659).
Linux discovery ran1931 tests in79.273s with one setup error and the existing11
platform skips; the UDP job was skipped after build failed. The exact failing
fixture was `FrozenRegistryWitnessTests.setUpClass`, with
`required_frozen_dd43_source_unavailable`. This is a new prerequisite failure,
not one of the earlier repaired path/socket/helper errors.

Before4f882, the canonical registry matched the original dd43 pinned bytes.
After the intentional default-OFF constructor addition, the fixture needed its
`git show dd43:udp_lan_sessions.py` fallback for the first time. The shallow CI
checkout has no such historical object. Local focused checks had the full object
and therefore did not expose the cloud prerequisite. No phone, guest or live
gateway failed or changed in this CI run.

The fixture now bundles all four exact dd43 source-only inputs and verifies
their existing immutable SHA256 pins before compiling private module namespaces.
Missing or corrupted bytes still fail; foreign module paths are refused. It
does not substitute current source, fetch history, remove assertions or add a
skip. Two integrity checks and the four original registry concurrency checks
are retained. With selection/registry/worker/gateway/admission fixtures,234
focused checks passed0.583s locally. Production selection, live admission safety,
workflow and platform skip policy are unchanged. Exact byte pins and actual
failed-run evidence are in `frozen-dd43-CI-fixture-repair-20261004.json`. The
repair requires its own new-SHA cloud result; failed4f is not re-run or accepted.

The pushed repair84b98ead9a11972cdce83b2e18bdbbc9f361bf2f completed its own
[run37198802511](https://github.com/w343153618/huoguo-android/actions/runs/37198802511)
with overall/build/udp_candidate all successful. Linux discovery ran1937 tests
in82.971s, `OK (skipped=11)`; these are the same existing platform skips. Exact
SHA/job results and the completed build log were independently read. A NEW
source-only freeze of1063 tracked files/341 Python files without Git history
also passed36 targeted checks in0.042s; all tracked hashes remained unchanged.
Neither cloud compilation nor that source-only check is phone/ART/native sampling
acceptance. No App package, manifest, device or live service was changed.
