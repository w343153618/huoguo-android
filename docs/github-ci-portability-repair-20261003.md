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
