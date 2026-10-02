# 源端长帧间隔：可区分的因果测量方案

日期：2026-10-01。本文和新工具只记录源端元数据；没有启动播放器、修改清晰度、操作设备、创建并发截图订阅或改动已有采集脚本。下一轮由 root 运行真实 UDP + Perfetto 对照，本文不将尚未采集的数据写成结果。

## 已有证据把问题缩小到哪里

`evidence/surface-capture-20261001/capture-only-02.json` 的35秒采样有1902幅截图、54.337 FPS，最大 gRPC 返回间隔169.178ms、截图生成估计PTS间隔168.707ms，右帧截图年龄2.908ms。长间隔处序号550→551连续；整段没有重复、跳号或reset。以首幅gRPC返回为起点的独立宿主 `[5,30)` 窗口有1352帧，即54.08 FPS。并行源SF整段为53.277 FPS、cadence54.211 FPS，最大169.078ms；两者时间域未显式联结，不能直接将两个最大间隔说成同一帧。

该样本已经移除了VT串流编码、音频和手机媒体，但**仍保留gRPC截图与RGBA拷贝/潜在GPU回读**。它证明长间隔可在没有手机或串流编码时出现，尚不能排除截图回读阻塞模拟器合成，也不能唯一归因给YouTube decoder。

历史有效24M UDP样本来自`evidence/native-iteration-20261001/phone-morphe-avc-vtb-720-udp-24M.json`，与同名源/手机SF文件。该样本是720×1280、24Mbps编码目标、40Mbps短突发预算、80ms整帧准入，并非下一轮4M/32M基线。七个IDR因整帧预算拒绝，698个依赖帧未发送；最后周期摘要能对账`1737=1031+7+1+698`。源SF最大129.498ms，手机最长约8053.937ms；这次主冻结已有发送端断供证据，不能拿它证明源decoder或UDP传输本身长阻塞。暂停输入的`paused-input`样本已标为无效，不混用。详见[历史24M调查](evidence/native-iteration-20261001/udp-24M-deadline-investigation.md)。本轮没有新的2M/24M数据，也不恢复已取消的档位测试。

当前源路径还存在一个重要边界：`ANDROID_EMU_MEDIA_DECODER_VTB=1`已读回，只证明允许VTB；H.264允许软件fallback，VP9在现有Apple emulator实现中使用libvpx。历史codec名和播放器1080p60/720p60 UI都不能证明当前每帧硬解或实际60FPS。参见[已安装模拟器解码路径核查](apple-emulator-decoder-path-20261001.md)。

## 第一组应当怎样测

root计划保留`final-wire32`基线：4Mbps平均编码目标、32Mbps短时发送节奏、60ms手机缓冲、原Surface提交路径。它是实际M1视频经过加密UDP的局域网路径，不是NPS公网、蜂窝、V50或P2P打洞验收。

顺序为A：真实UDP + 唯一现有gRPC截图 + Perfetto/SF；B：同视频播放，仅Perfetto/SF，**无gRPC、无VT串流编码、无手机媒体**。若有明显差异再做B/A复验，以区分时间漂移。这里A/B同时移除了多种宿主负载，首次差异只能归为“远程采集/编码负载相关”，不能直接归为gRPC回读；若需要进一步区分，C使用已有capture-only保留gRPC但没有VT/音频/手机，与B比较。

不先假设虚拟屏120Hz：root最新读回guest min/peak60.0；此前final源SF header16.666ms。每轮应保留实际mode/readback与逐poll header，不把请求值当成持续实际模式。视频会话须前后确认播放、同段、同清晰度；记录当前活跃codec/尺寸，保留“UI档位≠实际decoded FPS”的限制。

首轮只需35～40秒。保留启动0～5秒与稳态5～30秒，采样时先不换播放器、清晰度、codec、队列策略或缓冲。单次计数不能同时作为源码优化和产品发布验收。

## 新的只读三列采样器

新文件[measure_source_frame_fences.py](../scripts/probes/measure_source_frame_fences.py)与旧`measure_surface_cadence.py`独立，没有复制/修改其运行逻辑。

```sh
python3 scripts/probes/measure_source_frame_fences.py \
  --serial emulator-5556 --package app.morphe.android.youtube \
  --seconds 35 --output docs/evidence/source-gap-20261001/source-fences-01.json
```

外部runtime依赖：ADB来自`ANDROID_HOME/platform-tools/adb`，默认`~/Library/Android/sdk/platform-tools/adb`；可选guest clock来自已存在的`/data/local/tmp/remoteandroid-host-control.jar`中的`StreamClock interactive`，**工具不push、不安装该jar**。缺少clock时保留fences，明确`guest_clock_available=false`，不猜偏移。工具只允许本地`emulator-N`。

