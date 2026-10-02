# Tailscale 内视频 UDP 与 KCP 评估（2026-09-30）

## 当前链路和限制

当前 App 的视频、音频、控制分别经已认证的 TLS `CONNECT` 建立独立 TCP 连接。M5 的 Tailscale 备选地址用 `tailscale serve --tcp=15556` 转发到回环网关，因此外层 Tailscale 即使通过 UDP 直连，应用视频本身仍按 TCP 字节流交付。M1 测试 Mac 当前 Tailscale 为 `Logged out`，M5 本轮关机；本评估是代码和协议可行性分析，不是已经完成的 UDP 真机测试。

Tailscale 的 WireGuard 数据平面会对直连和中继路径加密，但 UDP 视频服务仍须限定只在 Tailnet 地址/接口监听，并通过 Tailnet 策略只允许授权手机访问。现有用户名、密码和 TLS 会话建立可继续作为应用级授权；服务端仅对已登录会话签发短期随机视频会话令牌。NPS 公网入口保持原 TLS/TCP 协议，不能把 Tailnet 内的 UDP 监听直接暴露到公网。

## KCP 能做什么，不能做什么

KCP 是可靠的 ARQ 算法，不负责底层 UDP 套接字、安全或完整的实时媒体控制。比普通 TCP 更快的重传在某些丢包和 RTT 条件下可能减少等待；项目 README 的百分比是其自身宣传和特定测试结果，不能外推到这里。当前播放缓冲上限为 80 ms；往返、重传、解码和显示若已超过期限，补回旧帧可能增加排队与手感延时。把整个 H.264 视频字节流直接放进单一 KCP 可靠队列，仍会受到依赖旧数据才能交付新数据的影响，不是消除卡顿的充分条件。

若做实验，可把 KCP 作为 **一个候选对照**，并限制发送队列与重传期限；它不应替代源端帧节奏、码率背压和客户端显示诊断。音频和触控暂保留现有独立 TLS/TCP 通道，避免一次改动混淆多个变量。

## 优先实验方案

1. 只对 Tailnet 地址新增独立视频 UDP 端口，保留现有 TLS 会话和 NPS/TCP 回退。不能复用当前 `tailscale serve --tcp` 来宣称视频已走 UDP。
2. 将 VideoToolbox 的每个 H.264 access unit 拆成小于路径 MTU 的 UDP 片段；携带会话号、帧号、时间戳、片号/总数和关键帧标志。接收端只将完整帧送给 MediaCodec；超过 80 ms 播放窗口的片段丢弃，不能把不完整帧交给解码器。
3. 对尚来得及恢复的丢失片段使用限时 NACK 或少量前向纠错；关键帧丢失时通过现有控制通道请求新 IDR。发送端根据丢包、到达延迟和手机显示结果控制码率，不关闭拥塞控制。
4. A/B 比较现有 NPS/TCP、Tailscale/TCP、Tailnet UDP 和 KCP/Tailnet UDP。每组使用同一手机、同一真实视频、同一网络、540P、4 Mbps、60 FPS 上限及不超过 80 ms 的缓冲；记录客机源帧、gRPC 抓帧、硬编、服务端发送、手机收包/重组、解码显示、触控到画面变化及实际 Tailscale `direct`/`relay` 状态。

只有在真实手机与目标线路上，显示间隔和操作延时同时改善、音画同步和断线回退可靠，才把 UDP 设为可选入口。短视频片源本身为 30 FPS 时，传输协议不会生成 60 张不同画面。若 Tailscale 回落到 DERP，或 UDP 丢包恢复超出 80 ms，则自动退回已验证的 TLS/TCP；不要仅凭“UDP”或“KCP”名称宣称加速。

参考主源：[Tailscale 加密](https://tailscale.com/docs/concepts/tailscale-encryption)、[连接类型](https://tailscale.com/docs/reference/connection-types)、[KCP 项目说明](https://github.com/skywind3000/kcp/blob/master/README.en.md)、[H.264 RTP 丢片处理](https://www.rfc-editor.org/info/rfc6184/)、[UDP 拥塞控制指南](https://datatracker.ietf.org/doc/rfc8085/)、[QUIC DATAGRAM](https://www.rfc-editor.org/rfc/rfc9221.html)。
