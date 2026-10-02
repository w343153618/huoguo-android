# 一加 12 进一步降频后的真实视频验证

2026-10-01，本轮在用户进一步调整 CPU 限频后运行 **5 次、每次 35 秒**的真实视频串流。结果没有达到稳定 60 FPS：1080P/cap60/hint60 两轮为 **39.72、40.84 FPS**；1080P/cap120/hint120 为 **53.20 FPS**；720P/cap120/hint120 两轮为 **54.28、53.56 FPS**。

这里的 FPS 是手机实验客户端的独立 SurfaceFlinger present 时间线计数，不是请求值、屏幕 Hz 或 codec callback 数。CPU 限频读数动态变化，本轮不能称为“全程锁定约 0.7GHz”的实验，也不能证明与真我 V50 等效。

## 实验条件和范围

- 源端为 M1 的 `emulator-5556`，6 核、16GiB、1080×1920、480dpi，host GPU，`skiavk`。虚拟安卓与一加 12 的 min/peak 刷新率偏好保留 120Hz。
- 手机为 USB 连接的 root 一加 12 PJD110，Android 16。USB 用于控制和观测；媒体路径为 **M1 en7 有线 → 家庭局域网 → 手机 Wi-Fi**，AES-GCM UDP、10+2 FEC。
- 每次重新打开 Morphe YouTube 的同一公开 BBB URL，请求 seek60s，预热 8 秒。播放器前后均读到 playing 状态。媒体文件实际帧率和播放器实际清晰度仍未独立确认。
- 请求视频目标 8Mbps、自适应 VBR，UDP wire pacing ceiling 32Mbps，播放缓冲 60ms，FIFO，Surface submission lead0；8Mbps 是编码目标，32Mbps 是传输整形上限，不是实测带宽。
- 5 次都验证实际 Apple AVE 硬件编码和手机 `c2.qti.avc.decoder.low_latency` 硬件解码。720P 组编码读回为 720×1280；虚拟安卓物理屏幕仍为 1080×1920。
- 音频、原生触控服务开启。本轮没有执行标准化触控动作或测量光学响应、声学音画同步；这些不作为通过项。
- 每轮手机有效接收窗口完整覆盖 35 秒，矩阵播放门槛通过，源文件 fingerprint 未变化。没有新构建、发布或正式 App 默认参数更改。

## 手机实际呈现

共同手机窗口是首个认证视频包后的 `[5,30)` 秒，固定分母 25 秒。SF 时间线覆盖整个窗口。

| 编码尺寸 | FPS 上限 / content hint | 轮次 | 手机 SF FPS | 帧间隔 p99 | 最大帧间隔 | >100ms 间隔 |
|---|---|---:|---:|---:|---:|---:|
| 1080×1920 | 60 / 60 | 1 | 39.72 | 116.03ms | 190.61ms | 22 |
| 1080×1920 | 60 / 60 | 2 | 40.84 | 114.45ms | 314.94ms | 13 |
| 1080×1920 | 120 / 120 | 1 | 53.20 | 49.73ms | 140.87ms | 2 |
| 720×1280 | 120 / 120 | 1 | 54.28 | 49.72ms | 82.88ms | 0 |
| 720×1280 | 120 / 120 | 2 | 53.56 | 54.94ms | 132.59ms | 2 |

这些间隔是显示节奏，不是网络 RTT、端到端延迟或触控延迟。content hint 和 120FPS 上限都不是实际达到 120FPS 的证据。

## 瓶颈证据

在手机同一 `[5,30)` 秒窗口中，native 完整帧 delivered / input queue / callback 保留记录中的 output-ready / 独立 SF present 分别为：

| 样本 | delivered | input | ready 观测 | SF present |
|---|---:|---:|---:|---:|
| 1080P cap60 第1轮 | 990 | 990 | 989 | 993 |
| 1080P cap60 第2轮 | 1030 | 1030 | 1030 | 1021 |
| 1080P cap120 | 1331 | 1331 | 1332 | 1330 |
| 720P cap120 第1轮 | 1357 | 1357 | 1357 | 1357 |
| 720P cap120 第2轮 | 1359 | 1359 | 1352 | 1339 |

