# M1 真实视频的 guest Perfetto 探针

此脚本只为拆分当前真实 YouTube 播放的源端空档而采集时序；不启动播放器、不修改账号、虚拟机资源、显示刷新率或现有服务。默认设备 `emulator-5556`，Morphe 包为 `app.morphe.android.youtube`。实验仍须由操作者记录当前真实视频、播放器选中清晰度、联网路径与手机参数；选择“1080p60”不等于每秒实际解码并呈现了 60 个新内容帧。

2026-10-01 在该实例的只读能力检查中确认：`perfetto`、`atrace` 可用；注册数据源包括 `linux.ftrace`、`linux.process_stats`、`android.surfaceflinger.frametimeline`、`android.surfaceflinger.layers`、`android.surfaceflinger.transactions`、`android.surfaceflinger.frame`，`gfx/view/video` 分类及 scheduler/task 事件可采。后三类仅通过显式选项启用，不采 Android 日志、画面、媒体元信息或网络载荷。

从项目根目录执行核心采集（必须使用全新的输出文件）：

```sh
python3 scripts/probes/guest_perfetto_probe.py \
  --seconds 35 --categories video --capture-only \
  --output /private/tmp/huoguo-perfetto-capture-01.json
```

时长必须 30–45 秒；内部缓冲 32 MiB、普通 trace 上限 64 MiB，`--surface-buffers` 的完整图层/事务追踪上限 128 MiB。`--categories video` 保留 CCodec 阶段、减少 observer 的 gfx/view 事件量；默认仍为三类，可逗号手动选择。报告含 `captured_categories`，不能只因能力存在就声称实际启用了分类。先前存在的 trace 会话保持原样。脚本给本轮创建唯一 guest trace 和 PID 文件，异常清理时同时校验 PID 的 executable 和本轮 output 路径，才向该进程发 TERM。成功后移除本轮 guest 文件；失败保留已复制的 local partial 与唯一 guest trace，仍清理自己的 PID 文件。原始 trace 留在新建的 `/private/tmp/huoguo-guest-perfetto-*/guest.pftrace`，目录 0700、文件 0600。CLI 回传这个私有路径，仓库数值报告不存它。原始 trace 和 private diagnostic log 可能包含进程、线程、系统 slice 名，必须保持私有且不能提交 Git。

Trace 等待限时是请求时长 +20 秒；复制单独限时 45 秒并对照 expected bytes，避免把大文件复制失败当作 trace 启动失败。错误状态 21 表示 trace 等待超时，22 是 copy 超时，24 是 OS error；`failure_phase_id` 1启动/2等待/3远端size/4复制/5size核对。诊断只读取私有日志末尾最多64KiB、输出固定原因 flags，不输出错误原文。失败或 partial 即使能够解析，也不能据“导入统计无丢失”声称是完整 trace。

可先执行 `--check`（仅能力读取）；需要保存其结果时也传新 `--output`。

对已经完成的 raw trace 分析，无须重跑设备：

```sh
python3 scripts/probes/guest_perfetto_probe.py \
  --analyze-existing /private/tmp/huoguo-guest-perfetto-EXAMPLE/guest.pftrace \
  --trace-processor /private/tmp/huoguo-perfetto-v58.2-trace_processor_shell \
  --output /private/tmp/huoguo-perfetto-numeric-01.json
```

