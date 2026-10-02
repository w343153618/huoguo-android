# Gemini 跨终端低延迟研究评审

日期：2026-10-01。用户提供的[分享报告](https://share.gemini.google/MTnG4kAc2Pt4)已完整读取；分享重定向到 Gemini `c936222baa1d`。这份记录是技术评审，不是新的性能验收。没有照报告开放 ADB、切换模拟器、改动 Clash、部署新云端口或替换现有媒体服务。

## 结论

可参考其硬件编解码、Surface 输出、UDP 实时媒体、原生多点输入、FEC 与有界恢复方向。不能接受其最终架构是最低延迟的定论，也不能把文中的固定毫秒数、商业产品内部阈值或平台帧率矩阵当实测。当前用户明确要求实时视频和声音走 UDP、推荐手机缓冲最多 80 ms；标准 Scrcpy 经 Tailscale 的 TCP 流不满足前者。

## 值得落实的建议

1. 保持无 B 帧、短队列、明确期限与拥塞反馈；过期媒体不通过无限重传拖住新画面。FEC 有额外带宽成本，恢复能力取决于块大小、突发丢包与期限，不能用一个平均丢包百分比保证恢复。
2. 对关键帧突发做编码侧限制与发送调度。动态帧内刷新可作为候选，但要验证编码器真正支持、码流兼容及丢失后的恢复，不能仅设参数就宣称解决。既有实验中的大 IDR 丢弃后长时间停发依赖帧，是比换协议名字更具体的修复对象。
3. 使用解码器直接输出 Surface，避免手机像素回读；对源解码、宿主重编码和手机解码分别验证。CPU 虚拟化、GPU 渲染、视频编解码是三条不同的加速链。
4. 复用原生触摸事件模型，保持 pointer ID、DOWN/MOVE/UP/CANCEL、坐标旋转及断线清理。不能把触摸翻译成 Mac 鼠标。

## 关键修正及官方依据

| 报告建议或断言 | 核对与工程含义 |
| --- | --- |
| AVD 的 MediaCodec Surface 自动接入苹果硬件编码 | Surface 是像素输入接口，不证明 codec 后端。scrcpy 的编码器通过 Android MediaCodec 选取；当前环境不能因启用宿主 GPU 就推导为 Apple VideoToolbox。参见 [SurfaceEncoder](https://github.com/Genymobile/scrcpy/blob/master/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java) 与 [MediaCodecInfo](https://developer.android.com/reference/android/media/MediaCodecInfo#isHardwareAccelerated())。本工程继续明确使用宿主 VideoToolbox 重编码，并分别审计源解码。 |
| Scrcpy＋Tailscale 是无 TCP 等待的媒体路径 | 官方 scrcpy 视频、音频、控制使用 ADB 隧道内流式 socket，文档也提供 TCP 输出示例。外层 WireGuard UDP 不移除内层 TCP 的可靠有序语义。见 [scrcpy develop](https://github.com/Genymobile/scrcpy/blob/master/doc/develop.md)。可以借鉴控制服务，不把标准 ADB 视频流当最终跨网 UDP 媒体。 |
| Tailscale 能连或 ping 成功即证明 P2P | 需要记录实际 direct／peer relay／DERP 路径。DERP 客户端使用 TCP、TLS、HTTP Upgrade，不能静默作为纯 UDP 媒体回退。见 [connection types](https://tailscale.com/docs/reference/connection-types)、[DERP 源码](https://github.com/tailscale/tailscale/blob/main/derp/derphttp/derphttp_client.go)。 |
| UHID／Touch Events 是同一个原生多指选项 | scrcpy 当前 UHID 选项对应键盘、鼠标、游戏手柄；finger 输入另外由 Controller.injectTouch 使用 SOURCE_TOUCHSCREEN 和 pointer 状态。见 [mouse](https://github.com/Genymobile/scrcpy/blob/master/doc/mouse.md)、[Controller](https://github.com/Genymobile/scrcpy/blob/master/server/src/main/java/com/genymobile/scrcpy/control/Controller.java)、[PointersState](https://github.com/Genymobile/scrcpy/blob/master/server/src/main/java/com/genymobile/scrcpy/control/PointersState.java)。 |
| QUIC、KCP 或省掉 SDP 就自然消除实时抖动 | 协商开销与持续媒体排队分开测量。QUIC 可靠 STREAM 和不可靠 DATAGRAM 不是同一种语义；后者才直接对应本工程的媒体数据报要求，仍受拥塞控制与调度限制。见 [RFC 9221](https://www.rfc-editor.org/rfc/rfc9221.html)。 |
| gfxstream 对 Metal 是 GPU 直通，因而 AVD 必定稳定 60 FPS | 图形 API 转发／转换不等于独占物理 GPU 直通。官方区分 VM 与图形加速配置，参见 [emulator acceleration](https://developer.android.com/studio/run/emulator-acceleration)。真实源呈现节奏、捕获、编码、网络和手机上屏都仍需单独测量。 |
| 网易内部 UDP+、码率 50–500 Mbps、按 60/100 ms 自动切模式、锁定 58 ms 等细节 | 本次未找到官方直接支持这些具体技术断言的来源。成功读取的[网易官网](https://uuyc.163.com/)和[官方开发者产品说明](https://apps.apple.com/cn/app/uu%E8%BF%9C%E7%A8%8B-%E8%BF%9C%E7%A8%8B%E5%8A%9E%E5%85%AC-%E6%B8%B8%E6%88%8F%E4%B8%B2%E6%B5%81/id1642306791)支持 4K／144 帧等产品能力描述；这不是我们的帧率、延迟或线路保证，也不能反推闭源内部实现。 |

报告的毫秒预算可以作为目标清单，不能当实测。它把一处单向网络时间标成 RTT，并未测量源端 VSYNC 等待、播放队列、音频时钟或公网抖动分布。若另设固定 80 ms 播放缓冲，仅该项就超过 50 ms 总预算。这里的“固定播放缓冲”与实验的最大 80 ms 分片组装／帧准入期限不同，不能混用。

## 与当前 M1 实证的对应关系

- 真实蓝色 M 视频 App 的 H.264 源解码会话，已成功读回 VideoToolbox `UsingHardwareAcceleratedVideoDecoder=true`：[审计](evidence/native-iteration-20261001/source-avc-vtb-hardware-audit.json)。一次会话读取不保证以后每次会话或每一帧都不回退。
- 真实 YouTube、一加 15、M1 局域网的独立原生 UDP 视频探针，8 Mbps 有效样本源端 Surface 活跃呈现 cadence **55.569 FPS**，手机 **54.601 FPS**。窗口长短及启动等待不同，完整窗口平均分别 54.817／45.669，不能宣称稳定全程 60 FPS：[分析](evidence/native-iteration-20261001/udp-24M-deadline-investigation.md)。不是合成视频，但也不是公网、音视频或 V50 验收。
- 先前高码率 UDP 样本的长停顿，已定位到关键帧超过发送期限后，发送端保护依赖链而连续停发；它不是已证明的旧 TCP App 卡顿原因。用户已要求停止 24 Mbps 后续试验，当前不扩大高码率测试。
- [独立 ICE UDP 后端](../experiments/moonlight-v2/transport/ice/README.md)已构建 Mac／Android arm64 C 库，完成同机双 peer UDP 收发与 Mac 物理接口约束读回。尚无 JNI／APK 接入、手机、真实 NAT 打洞、STUN／TURN、中继、音视频或国内公网来源验收。不能把同机微秒结果当跨城延迟。
- M1 保留 6 核、16 GiB、物理 720×1280、Vulkan、源 VTB 开关、headless 启动；网关 idle=0，在线可用。现有已安装 App 的正式媒体仍是 TLS TCP；独立 UDP 视频探针并不意味着整套 App 已替换。

## 后续实验顺序

先稳定真实视频源的供帧与时间戳，再修关键帧超预算的快速恢复与发送侧拥塞反馈；在已有硬编／硬解 UDP 视频组件上接入 UDP 音频、统一音画时钟和原生触控。随后做手机至 M1 的真实 ICE 直连与 UDP 中继对照，并从云端验证物理国内来源。所有结果保留真实视频、链路类型、请求／源／收到／实际呈现 FPS、间隔长尾、丢帧和音画差，遵守最多 80 ms 的推荐手机缓冲边界。