SF `--latency`三列为desiredPresent、actualPresent、frameReady；最后者可由ready fence解析。新工具保留原数值、valid flag及0/INT64_MAX/负值原因，初始ring独立作为baseline，不计入新呈现。新实际呈现按唯一actual时间去重，仍不能认定为唯一媒体内容；同actual出现不同desired/ready时记录身份冲突，不把它们合成一个可信ready→actual耗时。[AOSP FrameTracker列和fence处理](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/FrameTracker.cpp)

仅选择最初视频层的完整身份，输出identity SHA/generation，定期和最终核对generation；发现改变立即停止并标记partial，不自动选新层。每次SF poll保留host CLOCK_MONOTONIC查询前后括号、每poll period、malformed/invalid数量。15,000个frame records和2048个invalid observations有显式上限，超限记metadata drops；不保存像素、截图、原始dumpsys或无关窗口字符串。

frameReady无有效值时只能说fence信息缺失，不能说decoder没有输出。ready本身也不等于纯视频解码完成：它可能包含producer、GPU和桥接工作的完成信号。

## 显式连接四种时钟

|时间轴|来源|可直接做的差值|需要的连接证据|
|---|---|---|---|
|guest MONOTONIC|SF fence/调度元数据、guest `System.nanoTime`|同guest ready→actual、呈现相邻间隔|Perfetto中同guest的MONOTONIC/BOOTTIME ClockSnapshot|
|guest trace时间|Perfetto解析后`ts`，常由BOOTTIME同步|同trace scheduler/codec/SF事件|查询实际clock域与snapshots，不硬编码默认epoch|
|host CLOCK_MONOTONIC|Python/Swift trace、SF poll/clock query括号|同host gRPC→pipe→VT、query RTT|host MONOTONIC↔host REALTIME成对快照|
|host Unix生成估计|gRPC `timestampUs`|同host screenshot估计PTS间隔|host墙钟快照及其不确定性，不当作guest媒体PTS|

