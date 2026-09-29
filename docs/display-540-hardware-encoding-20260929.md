# 540P 物理屏幕与 macOS 硬件编码实验记录

日期：2026-09-29。目标设备：M5 上的 ARM64 Android Emulator；对照设备：M1。本文记录本轮现场读回和隔离实验的边界，供后续优化和审阅使用。

## 当前结论与部署状态

M5 虚拟安卓的物理屏幕已改为 **540 × 1200、210 dpi**，并经过冷启动读回验证。macOS 的 VideoToolbox 硬件 H.264 编码已在 M5 的独立测试中确认可用，但真实模拟器画面的 gRPC RGBA 采集目前只有约 **17–18 FPS**，因此尚未把该原型接入生产串流。生产仍使用 Android 内的 **`c2.android.avc.encoder` 软件编码器**。

**本轮没有发布新 APK。** 手机上的参数保存、登录、清晰度选项和升级流程没有因这次实验而更新。M5 上原有的 NPC 国内直连及物理网卡绑定保护继续保留。

## 1. 降低物理屏幕分辨率：已应用，收益不能等同于 CPU 降幅

| 项目 | 原配置 | 当前配置 | 验证状态 |
| --- | --- | --- | --- |
| 物理像素尺寸 | 1080 × 2400 | 540 × 1200 | 冷启动后已读回 |
| 显示密度 | 420 dpi | 210 dpi | 冷启动后已读回 |
| 每帧像素数 | 2,592,000 | 648,000 | 降至原来的 1/4 |
| 现有串流目标宽度 | 540 像素 | 540 像素 | 编码目标此前已是 540 |

像素数量减少 75%，有机会降低安卓界面绘制、合成和画面读回的工作量。密度同时减半，维持了原来的逻辑界面尺寸。不过，应用业务逻辑、网络、音频和虚拟 CPU 的开销不会按像素数同比下降；**不能据此宣称 CPU 下降 75%**。

本轮在修改前后分别进行了 **3 组、每组约 10 秒**的现有软件串流采样，输出均大致落在 **20–23 FPS**。这些短样本没有证明帧率明显提高，也没有证明 CPU 显著下降。它们不能替代长时间、同一内容和同一网络条件下的手机实测。

当前源屏幕只有 540 × 1200，因此 App 即使选择 720P 或 1080P，也不能获得超过源画面的真实细节；是否放大或沿用源尺寸还取决于具体串流配置。scrcpy 的尺寸限制是对源画面缩放，帧率限制也是上限，实际输出取决于画面更新。这一限制是依据源尺寸与官方行为作出的判断。[scrcpy 官方视频说明](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md)

## 2. GPU 绘制加速与视频硬件编码是两个环节

