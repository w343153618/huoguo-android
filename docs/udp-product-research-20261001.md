# UU 远程、掌上 MuMu 与原生安卓串流研究

核验日期：2026-10-01。目标设备为 M1 上的 Google Android Emulator，真实手机为控制端，优先原生触屏和真实视频稳定播放，推荐播放缓冲上限 80 ms。

## 官方资料说明了什么

[网易 UU 远程官网](https://uuyc.163.com/)公开介绍高速直连、多点触控及高帧率显示；[掌上 MuMu 官方产品页](https://mumu.163.com/product/pocketmumu/index.html)介绍手机远程安卓设备，并明确手机触控可同步至多个安卓设备。网页原始 HTML 已实际读取。掌上 MuMu 的目标交互尤其贴近本项目。

没有在上述已检查的公开官方资料中找到具体 UDP 协议、拥塞控制算法、FEC 参数、打洞预测算法或经独立验证的 NAT 成功率。因此“它们用了 QUIC/KCP/某个自研算法”和“打洞必定成功”都不能作为事实。搜索结果中大量相似域名不是本次研究依据。

可借鉴的是产品行为：用户选择设备即可连接、优先低延迟直连、自动处理网络变化、触摸直接作用于安卓、低排队的音画播放；具体实现使用可审阅的开源协议与我们自己的测量。

## 推荐实现

### 分离媒体与控制

- 视频使用 RTP/UDP，加帧序号、媒体时间戳、FEC 和有限的丢片恢复，配合发包节奏控制及拥塞反馈。不能为了等一张已经过期的画面拖住后面所有画面。
- 控制使用经过鉴权的可靠通道。触摸 DOWN/UP/CANCEL 不能丢，MOVE 可以合并为最近位置但必须保持手指生命周期；每个手指保留独立 ID。多点触控不走 macOS 鼠标 API。
- 音频考虑 Opus、小帧及有上限的抖动缓冲。以输出设备真实播放头估计声音播放时间，使声音和视频对应同一媒体时钟。固定提前声音 50 ms 不适合所有手机。
- Mac 使用 VideoToolbox 编码，颜色转换/缩放评估 Metal；手机使用 MediaCodec 硬件解码和 Surface 定时显示。编码器由专用媒体引擎工作，不能把 CPU、GPU、媒体引擎混称“全部 GPU 加速”。

Moonlight 的参考源码包括 [视频接收](https://github.com/moonlight-stream/moonlight-common-c/blob/f900dd4767759c7b9d0e93bcea666b55c69ea62f/src/VideoStream.c)、[视频排队及纠错](https://github.com/moonlight-stream/moonlight-common-c/blob/f900dd4767759c7b9d0e93bcea666b55c69ea62f/src/RtpVideoQueue.c)、[原生触摸接口](https://github.com/moonlight-stream/moonlight-common-c/blob/f900dd4767759c7b9d0e93bcea666b55c69ea62f/src/Limelight.h)和 [Android 解码呈现](https://github.com/moonlight-stream/moonlight-android/blob/b48494cb96bff23d8886c4775cc4f39a1075495d/app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java)。这些组件不等于已适配 Google 模拟器；我们仍需窗口捕获与安卓输入后端。

### 智能找路及切换

第一阶段复用已有 Headscale/Tailscale 的穿透与加密，先把媒体本身改成 UDP。若媒体仍是 TCP，把外层换成 Tailscale 不会消除内层顺序交付等待。

后续内置传输按以下行为设计，不要求用户安装额外 VPN 客户端：

1. 认证后并行收集局域网、IPv6、STUN 观测地址、允许的端口映射及国内中转候选。
2. 在实际媒体使用的 UDP socket 上探测，双方协调发包；以真实双向探测成功为依据，不凭“发现公网地址”认定直连。
3. 候选就绪即开始播放；中转可先提供画面，后台继续尝试直连。
4. 综合 RTT、p95 到达抖动、丢包和估计容量选择路径。直连更差时允许保留中转；切换设稳定窗口和滞回，避免反复跳线。
5. Wi-Fi/移动网络或 M1 有线/Wi-Fi 变化后重新探测、更新地址，尽量保持会话和用户配置。
6. 失败回退顺序：经过验证的 UDP 直连 → 国内 UDP 或 QUIC DATAGRAM 中转 → NPS TCP 兼容路径。UDP 被封锁或 NAT 无法穿透时中转是必要功能。

[ICE 标准 RFC 8445](https://www.rfc-editor.org/info/rfc8445/)给出候选与连接检查机制；[Tailscale 穿透说明](https://tailscale.com/blog/how-nat-traversal-works)展示了同一 socket、端点发现及困难 NAT 的问题；[连接类型文档](https://tailscale.com/docs/reference/connection-types)区分直连与中继。不把 Tailscale 的实现冒称为完整 ICE 实现，也不把所有 DERP 路径称为 UDP 中转。

家中爱快拨号获得内网地址不意味着所有 UDP P2P 都不可能；双方映射/过滤行为、多层 NAT 和运营商策略共同决定结果。两端均有困难的端点相关映射时不能承诺打通。不能凭用户所述 NAT1 标签给出成功率。

[QUIC DATAGRAM RFC 9221](https://www.rfc-editor.org/rfc/rfc9221.html)允许不可靠数据报；普通 QUIC stream 仍是可靠有序流。KCP 也采用 ARQ；把实时媒体全部变成可靠顺序交付仍可能等重传。应按画面播放期限决定是否值得恢复，并保护 H.264 参考依赖，不能随意丢压缩帧。

### 国内直出边界

所有腾讯控制、中转、穿透服务的连接都固定走允许的物理网卡或已验证 DIRECT 路由，拒绝代理回退；STUN 必须观察实际媒体出口。安卓访问 YouTube 的代理出口与腾讯中转出口分开。云端继续保留原国内 IP 访问规则。

2026-10-01 实际恢复检查：物理 en7 的公网 /ping 返回 M1、emulator-5556、videotoolbox；云端收到来源 180.111.118.161，且该地址命中其现有 cn4 集合。这个证据仅说明本次访问和 NPC 连接，不能代表未来所有网络变更后的出口，需持续核验。

## 本次组件成果

代码位于 ../experiments/moonlight-v2/，固定上游提交并通过两项 CTest。原生 C++ 触控适配器已在 M1 安卓中实收双指 SOURCE_TOUCHSCREEN；纠错库通过 66 种双丢失恢复测试。还没有新的完整手机串流 App，也未完成本方案的 UDP 公网、移动网络、V50 或 60/120 FPS 验收。

下一项最有判别力的测量，是比较捕获源端唯一帧率与手机显示帧率。既有真实视频测试中捕获供帧约 25–30 FPS，不能靠替换传输协议获得真实 60/120 个不同视频帧。独立验收 60 FPS 视频源与 60 FPS 界面动画，然后才考察 120 FPS 选项。