Perfetto用ClockSnapshot把同机器不同clock域转换到trace时间，MONOTONIC与BOOTTIME的偏移可能因suspend而变化；`TracePacket`的clock id、单位及primary trace clock应读回。不同机器的BOOTTIME仍是不同节点，必须有额外cross-machine同步边。[Perfetto clock sync](https://perfetto.dev/docs/concepts/clock-sync)、[时钟字段参考](https://perfetto.dev/docs/reference/trace-packet-proto)、[跨机器同步](https://perfetto.dev/docs/deployment/multi-machine-architecture)

推荐连接步骤：

1. 保留guest Perfetto内MONOTONIC、BOOTTIME、REALTIME同组ClockSnapshot；从实际trace中提取，不根据“看起来接近”补值。SF guestMONOTONIC先映射到guest trace域。
2. 新采样器在host send/receive之间取guest StreamClock读数，得到`guest_to_host_monotonic_offset_bounds_ns`。guest nanoTime以微秒截断，wall为毫秒精度，span也保留；offset区间包含完整ADB RTT，**不假设上下行对称**。开始/结束各三次、中间约五秒一次，可选择最紧区间，但不将最短RTT变成零误差。
3. host CLOCK_MONOTONIC在`time.time_ns()`前后采样。host截图Unix时间通过该bracket映射到hostMONOTONIC，再经步骤2的offset区间关联guest事件，最后通过guest ClockSnapshot进入Perfetto轴。
4. 若使用guest REALTIME作为rendezvous，仍必须实际测量guest REALTIME↔host REALTIME偏移；虚拟机和宿主的“Unix”不能默认为相等。墙钟step、guest suspend、重启或offset区间不相交时，拆段或拒绝映射。
5. 汇总误差包含host快照宽度、guest采样精度/跨度、ADB RTT区间、snapshots分段精度。建议≤5ms才讨论一个刷新周期内的先后；>20ms仅做粗窗口关联。这个阈值是本实验的判读门槛，不是Perfetto精度保证。

即便时钟映射成功，SF没有截图seq或媒体PTS，也不能仅靠最近时间点认定“同一帧”；它只让停顿窗口能正确重叠。精确逐帧归因还需要producer buffer/frame token或显式桥接ID。

gRPC官方把`timestampUs`定义为模拟器估计的Unix生成时间，早于拷贝/转换；`seq`允许跳号。[官方截图协议](https://android.googlesource.com/platform/tools/base/+/refs/heads/mirror-goog-studio-main/emulator/proto/emulator_controller.proto) root已在新`capture_enqueue`中增加`screenshot_seq=int(image.seq)`及分析delta；历史硬件trace只有本地`capture_seq`，该字段为unknown，不能倒推出旧stream跳号。capture-only已有真实`image.seq`。跳号是截图流遗漏，不是Internet丢包。

## Perfetto需证明哪一环首先断供

先核对当前设备实际支持的数据源和category，再使用typed config；不能把category未启用或trace缺失当成没有事件。最低数据为scheduler switch/wakeup/waking、目标Morphe与media codec线程、SurfaceFlinger/RenderEngine/HWC、video/gfx/view注释、binder交易；需要时加freq/idle和有限process stats。`video` category可产生可解析的codec事件，但应先确认当前build有这些track，不假设所有C2版本都有相同事件名。[ATrace配置](https://perfetto.dev/docs/getting-started/atrace)、[android.codec标准模块](https://perfetto.dev/docs/analysis/stdlib-docs#android-codec)

FrameTimeline可补充SF/display deadline和token，但官方文档提示SurfaceView覆盖限制；Morphe视频层没有app FrameTimeline行不能视作“播放器无jank”。三列video层fence仍独立保留。[FrameTimeline边界](https://perfetto.dev/docs/data-sources/frametimeline)

|候选位置|足以支持的观测组合|仍不能据此断言的事|
|---|---|---|
|播放器/下载输入断供|当前活跃codec的输入/queued work间隔先拉长，接着输出、ready、actual、截图均停；缓冲/网络事件同窗|不能只凭SF gap认定decoder太慢；也可能是压缩输入未送到|
|decoder或guest→host解码桥接|输入按时持续，已入队work的完成/输出晚；ready晚跟随，codec线程等待链或宿主decoder活动同窗|guest C2 wall span包含桥接/排队；不等于苹果单帧解码计算耗时|
|播放器渲染/应用调度|codec输出及时，但release/queueBuffer晚；app线程长runnable等待或锁/binder等待|不是仅凭总CPU低就排除调度；缺少output事件时只能未知|
|SF合成/显示调度|视频buffer ready及acquire已及时，actual晚；SF/HWC deadline或fence等待明确拖后|ready fence可能缺失；FrameTimeline颜色/标签单独不能证明具体GPU执行阻塞|
|宿主模拟器/回读/截图回调|映射后的guest actual仍正常，而截图生成或返回暂停；或B无gRPC时源ready/actual恢复，C加gRPC复现|A/B移除多种负载只证明负载相关；若host停顿同时拖住虚拟HWC，guestSF与截图都会停，不能只把其中一端当起因|
|gRPC交付/collector排队|估计生成PTS持续但返回gap/年龄长、真实seq跳号；host采集/线程调度证据对应|源码seq连续也不能证明原视频每帧都存在；生成时间是估计，不是解码PTS|
|VT串流编码之后|源ready/actual/截图供应正常，明确某个pipe/VT/output/packetizer阶段先长阻塞|本轮已有VT常态短耗时；不能用手机个位数FPS倒推苹果硬件失效|

最有价值的是每个>50ms源gap前后至少200ms的事件链：codec输入/输出、queueBuffer/ready、actual、截图估计生成/返回、VT阶段。同一段必须记录其clock映射区间和观测完整性。

若guest trace显示codec等待宿主而看不到后端执行，下一步才做现有emulator精确PID的短有界调用栈/线程采样或后端instrumentation，并记录采样开销；只保留白名单symbol计数，不保存含credentials的完整命令行。H.264需在活跃VT session读`UsingHardwareAcceleratedVideoDecoder`、submit/callback/fallback事件；VP9需当前libvpx decode活动证据。这些仍是下一层证明要求，不是本次已完成读回。

## 本轮结束时的报告标准

按同一显式映射的稳态窗口报告：源codec输入/输出计数（若可用）、valid ready数/间隔、ready→actual分布、actual SF间隔、截图seq缺口/PTS与gRPC间隔、VT阶段、手机实际SF及路径。窗口边界冻结也要保留原gap和与窗口的重叠长度，不能只统计两个端点都落在窗口中的gap。

另列：invalid fence数、layer generation变化、trace dropped事件、native final summary缺失、clock映射宽度/step、FrameTimeline/codec category缺失。无法观测的环节写unknown；不把“没有日志”当成零耗时或零丢帧。得到可重复的第一断供位置后，再改一个参数或环节并重复A/B验证。

## 纯源码验证

`tests/test_source_frame_fences.py`目前13项检查覆盖三列顺序、invalid哨兵、malformed/out-of-range、ring上限、精确layer generation、baseline去重、ready补全、metadata上限、同actual身份冲突、clock RTT/精度界限以及partial pipe回复的有界超时。Python编译检查通过；没有进行设备验收或启动任何真实采集。
