# M1 真实视频 Buffer 身份、Latch 与 Present 追踪

日期：2026-10-01。本轮新进展是按同一 buffer / frame 身份关联 SF 接收事务、Latch 与 Present 记录，而非依靠两个事件的时间接近。真实视频源仍为 M1 的 Morphe YouTube，独立手机串流仍为加密 UDP 实验组件。

**当前没有证明稳定60FPS或120FPS。** 有效一加15 Wi-Fi实测35秒，稳态 `[5,30)` 显示57.84FPS、最长99.59ms、超过100ms空档0次。源端轻量追踪的一处97.64ms Present空档中，下一帧从SF接收事务到Latch为85.67ms，Latch到Present记录为2.91ms；等待集中在Latch之前，但尚不能分解为buffer readiness、目标呈现时间、backpressure、SF或宿主调度。

## 环境与实验边界

- M1 `RemoteAndroid17Compare` / `emulator-5556`：6核、16GiB、物理720×1280、320dpi，host GPU，源App为Skia Vulkan。保留原AVD `hw.lcd.vsync=120`、现有guest min/peak60设置、默认HWC合成及供电常亮。
- 固定公开视频BBB，请求URL seek60s。每轮只force-stop并重开源App，保留数据，预热8秒，前后媒体会话state3/speed1。请求seek、UI选项与媒体会话位置均不代替逐帧PTS。
- 三轮source-only无gRPC capture订阅、无串流编码、无手机视频传输。各轮同时采连续SF三列记录；截图没有用于估算FPS。
- 另做35秒一加15真实视频验收：M1有线en7 `.128` → 家庭局域网 → 一加15 Wi-Fi `.6`；视频540×960、60FPS cap、4Mbps VBR、60ms缓冲、手机实际90Hz，Apple硬件编码读回true、手机 `c2.qti.avc.decoder.low_latency` / hardware=true。
- UDP wire pacing ceiling32Mbps是关键帧瞬时发送预算，平均编码目标仍4Mbps；没有恢复24/40Mbps产品档位。本轮无公网NPS、Tailscale路由、手机流量或远程V50验收，也没有声学A/V同步或触控到光子测量。

## 从重型快照改到轻量帧事件

| 采样 | 观测工具 | 覆盖和完整性 | 源SF `[5,30)` | 窗内最大间隔 |
| --- | --- | --- | ---: | ---: |
| source-only-01 | video/sched + ACTIVE layers/transactions，64MiB cap | trace25.961s，共同窗24.819s，接近cap；零loss统计也不完整 | 56.04FPS | 120.78ms |
| source-only-02 | 同上，128MiB cap | trace29.998s，共同窗29.993s，未近cap；packet loss group1项 | 53.00FPS | 145.50ms |
| source-frames-03 | video/sched + `android.surfaceflinger.frame`，不采完整layers/transactions | 12组clock snapshot跨度30.001s，文件12.64MB，loss/overrun0；仍有instrumentation边界 | 57.28FPS | 97.64ms |

这些是顺序真实样本，观察开销不同，不能当作无扰动的ABA性能提升证明。第一轮成功复制不代表完整30秒，第二轮覆盖30秒不代表无损；第三轮的trace bounds30.175s含约160ms历史Dequeue时间，不能只用bounds自证采集时长。

轻量源注册已经实测确认。官方[AOSP FrameTracer](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/FrameTracer.cpp)采用DataSource Trace，不要求增加gfx分类。每个实际事件必须按固定视频SurfaceView核对；源存在不等于该层会导出所有阶段。

## 身份关联和实测阶段

第三轮目标视频层实际采到：Dequeue1800、SF receipt Queue1800、Latch1722、Present1716、Acquire0。按同machine、buffer track、buffer低32位、frame低32位构建1800个identity，1716组有唯一Q/L/P；未观察到reset、duplicate或跨track低32位碰撞。APP/GPU/SF/Display派生轨道不当成额外真实帧事件。

| 同identity时间区间 | p50 | p95 | 最大 |
| --- | ---: | ---: | ---: |
| SF接收事务Queue → Latch记录 | 41.064ms | 49.789ms | 87.047ms |
| Latch → SF Present记录 | 1.646ms | 6.572ms | 26.803ms |
| SF接收事务Queue → Present记录 | 43.339ms | 52.884ms | 92.736ms |

这里Queue是SF接收事务的采样点，不能叫producer queueBuffer或解码器释放时刻。Latch是SF本轮latch阶段的批次时间，不能叫每个buffer完成的GPU时间。PresentFenceSignaled是trace事件名；AOSP无有效present fence时也可能记录HWC估算timestamp，当前guest具体分支未二进制确认。表格不是手机显示、声学同步或端到端操作延时。

