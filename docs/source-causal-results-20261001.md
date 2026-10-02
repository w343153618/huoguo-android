# 真实视频源端调试与合成路径对照

日期：2026-10-01。当前对象为 M1 的 `RemoteAndroid17Compare / emulator-5556` 和 USB 连接的一加 15。媒体经 M1 有线接口、家中 Wi-Fi、认证加密 UDP 传输；USB 用于控制和读取。本文不代表 NPS 公网、蜂窝、远程 V50、完整新版 App 或 120FPS 验收。

## 当前结论

源端呈现空档已经在**没有 gRPC 抓屏、没有串流编码、没有手机媒体**的真实 YouTube 播放中复现。因此这些空档不能只归因于手机硬解、TCP/NPS 或 Mac 串流编码器。另一方面，顺序样本中带串流的一轮反而更好，不能用这一组数据证明抓屏负载完全没有影响。

视频解码输出交给 Surface 的调用在部分长呈现空档里仍持续进入并返回。它将重点缩小到播放器缓冲交付、BufferQueue、SurfaceFlinger/模拟 HWC 的调度和宿主执行；**尚未取得媒体 PTS 到 SF buffer 的逐帧身份连接，不能唯一归因给 GPU、HWC 或 CPU**。

本轮没有达到稳定 60FPS，更没有证明真实视频稳定 120FPS。强制物理 60Hz 和强制 GPU client composition 都没有得到足够可靠的提升证据，已恢复原 AVD 配置与合成开关。

## 已启用和验证的调试能力

开发者选项和 USB 调试均已开启。使用当前镜像实际支持的 Perfetto、video atrace、scheduler 和 FrameTimeline 数据源采集连续时间轨迹；SurfaceFlinger TimeStats 辅助统计源视频层的 droppedFrames、呈现直方图与合成路径。没有清空历史计数，没有用逐张截图计算 FPS。

原始轨迹仅留在私有临时目录，目录 0700、文件 0600。仓库保存白名单数值及工具源码。每次采集有时长、大小限制，结束后清理本轮 guest 文件与进程；重型 gfx/view trace 的观察开销已单独标注，后续优先使用 video 分类。

