# 60 Hz / 60 FPS 提议与音画同步排查

**可试的基线：**虚拟安卓的应用渲染目标 60 Hz、串流上限 60 FPS、手机缓冲 50–80 ms；维持 540p/4 Mbps 自适应 VBR，然后分别测真实视频与滑动手势。用户已实测 120 ms 操作太滞后，故不把 120 ms 设为默认值。这是一组待手机验证的配置，不是“30 FPS 视频零掉帧”的保证。

Gemini 分开讨论刷新率、视频 FPS 和缓冲的思路是对的，但“120 Hz 与 30 FPS 必然相位冲突”不成立：120/30=4，也是整数倍。Android 的 [Frame Pacing 文档](https://developer.android.com/games/sdk/frame-pacing)明确以 60 FPS 内容在 120 Hz 屏幕上每帧重复两次为例。M1 这台 540×1200 AVD 目前 `peak_refresh_rate=min_refresh_rate=60.0`，`dumpsys display` 同时列出物理 mode 的 120 Hz 和渲染帧率约 60 Hz；仅凭这两个数不能认定 VSYNC 冲突。M1 先前同一个 YouTube Shorts 内容从 120 Hz 上限降至 60 后，手机端平均接收从 22.61 升至 29.56 FPS，说明 60 Hz 值得继续测试，但不是普适定律。

“60 FPS 上限播放 30 FPS 视频时一定只传 30 帧”也不成立。界面叠层、播放器控件、动画、字幕、触摸指示或网关的空闲重复帧都会增加抓取/发送；该上限只保证不超过 60，并不保证实际等于视频源帧率。固定 30 Hz 虽然可能减少抓帧负载，但每次显示机会相隔约 33.3 ms，作为以手势滑动为主的默认方案不合适；是否有收益仍应和 60 Hz 比实际触控到显示延迟。

手机 App 的视频输出使用 `MediaCodec.releaseOutputBuffer(index, renderTimestampNs)`，Android 文档说明 SurfaceView 会在该时间戳后最近的 VSYNC 呈现画面：[MediaCodec API](https://developer.android.com/reference/android/media/MediaCodec)。App 的音频和视频已经通过同一个 `PlaybackClock` 排期，音频不是简单地“收到即播放”。但视频解码晚到时，`videoDeadline()` 可能增加最多 200 ms 的补偿；此前已经写入 `AudioTrack` 的音频无法追溯延迟。音视频又走独立 TLS/TCP 连接，传输及解码排队时长不同，因此用户听到“声音先到、画面慢半拍”是可信的故障现象，尚未在手机端量出具体毫秒数。

隔离 NPS 测试的 M1 真实 YouTube 样本显示，接收端视频与音频相对源 PTS 的中位年龄差在 TCP 一轮约 42 ms、QUIC 一轮约 122 ms；详见 [`evidence/nps-transport-20260930/README.md`](evidence/nps-transport-20260930/README.md)。这些是服务器侧**压缩数据到达**时间，既没有手机硬件解码，也没有屏幕/扬声器实际输出时间，不能直接称为音画不同步量。两轮源端出帧节奏也不同，不能用它们宣称 QUIC 必然使声音领先。

下一步应在手机实际播放时同步记录：视频帧源 PTS、收到时刻、`OnFrameRenderedListener` 的呈现时刻；音频源 PTS、写入 AudioTrack 的时刻、`AudioTrack.getTimestamp()` 报告的播放帧位置；再计算同一源 PTS 在屏幕和扬声器的偏差，并区分源端、网络、解码和输出排队。若网络视频晚到，要优先修正突发和掉帧/码率策略；单纯把缓冲提高到 120 ms 虽可能遮住现象，却违反操作延迟要求。手机端对比需要手机空闲且实际连接 M1/公网；本轮尚未完成。