各事件按自身时间戳入窗，窗口边界不同；ready 记录只覆盖 callback 保留的帧。因此不能相减直接宣称确切丢帧数。vendor callback 的 render timestamp 不作为独立显示证据。

手机稳态输入数量基本跟随到货，没有持续 inbox 断链或 codec input timeout。1080P 组的启动 timeout/overflow 保留在原始报告中，不能用它们解释稳态低帧率。720P 第2轮有一次稳态 FEC `assembly_deadline` 到期，位于首包后约 6.235 秒；不要把所有轮次写成零 FEC 到期。

更早的供给缺口已经出现在源端 SF 与截图/gRPC 时间线。各自完整独立源端采样窗口的 SF FPS 依次为 **35.20、39.25、51.02、50.20、52.49**，窗口与手机固定 25 秒不同，不能直接相减作丢帧率。两轮约 40FPS 的测试同时存在显著源端供给断档，当前证据不支持“手机解码器持续堵塞把稳定 60FPS 输入压成 40FPS”。

720P 降低了 VT 提交至回调耗时，但源供给仍约 50 多帧，手机 SF 没有达到稳定 60。源绘制、视频解码/播放、模拟器截图交付内部的进一步归因仍待实验；没有将其中某个环节未经验证地定为唯一原因。

## CPU 限频和采样边界

初始读取的四组 `scaling_max_freq` 为 672000、729600、614400、672000kHz，8 核在线。约每 5 秒只读一次 sysfs，上限与当前频率随后动态变化；全过程出现过 1.6～1.8GHz 上限。结束快照为 1689600、1612800、960000、902400kHz。测试脚本没有写 CPU 频率、关闭限频工具或解除温控。

CPU 采样覆盖源播放器准备、测试及清理，字段是逐个读取，非原子快照，采样开销没有独立量化。不能将它视为每一帧对应的连续 CPU 运行状态。

额外时钟检查发现，本机 Python `time.monotonic_ns()` 与 trace 使用的 `clock_gettime_ns(CLOCK_MONOTONIC)` 存在约 6.089 秒 offset。**CPU bracket 不能直接对齐 trace 的 `[5,30)` 窗口；本轮撤回这种 CPU 稳态筛选，没有用事后 offset 强行校准。** 手机同域 SF/阶段统计和 trace 内显式同域的 capture/VT 分析仍有效。具体读数见 `clock-domain-check.json`。

CPU 轨迹、cap/hint、编码尺寸及每轮实时片段共同变化。本轮不是固定频率、相同编码字节的严格因果 A/B，不能把不同轮次的差异单独归因于降频、content hint 或分辨率。

## 保留状态与证据

手机与虚拟安卓的 120Hz 偏好保留，AVD 配置 SHA-256 与开始一致，物理 1080P、6 核、16GiB 保留。最后一次实验编码尺寸为 720P。结束后没有源捕获进程，本机 gateway 与下载服务均返回 HTTP200；没有操作 M5、NPS、代理规则或账号，没有发布 APK。

本轮证据位于 [evidence/oneplus12-lower-cpu-60fps-20261001](evidence/oneplus12-lower-cpu-60fps-20261001/)：

- `preflight.json`、`final-retained-state.json`：前后只读快照。
- `invocation*.json`：准确命令及采样方法，使用既有独立客户端与探针。
- `real-*/matrix.json` 及各轮报告、SF/trace companions：真实串流、播放完整性门槛与呈现时间线。
- `comparison.json`：手机固定窗口、host 自身窗口、全过程 CPU 范围及明确的时钟限制。
- `cpu*-samples.json`：只含 CPU 数值、policy 名称和采样 bracket。
- `clock-domain-check.json`：CPU 与 capture 时钟不能直接对齐的读回证据。

这里验证的是 M1 家庭局域网至降频一加 12 的真实视频表现；未验证公网 NPS、蜂窝网络、东北 Wi-Fi、真我 V50 或长时间音画同步。当前可报告约 53～54FPS 的较好组，**稳定 60FPS 目标尚未达到**。
