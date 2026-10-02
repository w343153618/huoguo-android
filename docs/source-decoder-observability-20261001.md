# 源视频解码与帧交付的可观测性（2026-10-01）

## 本次只读结果

检查对象为 M1 上的 `emulator-5556`，源视频 App 为 `app.morphe.android.youtube`。没有操作 App UI、切换视频清晰度、修改虚拟机设置、清空日志或重启服务。原始 `logcat`、资源管理器和媒体指标输出只在内存中解析，未保存。

| 观察 | 本次证据 | 能说明什么 |
| --- | --- | --- |
| 源 App 进程 | `pidof` 为 `3440` | 可将当前分配的媒体组件与源 App 对应 |
| 当前分配的视频组件 | `media.resource_manager` 的当前 Processes 段中，PID `3440` 的 client 名为 `c2.goldfish.h264.decoder` | 当前分配的是 H.264/AVC 组件，不是 VP9 组件 |
| 资源类别 | 同一当前 client 中出现 `non-secure-codec/hw-video-codec` | Android 资源管理器将该组件列为硬件视频组件；不能单独证明宿主 Apple VideoToolbox 没有进入软件回退 |
| 媒体服务 | 有 `android.hardware.media.c2.IComponentStore/default`、`.../software`、`media.resource_manager`、`media.metrics`、`SurfaceFlinger`，无旧 `media.codec` Binder 名称 | 应使用 Codec2 和 SurfaceFlinger 的观察路径 |
| 媒体进程 | PID `469` 为 `android.hardware.media.c2-service-goldfish`；PID `585` 为 `media.swcodec` | 进程存在不等于此视频使用该进程解码，尤其不能由 `media.swcodec` 存在推断当前是软件解码 |

资源管理器只分析 `Process Pid override`、Events 和 Metrics 日志之前的当前 Processes 段，避免把历史 client 当成当前组件。上述证据证明了组件分配，尚未证明每帧正在解码、解码速度或当前输入格式。

本次有界 App PID 日志中最近的 Codec2 记录为 guest monotonic `21770.529 s`，最近格式记录为 `21770.462 s`；读取 `/proc/uptime` 时为 `23970.88 s`。相差约 `2200 s`（36.7 分钟），因此日志中的 `1920 × 1080`、`color-format=2130708361` 是**历史配置**，不能作为当前真实播放格式。更早记录也出现 `1280 × 720`。`video/raw` 是解码器输出 MIME，不能代替编码输入 MIME。源 App UI 的 1080p60 选择也只是请求，不足以证明正在交付 60 个独立视频帧。

`media.metrics` 是历史聚合指标。此次没有把其中的记录解释为当前播放帧率或队列健康度，后续应优先使用与真实视频测试同窗的逐帧轨迹。

## 可以采集的逐帧链路

官方 AOSP 的 [`CCodecBufferChannel.cpp`](https://android.googlesource.com/platform/frameworks/av/+/master/media/codec2/sfplugin/CCodecBufferChannel.cpp) 使用 `ATRACE_TAG_VIDEO`，包含以下名称。它们是采集与查询的候选 schema；必须先在当前镜像的真实 Perfetto 轨迹中确认名称和数量，不能假定当前二进制与 master 完全一致。

| trace 名称模式 | 阶段与解释 |
| --- | --- |
| `CCodecBufferChannel::queue(c2.goldfish.h264.decoder@ts=PTS)` | 输入交给 Codec2 component；带 PTS 的匹配键 |
| `CCodecBufferChannel::onWorkDone(c2.goldfish.h264.decoder@ts=PTS)` | component work 完成回调；可按组件与 PTS 对应输入 |
| `CCodecBufferChannel::onWorkDone-c2.goldfish.h264.decoder` | 外层 scope，没有 PTS，不能直接用来逐帧配对 |
| `CCodecBufferChannel::handleWork-c2.goldfish.h264.decoder` | 完成结果处理 |
| `CCodecBufferChannel::sendOutputBuffers-c2.goldfish.h264.decoder` | 输出 buffer 分发 |
| `CCodecBufferChannel::renderOutputBuffer-c2.goldfish.h264.decoder` | 解码输出交给 Surface 的路径 |

查询应先列出匹配的 `slice.name`、数量、track 名和所属进程，再按同一 component 与 PTS 关联 `queue → onWorkDone`。一个输入可能对应多个输出，重复 PTS 不应强行合并成一对一。该时长包含框架、服务和排队，不能称为 Apple 芯片的纯解码时长。

下一段关联 `renderOutputBuffer → BufferQueue queueBuffer → 源 App layer 的 SurfaceFlinger latch/present`。需要跟踪同一 Surface/layer、buffer/frame 标识并核实实际字段，不能按“时间最近”随意拼接不同层。BufferQueue 库存、长 dequeue/queue 等待、源 layer 的相邻 present 间隔与重复 buffer，才能帮助区分供帧不足和显示合成延迟。最终远程端的输入、目标提交、回调与真实 SurfaceFlinger 呈现仍是不同指标。

测试期间不要用抓图代替连续采样。建议固定真实视频与时间窗，记录上述阶段的数量、间隔 p50/p95/max 和缺口，并将 Mac 捕获/编码与手机接收/呈现的同窗计数并列。音频 DAC 播放时钟也须单独测量；视频 buffer 时长不能自动视为 A/V 同步结果。

## Apple 硬件解码仍未得到逐帧证明

已读取的官方旧版 [QEMU VideoToolbox helper](https://android.googlesource.com/platform/external/qemu/+/ba29194f97e72ffe770bd56e4e5c5c620598004b/android/android-emu/android/emulation/MediaVideoToolBoxVideoHelper.cpp) 以 EnableHardware 方式创建解码器，允许回退，且 `MEDIA_VTB_DEBUG=0` 是编译期开关。创建 VT session 成功不能证明 `UsingHardwareAcceleratedVideoDecoder` 属性为真。这一旧版本仅解释为什么 guest 的 hardware 类别证据不足，**不能据此认定当前 Emulator 二进制正在回退或有相同实现**。

[官方 Emulator 命令行文档](https://developer.android.com/studio/run/emulator-commandline) 的 `-debug`/`ANDROID_VERBOSE` 为启动时配置；没有找到无需重启便可开启上述编译期日志的正规接口。此次不重启、不附加 LLDB、不改变环境变量。优先用当前 Codec2 与 BufferQueue 轨迹验证“哪一段少交付帧”，而不是把历史源码的可能性直接解释成当前瓶颈。

## App 内诊断的辅助价值

[YouTube 官方 Android Stats for nerds 说明](https://support.google.com/youtube/answer/7519898?co=GENIE.Platform%3DAndroid&hl=en-EN) 提供 App 内诊断入口，但 Morphe 是否保留相同功能尚未通过 UI 验证。若后续固定视频测试能打开该浮层，可记录当前 codec、当前/最佳分辨率、视频 dropped frames 与 buffer health 数值作为旁证；不需要记录视频 ID、会话 ID 或账号内容。

App 的 dropped frames、源 SurfaceFlinger 呈现次数和远程手机显示 FPS 含义不同。App buffer 充足且 Codec2/SF 供帧不足更支持源端调查；源端连续而远程端缺口更支持捕获、传输或手机呈现调查。此次只读检查还不能给出这一因果判断。
