# 源端绘制配置的只读预检

新增 [`read_source_renderer_preflight.py`](../scripts/probes/read_source_renderer_preflight.py) 只采集已知模拟器和已知 YouTube 源 App 的配置与进程信息。它不修改属性，不重开 App，不启动媒体，不执行 `gfxinfo reset`，不替换正在运行的冻结 UDP 服务，也不成为默认的会话准入条件。

本轮使用背景是机主 M1 在 alpha8 冷启动后读回 HWUI 属性 `skiagl`，源 App 进程使用 OpenGL，而历史实验目标为 `skiavk`。历史记录已说明这项属性不是自动持久化的；类似的局部翻转/错位曾在恢复 HWUI 目标并重开播放器后消失，见[源端对照记录](source-causal-results-20261001.md)。这不是把历史 135 秒测试重新归因为某个确定根因，也不是证明改为 Vulkan 就能消除所有掉帧。

从代码入口可以解释为什么存在漂移：正式 [`gateway.py`](../gateway.py) 的 `ensure_android()` 在已启动和新启动的 guest 上调用 `apply_performance_profile()`；认证 UDP 的 [`LanMediaWorker._start_media()`](../udp_lan_worker.py) 直接构建硬件会话，没有调用该配置步骤。不能把正式配置函数整体复制到 UDP 入口：当前 `performance_profile.py` 只接受 60/120 的刷新率配置，而本轮物理显示已固定 30Hz；这样既可能使启动失败，也可能重新应用旧刷新率。此次没有修改这两个运行路径。

## 用法与字段

SDK 默认路径是宿主用户目录下的 `Library/Android/sdk/platform-tools/adb`，可用 `--adb` 指定同一台宿主上受信的 SDK adb。必须显式选择 M1/M5 的已知模拟器串号、已知源 App 和预期 renderer。例如在仓库根目录执行：

```sh
python3 scripts/probes/read_source_renderer_preflight.py \
  --serial emulator-5556 \
  --source-package app.morphe.android.youtube \
  --expected-renderer skiavk
```

加 `--output docs/evidence/<本轮目录>/source-renderer-preflight.json` 可写一份新的脱敏 JSON；目录应已存在，脚本拒绝覆盖旧文件。没有访问手机或修改手机 CPU/刷新率的入口。M5 需在该宿主本地使用 `emulator-5554`，不是从 M1 的 adb 中推测另一台 guest。

报告分别记录三个检查结果，不把它们合成一个“已修复”结论：

- `renderer_property_target_drift`：当前 HWUI 属性是否偏离显式指定的目标。
- `target_vs_process_pipeline_mismatch`：实际源 App 的 HWUI Pipeline 是否偏离该目标。
- `property_vs_process_pipeline_mismatch`：实际进程是否还保留与当前属性不同的 Pipeline。

因此，属性已经是 `skiavk`，但旧进程仍是 `Skia (OpenGL)` 时，第一个标志为 false，后两个为 true。只读取到正确属性不能当成现有播放器已用 Vulkan。AOSP 的 [HWUI Properties.cpp](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/libs/hwui/Properties.cpp) 在进程内缓存已选 Pipeline，已进入绘制后不能任意更换；本次核对的是 2026-10-03 的上游 main/blob `064cac2a6fc6bf4a15ba5679c86d7091cd2eec2c`，不是对当前 guest 框架二进制的逐字节比对。

RenderEngine 的属性单独读取，不与 App HWUI Pipeline 混在一起。脚本没有把 `skiaglthreaded` 判成故障，也没有替换 SurfaceFlinger RenderEngine。HWUI Vulkan、VideoToolbox 视频编解码和完整远程链路的帧率是不同证据层次。

输出只保留固定枚举、有限数值、固定查询状态。源 App 的 PID 在 `gfxinfo` 前后各读一次，boot ID 也前后核对；只在一个稳定 PID、同一个可验证 boot 且 gfxinfo 的 PID/package 完全匹配时归属 Pipeline。boot UUID、原始 gfxinfo/window 输出、窗口标题、其他进程信息、stderr 和异常文字都不进入报告。PID 的重复读回不能排除极罕见的同 PID 复用，因此不宣称完整进程生命周期追踪。

显示证据包括 `wm size` 的物理/覆盖尺寸、物理/覆盖 density、`cmd display get-active-mode 0` 的尺寸/Hz 和实际 DisplayRotation 数值；用户旋转偏好不是实际旋转。当前 mode 输出契约为 `Active mode for display 0` 下的 `Mode ID / Resolution / Refresh Rate`，旋转只取唯一 display0 段落的 `mRotation=0 mDeferredRotationPauseCount=0` 一类数值行，不从 overrideConfig 里重复的 ROTATION 文本猜测。命令不支持、文本形状未知、多 PID、进程变化、boot 无法确认、输出过大、超时等都明确为 partial/unavailable，比较标志为 null，不猜成一致或不一致。`pidof` 非零退出也保留查询不可用，不从空输出推测源 App 必然不存在。

每个 adb 客户端最长 3 秒，整个样本的查询预算 20 秒；输出限制分别为属性/尺寸 512B、PID 128B、gfxinfo 256KiB、窗口显示信息 64KiB。超时或超限仅结束此探针拥有的本地 adb 子进程，不给 guest App、gateway、NPS 或 emulator 发停止信号。报告 complete 时退出 0，元数据不完整时退出 2；renderer 不匹配本身不触发自动修复或停止服务。

## 验证与后续边界

[`test_source_renderer_preflight.py`](../tests/test_source_renderer_preflight.py) 的 16 项离线检查通过，覆盖冷启动属性漂移、属性已经更改但进程未变、PID/boot 变化、gfxinfo 错 PID/package、不可识别值、尺寸/刷新率界限、实际 display0 与其他显示器/overrideConfig 区分、隐私输出、超时/超限客户端收尾。测试使用离线文本和临时本地假客户端，没有连接设备，也不是图片正确性或性能验收。

下一次真正的机主源内容实验应先用此预检固定 actual/expected。若需要恢复 `skiavk`，应由受控的机主实验另行安排，只在确认不影响正式会话后改变这一项，并对已知源播放器单独重开、再次读取实际 Pipeline 和布局。该动作不在此脚本里，更不能在用户远程播放时静默 force-stop 任意 App。随后仍需重新确认真实视频格式、源/手机节拍和长尾，不能把源端元数据当作 UDP 流畅度、音画同步、真我 V50 或公网蜂窝验收。

主任务已对既有 M1 执行一次实际只读预检，见 [固定字段读回](source-renderer-preflight-readback-20261003.json)。sample complete、各查询ok、前后同boot/同PID3391；HWUI为skiagl、实际pipeline opengl，期望skiavk的两项漂移标志为true，属性与进程管线一致，对应 mismatch 为false。物理1080×1920/density480、实际30.00Hz、rotation0。退出0表示元数据完整，**不表示渲染策略正确或布局已经修复**。本次没有属性写入、播放器重开或媒体性能采样；脚本16项离线检查也由主任务独立复验通过。