Android Emulator 的 `-gpu host` 使用宿主 GPU 加速图形渲染，虚拟 CPU 加速由虚拟化框架承担。它们并不证明 Android 内的 MediaCodec 已获得 macOS VideoToolbox 硬件编码器。本轮运行时检查仍显示当前安卓串流使用软件编码器。[Google 官方模拟器加速说明](https://developer.android.com/studio/run/emulator-acceleration)

可行的替代链路是：

```text
安卓界面 → Emulator gRPC 完整 RGBA 帧 → macOS VideoToolbox 硬件 H.264
        → 现有串流协议 / NPC / NPS → 手机硬件解码与显示
```

开源项目 Tapflow 已实现同类路径，包含 Swift 编码器、模拟器截图流、音频流和触摸控制。它的 Node 层会保留最新待处理帧并处理管道背压，避免原始帧无限堆积。这证明架构存在实际开源实现，不能据此保证在当前 M5、当前模拟器版本和现有 App 上直接替换就更流畅。[Swift 编码器](https://github.com/jo-duchan/tapflow/blob/main/packages/android-agent/src/emulator-encoder.swift)、[EmulatorVideo](https://github.com/jo-duchan/tapflow/blob/main/packages/android-agent/src/emulator/EmulatorVideo.ts)、[EmulatorGrpcClient](https://github.com/jo-duchan/tapflow/blob/main/packages/android-agent/src/emulator/EmulatorGrpcClient.ts)

Tapflow 的 Swift 创建参数没有强制要求硬件编码，也没有把实际硬件使用状态作为验收条件。因此，本轮原型额外要求硬件编码，并读回实际使用属性；不能只凭调用 VideoToolbox 就宣称硬件加速成功。若后续复用其代码，需要保留 MIT 许可证要求的版权及许可声明。[Tapflow 许可证](https://github.com/jo-duchan/tapflow/blob/main/LICENSE)

## 3. M5 独立硬件编码测试：已通过，约 5 ms 仅指编码阶段

测试使用 **540 × 1200 的合成 NV12 输入**，并非从模拟器实时采集的画面。设置为 H.264、实时编码、禁止 B 帧，强制硬件编码；读回确认实际使用硬件，编码器为 AppleAVE。

| 测试帧率 | 硬件要求 / 实际使用 | 丢帧 | 编码回调耗时 P95 |
| --- | --- | --- | --- |
| 30 FPS | `true / true` | 0 | 5.44 ms |
| 60 FPS | `true / true` | 0 | 5.20 ms |

这说明 **M5 的编码硬件能承担该尺寸的测试负载**，没有证明真实 gRPC 采集也能达到 60 FPS，更不代表手机端总延时只有 5 ms。数字不包含安卓生成画面、gRPC 交付、原始帧管道、NPS 网络转发、手机解码和屏幕显示。硬件要求与实际使用读回分别对应 Apple 的官方接口。[强制要求硬件编码](https://developer.apple.com/documentation/videotoolbox/kvtvideoencoderspecification_requirehardwareacceleratedvideoencoder)、[读取实际硬件使用状态](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_usinghardwareacceleratedvideoencoder)

隔离测试源代码：

- [合成 NV12 编码能力探针](../scripts/probes/videotoolbox_probe.swift)
- [完整 RGBA 输入到硬件 H.264 的原型](../scripts/probes/emulator_hardware_encoder.swift)

RGBA 原型使用 IOSurface 像素缓冲池和 Accelerate `vImage` 通道置换，保持 540 像素宽度，不裁成 528。它具有有限的在途帧数和超时保护，但这仍是实验工具；生产需要在采集入口保留最新完整原始帧、丢弃过期原始帧，不能随意丢弃已编码的 H.264 预测帧。

原型会尝试设置 `MaxFrameDelayCount` 并报告返回值。在本地 Apple 编码器测试中，设置 0 和 1 均曾返回 `-12900`；即使读取值为 0，也不表示设置成功。该属性不能作为已生效的优化来宣传。当前采用普通实时、无 B 帧模式；Apple 的专用低延迟编码模式仍需单独验证属性支持与实际硬件使用。[Apple 低延迟编码说明](https://developer.apple.com/documentation/videotoolbox/encoding-video-for-low-latency-conferencing)

## 4. M5 真实采集路径：暂不满足替换生产的条件

当前模拟器版本为 **37.1.11.0，build 15917651**。真实 `streamScreenshot` RGBA 路径测得约 **17–18 FPS**，低于本轮现有软件串流约 20–23 FPS 的采样范围。两者的采样条件尚不足以做严格统计比较，但已足以说明：仅替换编码器不能保证当前真实链路更快，需要先解决或重新定位采集吞吐限制。

540 × 1200 的 RGBA 帧为 **2,592,000 字节**；连续 30 FPS 约 77.76 MB/s，60 FPS 约 155.52 MB/s。这是 **M5 本机内的原始画面传递量**，不是公网 NPS 的码率。gRPC 官方协议提供普通通道和共享内存传输，截图流按画面更新产生帧；PNG 会增加压缩开销。[Google Emulator gRPC 协议](https://github.com/google/android-emulator-webrtc/blob/master/proto/emulator_controller.proto)

共享内存有减少序列化和复制开销的潜力，但当前协议明确提示可能出现撕裂。Apple Silicon 上也有公开的 MMAP 崩溃或不出帧问题，修复讨论指向后续 37.2.3 Canary；当前生产 37.1.11 是否涵盖修复尚未验证。本轮使用普通通道的完整帧，没有切换生产 MMAP，也没有为此升级生产模拟器至 Canary。[Google MMAP 问题记录](https://issuetracker.google.com/issues/537802959)

## 5. M1 真实 gRPC + VideoToolbox 组合测试：本机链路已通过

M1 Max 使用隔离、只读的 Android 17 测试 AVD，配置为 4 vCPU、4 GB 内存、物理尺寸 540 × 1200、密度覆盖 210 dpi、宿主 GPU，并使用与 M5 同版本的 SDK。安卓内生成合成测试画面，再经过真实 gRPC RGBA 采集、`vImage` BGRA 转换和 VideoToolbox H.264 编码；不是直接向编码器喂合成缓冲区。

两组测试各约 10 秒，均强制要求硬件且读回确认使用硬件。输出采用现有手机端的视频帧协议格式，`ffprobe` 解析通过，`ffmpeg` 解码退出码为 0；这验证了本机码流结构和解码，尚未验证手机实际接收。

| 指标 | 第 1 组 | 第 2 组 |
| --- | --- | --- |
| 实际采集帧率 | 30.0 FPS | 30.1 FPS |
| 实际编码输出帧率 | 30.0 FPS | 30.0 FPS |
| 源时间戳到 H.264 输出管道耗时估计 P50 | 12.953 ms | 12.800 ms |
| 源时间戳到 H.264 输出管道耗时估计 P95 | 15.719 ms | 15.193 ms |
| 帧间隔 P95 | 38.517 ms | 39.275 ms |
| QEMU CPU，按单核 100% 计 | 41.699% | 41.996% |

这是 **M1 上本机画面采集加编码**的结果，未包含音频、触摸、公网、NPS 或手机解码显示；测试内容也不是实际短视频播放。源时间戳耗时属于估计，不能作为用户操作到手机画面的端到端延时。

| 仍待补充的项目 | 状态 |
| --- | --- |
| M5 17–18 FPS 与 M1 30 FPS 的原因 | 待定位；不能跨机器量化改善幅度 |
| 实际 gRPC RGBA 画面上下方向验证 | M1 SDK 37.1.11 的实际合成截图已目视确认正向；原型不额外翻转 |
| 序号跳跃、在途帧峰值及持续运行稳定性 | 待补充 |
| 同一设备、同一内容下与现有软件串流的比较 | 待补充 |
| 音视频同步、触摸、公网及真实手机验证 | 未完成 |

另需区分原型的“完整 RGBA 到回调”指标：它从 stdin 收到完整原始帧后开始，不含 gRPC 交付和原始管道传递时间，与本节的源时间戳到 H.264 管道估计不是同一指标。

## 6. 显示缺口修正与上线前必须解决的问题

M5 已备份并部署启动后的 `display_profile` 辅助代码，持久启动配置启用 `DIRECT_PHYSICAL_DISPLAY=540x1200`。部署时有实际 YouTube 串流连接，因此没有重启正在运行的网关；**新辅助步骤尚未在该运行进程中启用，重启后持续生效的验证也未完成**。它只在每次安卓启动完成后尝试禁用两个 Pixel 6 覆盖包，并检查实际物理尺寸和密度。此步骤默认关闭；不能把部署源文件视为显示缺口已经永久修复。

硬件编码原型接入生产前，仍需完成以下验收：

1. **真实吞吐与手机实测：** 同一内容下比较采集、编码、输出帧率及 CPU，再验证公网、真实手机的连续播放；编码能力探针不能替代这些检查。
2. **音视频时钟：** gRPC 图像的 `timestampUs` 是宿主侧 Unix 微秒时间估计，现有 scrcpy 音频通常使用安卓侧单调时间。必须映射到统一时钟，或让画面和音频走统一采集时钟，再实测口型与声音同步。
3. **画面方向与触摸：** 安装的协议注释包含自下而上的像素说明，而 Tapflow 作者记录其实际截图流为正向。M1 上已用实际合成画面的文字和上下不对称内容验证当前版本为正向，不额外翻转。仍须验证 M5 生产采集，以及物理尺寸、逻辑尺寸和触摸坐标对应关系。
4. **有界队列与完整帧：** 采集入口保留最新完整帧，处理编码和网络背压；验证持续运行时不会堆积延时或出现撕裂。
5. **恢复与显示配置：** 验证冷启动、息屏再唤醒、缺口修正和异常断线恢复，再决定是否切换。

NPC 继续运行在 M5 宿主机，其物理网卡绑定和国内直连保护保持原状。安卓访问境外网站所需的 Clash 出口与 NPC 到腾讯 NPS 的国内连接应继续分开核查；不因采用硬件编码而改变出口规则，也不能把已有保护描述为对腾讯风控结果的绝对保证。

## 7. 可复核证据与最小下一步

指标报告在 [evidence/display-540-20260929](evidence/display-540-20260929/)：`m5-display-before.json`、`m5-display-after.json`、`m5-display-cap60.json`、`m5-videotoolbox.json`、`m5-raw-capture.json` 和 `m1-hardware-pipeline.json`。只提交性能指标，不提交模拟器令牌、认证文件、原始私人画面或服务端密钥。M1 的只读临时实例已关闭，原 AVD 数据不变。

M5 的三组 RAW 产帧时间戳间隔与到达间隔接近，而产帧到收到 RAW 的估计 P95 仅约 7 ms。这不支持本地 gRPC 队列积压的解释，更提示供帧环节在编码前已经不均匀。`scene_target_fps=30` 只表示目标；旧 M5 测试以 `force-stop` 结束，缺少生命周期中的实际唯一绘制计数，尚不能断言应用本身实际绘制了 30 FPS。

下次无人在用时，先让同一诊断源预热 2 秒，在同一 10 秒窗口记录 `gfxinfo framestats` 的实际完成帧和 RAW 帧数，再正常 BACK 退出以取得 `unique_drawn_frames`、`skipped_source_ticks`、`hardware_canvas`。若源也只有约 17–20 FPS，先排查绘制、刷新率和宿主后台状态；若源是 30 FPS 而 RAW 低，排查模拟器帧缓冲回调；只有源与 RAW 都达到目标，再加入 VT 比较。这能避免用更多 vCPU 或内存掩盖尚未定位的问题。