[AOSP Layer.cpp](https://android.googlesource.com/platform/frameworks/native/+/ec3ef50f1dbc57d3e57ae81ec26c9e7ed8735034/services/surfaceflinger/Layer.cpp)提供Latch与Present记录路径；[SurfaceFlinger.cpp](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/SurfaceFlinger.cpp)的timeline/buffer readiness检查还包含desiredPresentTime、autoTimestamp、cadence、barrier、backpressure及acquire readiness。公开参考源码尚未与当前guest精确build逐字匹配。

最大的SF Present记录空档为97.640459ms，左右frame1126→1129。右frame1129 Queue→Latch85.666958ms，Latch→Present2.910084ms。1127/1128均只有Queue而没有Latch/Present观测；没有观测到不直接称为物理掉帧或网络丢包。该窗Queue有3个marker，但包含87.214167ms没有新Queue marker的子窗，Latch只有1个marker，不能称整窗buffer连续上交。

同一pre-latch85.667ms窗口的guest SF主线程状态：Running1.901ms、Runnable1.065ms、Sleeping82.701ms，最长单段Sleeping31.864ms。它没有显示主线程85ms等待guest CPU调度。Sleeping只是guest最后记录的可中断睡眠状态，仍可能包含宿主没有调度vCPU的时间；没有host调度数据，不能据此证明是某个kernel fence、宿主CPU充足或虚拟机未暂停。

第二轮重型trace最大145.498ms空档的同窗有SF线程长Runnable约86.8ms，但第三轮轻量trace并不复现这一状态，且第二轮还有packet loss1。不能把重型观测的负担直接外推成生产环境CPU不足，更不能因此自行增加固定6核/16GiB配置。

## 仍缺少的身份和事件

- 源视频Acquire0；当前路径可能没有有效acquire fence或未导出。它不能证明buffer立即ready。其他层的Acquire事件不能借给视频层。三列latency中的ready也不一定是原始acquire fence：现代FrameTimeline可取queue时间或与其max值，未确认guest分支前不能用ready列填补缺失Acquire。见[AOSP FrameTimeline](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/FrameTimeline/FrameTimeline.cpp)。
- CCodec `renderOutputBuffer` scopes不带PTS，没有args/flow桥，尚不能把解码输出PTS与Surface buffer逐帧一一对应。第三轮1794个唯一H.264输出PTS均按16666/16667µs递增，支持60FPS媒体时间线；这不证明实时呈现60FPS。
- 当前layers proto `curr_frame`可以是frontend requested buffer state。layers snapshot与transaction commit共享timestamp，观察差0不代表Latch/显示零延时。[AOSP LayerProtoHelper](https://android.googlesource.com/platform/frameworks/native/+/d5678111f9/services/surfaceflinger/LayerProtoHelper.cpp)已核对此现代语义。
- 两轮post→frontend snapshot约41–43ms的中位原始时钟差，仅描述SF事务接收后到状态观察；当前guest post_time clock origin未精确build验证，不作解码/展示延迟。
- v58.2 `graphics_frame_event_parser_errors=1745`恰对应全trace Present事件数。该[官方parser](https://raw.githubusercontent.com/google/perfetto/v58.2/src/trace_processor/importers/proto/graphics_frame_event_parser.cc)会对这类已保留事件计diagnostic，分析器将它与packet loss/overrun分开，不能解释为1745次丢帧。
- buffer与frame字段只有低32位，observed uniqueness不证明绝无潜在碰撞；重置、重复与可观察到的跨track碰撞拒绝关联。

## 真实手机复验及失败记录

有效35秒样本见 [UDP确认](evidence/source-buffer-20261001/udp-confirmation-validated/matrix.json)：稳态57.84FPS，p99显示间隔44.26ms，最大99.59ms，>100ms空档0；整个source采样仍有一次103.76ms间隔。源与手机的统计窗口起点不同，不作逐帧配对。

该轮Apple VT readback `using_hardware=true`，手机hardware decoder=true；FEC recovered shards0、frames expired0、reference lost0，receiver loop无>80ms，decoder input timeout0。整轮video inbox出现1次overflow并恢复，不能称完全无丢弃。音频有写入且无worker failure，但存在late drops，未测声学同步。Native末尾summary `final=false`，不能据此保证完整发送诊断历史。

先前两轮尝试也保留：

1. `udp-confirmation`：手机Wi-Fi关闭，未收到READY，性能不可用。按用户Wi-Fi优先要求开启后确认 `.6`。
2. `udp-confirmation-wifi`：握手成功但临时旧packetizer不支持当前启动参数，media发送0，性能不可用。未把它当成网络掉帧。有效轮改用上一轮已验证的 `huoguo-udp-paced-credit/host/h264_udp_packetizer`，SHA256 `10aa36d3d159c5290df2e7f0ccc767271e979a7148552450bc0da511104d4b0f`。

## 工具、证据与下一步

- [Perfetto helper](../scripts/probes/guest_perfetto_probe.md)：新增显式 `--surface-buffers` / `--graphics-frames`，普通64MiB、重型128MiB cap；copy、cap警示和实际duration coverage分开。
- [buffer分析器](../scripts/probes/analyze_surface_buffer_trace.py)：固定数值字段、历史排除、共同覆盖窗、精确transaction元数据及frame identity。
- [graphics分析器](../scripts/probes/analyze_graphics_frame_trace.py)：只接受固定真实事件、排除派生阶段、拒绝identity歧义，输出Q/L/P及缺失stage、最大gap身份。
- [三轮数值对照](evidence/source-buffer-20261001/source-comparison.json)、[完整graphics数据](evidence/source-buffer-20261001/source-frames-03/graphics-analysis.json)、[最长空档scheduler数据](evidence/source-buffer-20261001/source-frames-03/longest-gap-scheduler.json)。

下一项可区分的实验应补同帧的desiredPresentTime/isAutoTimestamp、acquire readiness及宿主vCPU执行时间，先拆开Latch前等待。此轮数据不支持直接把问题归到GPU慢、CPU核数不足，或承诺换QUIC/KCP/NPS就能消除源端卡顿。

14项guest helper、11项buffer parser、6项graphics parser及41项相关source检查通过（合计72项）；另有真实trace与真实UDP手机采样，测试层不混为一谈。原始trace只在owner私有临时目录，仓库仅固定数值和工具。未构建、安装或发布正式App，未改M5/NPS/Clash/生产账号。

收尾读回：[final-health.json](evidence/source-buffer-20261001/final-health.json)。AVD config SHA256与上一轮原配置完全一致；guest tracing_on=0、atrace flags0、无本轮实验worker，网关和下载页均HTTP200。手机仍为正式App1.29，本轮Wi-Fi已开启，min/peak刷新设置保持null/165.0。实验环境保留。