官方 mac-arm64 processor 来自 [Perfetto 官方分发脚本 manifest](https://get.perfetto.dev/trace_processor)，该版为 v58.2，13,597,976 bytes，SHA256 `d29864d1ba3b36855527bb1b0ca3aa7f703cdce338b9680bb922c5c151b358fa`。当前外部工具路径见上例；它不在仓库里。固定 SQL 只导出数值时序、固定角色/类型 ID 与枚举映射。未识别 codec slice 名只计数，不输出原文本；stdout/stderr 错误也只有数值状态码。

分析产物包括：

- `clock_snapshots`：trace canonical timestamp 与 guest REALTIME(1)、MONOTONIC(3)、BOOTTIME(6) 的同一 snapshot 对照。应以这些数值将 SF MONOTONIC fence 时序映射到 trace 时钟；guest wall 与 Mac wall 的差不能直接当传输延迟。
- `thread_states`：YouTube、SurfaceFlinger、codec service 的 Running、Runnable、Sleeping、Uninterruptible 数值状态，完整保留至少 10 ms 的段；`thread_state_summary` 对本轮该角色全部有效 state 数值聚合，不加行数上限。Runnable 包括 R 与 R+。这些线程的 duration 总和可超过 trace 时长，不应被当成整机 CPU 利用率。
- `slices`：固定类型的 queue/dequeue/acquire/latch/present/DoFrame/decode/output/input/wait 时序；不保存任意名称。不同线程的 slice 必须根据实际时序证据解释，不应按相邻两个事件就假定同一帧。
- `codec_stages`：严格接受已知标准 decoder component 和 `CCodecBufferChannel::queue(...#数字instance@ts=数值)`、`onWorkDone(...#数字instance@ts=数值)`，仅输出 component ID、数值 instance、stage ID 与数值 PTS。没有 PTS 的 outer onWorkDone 独立为 scope stage6；render/send/handle 的标准名没有 PTS，不能伪造逐帧配对。
- `codec_summary`：同 component、instance 且唯一同 PTS 的 queue→onWorkDone 标记间隔；重复 PTS 计为不确定，负间隔不用来声称“负解码延时”。这个差值含异步排队与回调调度，不等于纯 CPU 解码耗时，也不是端到端显示延时。`stage_duration_ms` 与 `stage_completion_gaps` 单独计算闭合 scope 的方法耗时和退出间隔；未闭合的边界 scope 另计，不把 entry 数量当实际显示数量。无匹配 queue/done 的 PTS 也另计，trace 两端的在途帧不能自动判为丢帧。
- `trace_import_stats`：非零导入错误、丢失、overrun 的固定 ID/计数。采不到事件或存在丢失时，不能从“没有 stall”推导链路正常。

与 `measure_source_frame_fences.py` 的独立 SF desired/actual/ready 三列实验并行使用。若 codec `onWorkDone` 回调与 frameReady 同时断流，优先调查 decoder/player 或宿主阻塞；若 ready 连续、actual 才晚，SurfaceFlinger/显示合成更可疑；若两者连续但 gRPC screenshot PTS 断流，抓屏回读路径更可疑。这些是需在同一 trace 时钟和明确 layer generation 上验证的判别条件，不能提前当作结果。

`android.surfaceflinger.frametimeline` 虽注册可采，其 [官方 FrameTimeline 说明](https://perfetto.dev/docs/data-sources/frametimeline) 仍注明 SurfaceView 支持限制。当前 helper 不输出这个可选表；SurfaceView 的结果仍以独立 fence 三列为证。Scheduler 的 [官方解释](https://perfetto.dev/docs/data-sources/cpu-scheduling) 区分 Runnable 等待、Running 与阻塞，guest scheduler 不能单独证明宿主 libvpx/VideoToolbox 时间。`video` atrace 的分段依据 [官方 CCodecBufferChannel.cpp](https://android.googlesource.com/platform/frameworks/av/+/master/media/codec2/sfplugin/CCodecBufferChannel.cpp)，category 配置与 trace 开销参考 [Perfetto atrace 文档](https://perfetto.dev/docs/data-sources/atrace)。

Tracing 自身增加开销。应比较同一源内容的“有 UDP 串流/抓屏/编码”与“无串流/无 gRPC/无编码”两轮，均保持 Perfetto + SF fence，避免把探针开销误判为产品瓶颈。所有真实手机、公网及音画结论仍需要该轮实际接收/显示数据；此 helper 不采手机也不测网络。

验证：`python3 -m unittest discover -s tests -p test_guest_perfetto_probe.py -v`。离线解析检查通过不能替代本机实际 trace 的兼容性验证。

首轮 `udp-01` 和 `source-only-01` 已成功采集 scheduler 与 clock，但因能力解析最初漏识别 category 行前导空白，实际配置中的 `gfx/view/video` 为空。这两个旧 trace 的 CCodec slice 缺失不能作为 decoder 正常/异常证据；它们仍保留作 SF/调度对照。该正则已做精确修复并增加离线回归，后续两轮必须在 capture report 中确认三个 category 真正被选入。


## Buffer 身份与 graphics frame 事件

`--surface-buffers` 增加 ACTIVE layers（只启用 BUFFERS、COMPOSITION）和 ACTIVE transactions；`--graphics-frames` 单独增加 `android.surfaceflinger.frame`，不要求完整 layers 快照。它们默认关闭；当前镜像必须注册对应数据源，否则失败而不会自动降级。帧事件源名不是 `graphics_frame_event`，后者是 packet 类型。源注册只证明可尝试采集，不保证目标 SurfaceView 导出 queue/acquire/latch/present。

```sh
python3 scripts/probes/guest_perfetto_probe.py \
  --seconds 30 --categories video --surface-buffers --capture-only \
  --output /private/tmp/huoguo-buffer-capture-01.json

python3 scripts/probes/guest_perfetto_probe.py \
  --seconds 30 --categories video --graphics-frames --capture-only \
  --output /private/tmp/huoguo-frame-capture-01.json
```

分析大于64MiB的 buffer raw 时，同样传 `--surface-buffers`，使私有文件校验使用128MiB限额；普通分析保留64MiB。SurfaceView buffer 专用数值分析见 `analyze_surface_buffer_trace.py --help`。导出只含固定列和整数，图层名称仅在私有 SQL 内用于选中固定 Morphe 视频层。

`trace_copy_complete=true` 只表示复制字节核对成功。`duration_coverage_verified=false` 要求后续检查实际 `trace_bounds`，并分别检查每个源的覆盖。`trace_near_byte_limit=true` 提示文件达到容量95%；接近上限、丢失 packet、尾端缺失即使 CLI exit0 也不能当作完整有效窗口。ACTIVE transactions 在本镜像还包含采样开始前历史，必须排除；用于关联时取 trace、layers、transactions 的覆盖交集。`partial_trace_retained` 是旧兼容字段，仅描述复制/采集错误，不能替代时长完整性。

现代 AOSP 的 `curr_frame` 可能来自前端 requested buffer state；首次 snapshot 观察不是 latch 或屏幕呈现。Buffer transaction 的 `buffer_data.frame_number` 与 `LayerState.frame_number` 分开，父 `post_time` 按 entry 内 transaction ID/数组索引匹配。buffer ID 是可复用对象，不能单独作帧身份。`snapshot.ts == transaction entry.ts` 是同次 commit 状态记录，不能宣传“0ms显示延时”。此身份关联尚未把 codec 媒体 PTS 接到 present fence。