官方 [FrameTimeline 文档](https://perfetto.dev/docs/data-sources/frametimeline) 提示 SurfaceView 覆盖限制。当前真实 trace 的 YouTube 视频 SurfaceView 没有对应 timeline 行，不能把它解释成零视频 jank。HWUI/gfxinfo 的 App UI 卡顿也不能代替视频层卡顿。

## 连续解码、源端呈现和串流编码

固定公开视频使用蓝色 M 的 Morphe YouTube，请求打开 BBB 60FPS 版本并 seek60s；播放器 UI 的清晰度选择和请求 seek 均不等于已验证的真实媒体位置。后续实际解码输出 PTS 的步进用于复核源帧率。

| 条件 | 源 SF `[5,30)` 呈现率 | 源窗口最大间隔 | 手机 `[5,30)` 呈现率 |
| --- | ---: | ---: | ---: |
| source-only-03：无抓屏/编码/手机，轻量 video trace | 54.28FPS | 138.70ms | 不适用 |
| udp-02：真实视频 + 抓屏 + Apple 编码 + UDP + 手机 | 59.00FPS | 94.03ms | 58.80FPS |

SF 与手机窗口各自起点不同，表格不是逐帧端到端配对。顺序播放、内容变化与 tracing 开销限制了因果解释；不能说增加串流反而让源端更快。

`source-only-03` 的 H.264 CCodec 同 instance 观察到 2,692 次 renderOutputBuffer，全部为闭合 scope；方法最大耗时 14.378ms，退出间隔最大 69.962ms。源 SF 三个超过 100ms 的空档中，render 方法的进入/退出分别仍为 7/7、8/8、8/8。调用数不是独立视频像素数，也不证明每个 buffer 的 acquire/desired/present 都及时。

同 PTS 的 queue→onWorkDone 中位约 71.5ms，包含异步排队、重排、服务与回调调度，**不能称为苹果单帧纯解码耗时**。onWorkDone 输出 PTS 以约 16.667ms 连续递增；输入 PTS 的回退可能来自 B 帧重排，不自动当作时钟错误。

`udp-02` 的 Mac 编码器读回 `using_hardware=true`，540×960、4Mbps VBR。在稳态，VT 提交→回调 p50 7.121ms、p99 8.292ms、最大 13.993ms；gRPC 返回→编码 socket 完成 p99 19.696ms、最大 35.971ms。这些没有解释源端超过 100ms 的呈现空档，启动首帧的长耗时也没有混入稳态。

两个 gRPC/Swift/Python 进程的宿主单调时钟已核实。新增真实 screenshot seq；`udp-01` 的几个长截图间隔仍 seq+1，并有时间相近的源 SF 空档。时间邻近不是相同 buffer 身份。数值和时钟限制见 [汇总](evidence/source-causal-20261001/source-causal-numeric-summary.json)、[独立 fence 分析](evidence/source-causal-20261001/source-fences-video-category-analysis.json)。

## 物理 120→60→120Hz 对照

原配置读回的是物理 HWC 120Hz、播放时 SF render 60Hz。两者是不同字段；120 与 60 本身不能自动证明存在 VSYNC 冲突。

保持 6 核、16GiB、物理 720×1280、320dpi、host GPU、4Mbps 编码目标、32Mbps UDP wire pacing ceiling、60ms 缓冲、手机实际 90Hz，仅改变 AVD `hw.lcd.vsync` 并重启同一实例，未清除磁盘。

| 物理 HWC / SF render | 手机稳态呈现率 | 最大手机呈现间隔 | 超过 100ms 空档 |
| --- | ---: | ---: | ---: |
| 原 120 / 60 | 56.68FPS | 244.02ms | 2 |
| 临时 60 / 60 | 55.08FPS | 143.82ms | 2 |
| 恢复原 120 / 60 | 57.68FPS | 66.56ms | 0 |

60Hz 没有解决问题；已用私有原文件恢复原 AVD config 的完整字节。首轮热状态与重启后的两轮不同，无法用单次结果断言 120Hz 永远更好。[全部对照及限制](evidence/source-causal-20261001/physical-vsync-phone-comparison.json)。

## 默认 HWC 与强制 GPU client composition

按 AOSP [HardwareOverlaysPreferenceController](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/heads/main/src/com/android/settings/development/HardwareOverlaysPreferenceController.java) 的 transaction 1010 读取、1008 设置。当前 shell UID2000 被拒绝，root 后能读回五个 int，第五项为 disable overlays。不能根据 adb exit0 或 Parcel 的错误文本把权限失败解释成开关关闭。

临时切换均检查开关读回，TimeStats 的 clientCompositionFrames 在 GPU 轮增加约该轮全部合成帧，证明路径确实改变；`finally` 恢复原开关。它是在安卓合成路径之间切换，不代表默认路径没有使用 Mac GPU。

第一组三轮遇到自动播放换为 24FPS 视频，源 frameRate 与 SF render 均读回 24，手机约 23.6–23.9FPS。它保留为源变化证据，**排除出 60FPS 验收**。重新启动测试播放器后，公开 BBB 60FPS 视频恢复；30秒 trace 的 1,779 个唯一输出 PTS 全以 16,666/16,667µs 递增，对应 60FPS 编码时间线。

修正工具为每轮可选择只 force-stop 源测试 App、重开固定公开视频并预热8秒，保留全部 App 数据。后两组读取的 render 均为60Hz；先使用相同的 Skia OpenGL 源 App 路径做六轮对照。

| 路径 | 第一组手机 FPS / 最大间隔 | 复验手机 FPS / 最大间隔 |
| --- | --- | --- |
| 默认，前对照 | 56.88 / 99.81ms | 56.88 / 166.34ms |
| 强制 GPU client composition | 58.60 / 66.54ms | 57.64 / 110.90ms |
| 恢复默认，后对照 | 57.68 / 77.63ms | 57.48 / 88.51ms |

GPU 轮有小幅平均 FPS 差异，但未稳定60，复验仍有超过100ms空档，不能作为确定修复。保留默认合成。32Mbps 是 wire pacing 的短期预算，**编码目标仍为4Mbps**，没有恢复已取消的24/40Mbps档位测试。

TimeStats 的 before/after 包含 runner、预热、采样开销，累积丢帧不能除以35秒冒充严格稳态丢帧率；旧视频层也可能仍保留，必须按同 boot/同 layer hash 对照。[全部三组报告](evidence/source-causal-20261001/composition-phone-comparison.json)。

## 源 App 渲染器与后续观测

重启后实际 `debug.hwui.renderer=skiagl`，gfxinfo 读回 `Pipeline=Skia (OpenGL)`；源 App 页面观察到局部翻转/错位。既有网关环境目标为 `skiavk`，该配置是在新 boot 的串流入口应用，独立 UDP 探针未走该网关。因此 OpenGL 合成对照不能直接当作正式网关 Vulkan 路径的表现。

本轮将源 App 恢复为既有网关目标的 `skiavk` 并只重开播放器，读取 `Pipeline=Skia (Vulkan)`，播放器页面的文字和视频布局恢复正常。相同媒体参数下的35秒 [Vulkan 确认](evidence/source-causal-20261001/vulkan-confirmation/matrix.json) 为手机稳态57.52FPS、最大88.72ms、超过100ms空档0。它修正了本轮观察到的图像布局异常，单轮不能证明解决全部卡顿。物理模拟器 GLES driver 读回 Apple M1 Max/OpenGL ES Translator/Metal；它也不是逐帧 GPU 利用率。

下一层有价值的观测是媒体 PTS→producer buffer/frame number→SurfaceFlinger layer/present 的身份关联，以及同窗宿主 emulator/HWC 等待。现有 renderOutputBuffer 不带 PTS，FrameTimeline 没覆盖视频 SurfaceView；按时间最近连接这几段会伪造证据。完成这一层前，保留“源端合成/交付相关，具体等待位置未知”的结论。

收尾的 `perfetto --query` 已确认当前镜像还注册了 `android.surfaceflinger.layers` 与 `android.surfaceflinger.transactions`。本轮只检查能力，没有采集这两类 trace；下一轮可以先核对其实际 schema 与 buffer/frame 字段，尝试补全交付身份关联。数据源存在本身不等于能观察每个媒体 PTS或能归因GPU等待。

## 工具和验证

- [guest Perfetto](../scripts/probes/guest_perfetto_probe.md)：连续 scheduler/video 与受限数值导出。
- [三列 source fence 探针](../scripts/probes/measure_source_frame_fences.py)、[独立时钟与空档分析](../scripts/probes/analyze_source_fence_gaps.py)。
- [TimeStats 数值快照](../scripts/probes/collect_source_compositor_stats.py)、[FrameTimeline 补充](../scripts/probes/analyze_source_frame_timeline.py)。
- [物理 VSYNC 对照](../scripts/probes/trial_physical_vsync.py)、[可恢复的 client composition 对照](../scripts/probes/trial_client_composition.py)。
- [真实视频矩阵](../scripts/probes/run_surface_hint_matrix.py)：`--restart-source --source-warmup-seconds 8` 用于固定源实验；默认不重启播放器。

本轮相关58项离线检查通过，另有 Python 编译检查。它们验证解析、时钟边界、错误处理、活动源捕获保护及恢复机制；实际手机数据是单独的验证层。未构建/安装/发布新 App，未改 M5、NPS、Clash 或生产账号。

结束时原 AVD config 的 SHA256 与备份完全一致，6核/16GiB/720×1280/320dpi、物理120Hz、默认合成保持。供电常亮配置保留，手机 min/peak 刷新设置仍为 null/165。开发者选项与ADB仍开着；本轮 Perfetto producer 已结束，`tracing_on=0`、atrace flags0，额外 TimeStats 已关闭但未清空计数。实验编码/packetizer 均退出，M1 网关 ping 和下载页均HTTP200；手机仍为1.29。见 [配置读回](evidence/source-causal-20261001/final-state-before-debug-stop.json)、[结束状态](evidence/source-causal-20261001/final-services-and-debug.json)。
