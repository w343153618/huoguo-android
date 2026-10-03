# 独立无界面 instrumentation 生命周期验证

此工具只验证手机上 `am instrument --no-restart` 的实际生命周期行为。它不验证远程安卓的 codec/串流/FPS，不操作真实 App、界面、账号或用户数据。包名为 `local.huoguo.instrumentationlifecyclefixture`，instrumentation 仅 target 同一 APK 自己的包。

Manifest 没有任何 permission、Activity、Service、Provider 或 intent filter。只有一个显式 exported `SnapshotReceiver`；广播 action 必须等于 `local.huoguo.instrumentationlifecyclefixture.SNAPSHOT`。APK 使用已有可信 debug 签名，不复制或提交 signing key。没有 INTERNET、root、文件读写、UI automation 或 SharedPreferences。

广播 `resultData` 与 instrumentation 的 `fixture_snapshot` 都是最多 256 字符的闭合数字 JSON：`schema_version`、`pid`、`nonce_ns`、`snapshot_ns`。`nonce_ns` 是目标应用类加载器首次初始化时的 `SystemClock.elapsedRealtimeNanos()`，后续同进程/同类加载器保持不变；它是短期诊断标识，不是认证口令。Instrumentation 明确通过 `getTargetContext().getClassLoader()` 读取这个静态，避免读取自己类加载器的另一份静态造成假重启结论。

## 离线构建

使用已经安装的 Android37 SDK、build-tools36.0.0、JDK21 和已有 `~/.android/debug.keystore`；脚本不下载依赖，也不调用 ADB。

```sh
python3 scripts/probes/build_instrumentation_lifecycle_fixture.py \
  --output /private/tmp/huoguo-lifecycle-build-unique
```

输出目录必须是新建且不在源目录中。构建器核对源码 manifest、实际 APK 的包名/无权限/无 launcher、`apksigner` 验签和原唯一 signer；产物只保留在指定私有目录。ZIP 时间统一为1980-01-01，固定源码/既有工具/签名可作重复构建比较。每条工具调用最多60秒，输出上限64KiB，错误输出最多4KiB；receipt 只含公开包名、组件、文件路径和 SHA，不含私钥。

## 手机运行（仅主任务执行）

必须选定已授权手机 serial，把下面 `<serial>` 和 `<fixture.apk>` 替换成明确值；不要替换成真实 App 的包名。以下广播只启动 fixture 的无界面进程，不启动 Activity。单条操作应有宿主命令超时；实际 instrument 很快返回，若超过10秒应仅中止本 fixture 测试，不 force-stop 真实 App。

```sh
adb -s <serial> install -r <fixture.apk>
adb -s <serial> shell am broadcast -f 0x20 \
  -n local.huoguo.instrumentationlifecyclefixture/.SnapshotReceiver \
  -a local.huoguo.instrumentationlifecyclefixture.SNAPSHOT
adb -s <serial> shell am instrument --no-restart -w \
  local.huoguo.instrumentationlifecyclefixture/.LifecycleInstrumentation
adb -s <serial> shell am broadcast -f 0x20 \
  -n local.huoguo.instrumentationlifecyclefixture/.SnapshotReceiver \
  -a local.huoguo.instrumentationlifecyclefixture.SNAPSHOT
```

广播的 `0x20` 为 `Intent.FLAG_INCLUDE_STOPPED_PACKAGES`，让新装、没有启动过 Activity 的本 fixture 能被显式广播启动。它只用于此具名组件。记录第一次广播、instrumentation 和随后广播的四个数字，不只检查 exit code。`--no-restart` 期间 PID 与 nonce 都相同，才支持目标进程及该静态状态被保留。只有 PID 相同而 nonce 不同，应记录为类加载器/静态重新初始化，不能直接称 OS 重启；PID 不同表示进程身份改变。后续广播变化也要单独记录，instrumentation `finish()` 后是否保留进程是另一段生命周期，不应混在启动语义里。

另做默认行为对照，只允许本 fixture：

```sh
adb -s <serial> shell am broadcast -f 0x20 \
  -n local.huoguo.instrumentationlifecyclefixture/.SnapshotReceiver \
  -a local.huoguo.instrumentationlifecyclefixture.SNAPSHOT
adb -s <serial> shell am instrument -w \
  local.huoguo.instrumentationlifecyclefixture/.LifecycleInstrumentation
adb -s <serial> shell am broadcast -f 0x20 \
  -n local.huoguo.instrumentationlifecyclefixture/.SnapshotReceiver \
  -a local.huoguo.instrumentationlifecyclefixture.SNAPSHOT
adb -s <serial> uninstall local.huoguo.instrumentationlifecyclefixture
```

默认对照可能重启 fixture，允许它影响的范围仅是 fixture。两轮都要保存原焦点 App/真实测试 App 不被停止的观察边界。SDK/手机若拒绝选项、instrumentation 返回 error 或没有完整数字，则记录失败/未验证，不能从 flag 被 CLI 识别推论进程不会重启。

`tests/test_instrumentation_lifecycle_fixture.py` 使用实际三个 Java 源码、Android API 的 JVM 替身，验证广播静态稳定、错误 action、self-only target、跨类加载器读目标静态、数值边界、manifest 拒绝权限/UI/额外进程、输出路径保护和 ZIP 数据稳定。它不是 Android 实际生命周期验收。真实手机安装/运行/卸载由主任务完成后另记录，源码提交不包含 APK。

本候选离线 9 项检查通过，并完成两次已有 SDK/JDK/debug signer 构建和实际签名/包边界读回。两次 APK 字节完全一致：12,492 字节，SHA-256 `d6b1a4373cfbbb54de9b104844da023ca48a2e33e37e4a73715b5390929bc48b`。此记录只证明当前固定输入的离线可重复构建，尚未证明手机 `--no-restart` 的实际语义。
