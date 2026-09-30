# M1 Max 本机 Android 16 / 17 对照（2026-09-30）

结论：在这台 M1 Max 上，相同的本机测试条件下 Android 16 没有表现出足以解释或修复远程视频掉帧的优势。两版都能稳定采集并使用 Apple VideoToolbox 编码 30 FPS；60 FPS 目标下两版均接近 60 FPS。相同的本地 30 FPS 动态测试片通过 VLC 播放时，两版画面变化节奏也接近。**这些数据不能证明 M5 上的抖音／YouTube、腾讯云中转或手机端会同样流畅。**

## 条件

- 宿主：Apple M1 Max；同一台 Mac、同一 SDK Emulator 37.1.11.0、Hypervisor.Framework、宿主 GPU。
- 镜像：Google APIs ARM64，Android 16/API 36 revision 7 与 Android 17/API 37.0 revision 6；均为新建的独立 AVD，未改动原有 M1 Android 17 或 M5 正式实例。
- 每个 AVD：4 vCPU、4 GiB RAM、物理 720×1280、320 dpi、60 Hz、`hw.gpu.mode=host`、`hw.gltransport=pipe`。
- 合成绘制：同一 DiagnosticSource APK；本机 gRPC 原始画面采集；同一 VideoToolbox H.264 编码器，目标 4 Mbps。每版每档 2 组，每组 10 秒有效测量，另有 2 秒预热。
- 视频播放：同一个 540×1200、30 FPS、H.264 动态测试图案重复拼接成 24 秒，VLC 3.7.1 前台实际解码和显示；确认系统 MediaSession 为 PLAYING 后，以同一 gRPC 接口采集画面变化。每版 2 组，每组 10 秒。首次 VLC 帮助浮层遮挡画面的样本已作废，不纳入结果。

## 结果

| 场景 | Android 16 | Android 17 |
| --- | --- | --- |
| 合成 30 FPS：采集／硬编输出 | 30.0／30.0、30.0／30.0 FPS | 30.0／30.0、30.0／30.0 FPS |
| 合成 60 FPS：采集／硬编输出 | 59.8／59.8、60.0／60.0 FPS | 59.4／59.4、59.9／59.9 FPS |
| 60 FPS 编码帧间隔 p95 | 17.99、18.16 ms | 19.22、18.73 ms |
| 本地 30 FPS 视频：连续变化画面 | 31.7、31.2 FPS | 31.8、31.7 FPS |
| 本地视频画面变化间隔 p95 | 34.27、34.31 ms | 34.60、34.51 ms |
| 本地视频超过 50 ms 的变化间隔 | 1、2 次 | 3、1 次 |

VLC 视频的变化画面数可能包含播放开始时控件／状态的绘制，因此略高于源文件 30 FPS；不能把它当作解码器的精确输出帧率。两版数值差距很小，只有 2 组短样本，不能据此断言哪版整体更快。宿主 CPU 单核百分比在两版各样本间也有波动，不适合推断 Android 16 一定更省 CPU。

## 适用边界与下一步

本次没有测 M5 正式 AVD 的实际抖音／YouTube 播放，也没有通过 NPS/Tailscale 到手机测显示帧率、音视频同步或网络抖动。M5 实例的 CPU、内存、显示频率、已安装应用和后台负载也不同。当前证据**不支持仅为解决视频掉帧而把 M5 从 Android 17 迁移到 16**。更有判别力的下一步是在 M5 正式实例播放实际问题视频时，同时记录视频源、原始采集、VideoToolbox 输出、网关发送、手机接收／解码／显示的逐段帧率和间隔；优先定位出现第一处掉帧的环节。

原始指标文件在本目录的 `android16-30-hardware.json`、`android16-60-hardware.json`、`android17-30-hardware.json`、`android17-60-hardware.json`、以及四个 `*-vlc-local-verified*.json` 中。`real_video_capture_probe.py` 是本机 VLC 画面变化测量脚本；原始像素只在内存中计算哈希，没有保存或输出。两个测试 AVD 已关机，正式 M5 实例未受影响。
