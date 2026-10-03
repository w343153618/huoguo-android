# Inbox 数值事件导出候选（2026-10-03）

状态：仅源码和离线 JVM 契约候选，基线 `5d8d059`。这不是已发布 alpha8 的字段，不是新 APK、手机实测或性能改进结果。当前服务端 session descriptor 的 `diagnostic_events` 仍为 `false`；此候选不修改 descriptor、默认采样开关或开启路径。

现有 `VideoInbox` 在明确开启诊断后才记录最多 256 条 `InboxEvent`，淘汰最旧记录并累计 `eventsEvicted`。原来的 App `numericTree` 不保留对象数组，而且通用数值数组最多 128 项。因此旧 `inbox_epoch_events` 不能在 App summary 中作为事件覆盖证据。此候选在报告阶段增加闭合的 `inbox_epoch_events_numeric` schema 1；原始 instrumentation 事件数组保持原样。

## 固定列与代码

九个等长的整数列为 `event_code`、`reason_code`、`epoch`、`previous_epoch`、`time_ns`、`pts_us`、`received_ns`、`queue_frames`、`queue_bytes`，每列最多 256 项。`time_ns`/`received_ns` 保持原有手机 `System.nanoTime()` 值，`pts_us` 保持媒体 PTS，未关联帧的 PTS 为 -1；本契约不将它们对齐到宿主、SurfaceFlinger 或物理触控时钟。`chain_lost` 的输出 epoch 与原 instrumentation 一致，指向递增后的 epoch。

| event_code | 现有事件 |
|---:|---|
| 0 | 未知，不输出原始字符串 |
| 1 | chain_lost |
| 2 | idr_admitted |
| 3 | recovered |
| 4 | stale_fail_ignored |
| 5 | startup_prepare_admitted |

| reason_code | 现有原因 |
|---:|---|
| 0 | 未知，不输出原始字符串 |
| 1 | oversized_au |
| 2 | queue_frame_overflow |
| 3 | queue_byte_overflow |
| 4 | worker_complete_au_expired |
| 5 | codec_input_timeout |
| 6 | codec_input_failure |
| 7 | old_epoch |
| 8 | metadata_only_not_media |
| 9 | complete_config_idr |
| 10 | initial_idr_committed |
| 11 | epoch_idr_committed |
| 12 | stale_codec_epoch |
| 13 | codec_stopped |

这些编号不得因新增事件而重排。未知名称保留该条记录并使用代码 0，同时增加 `unknown_event_count` / `unknown_reason_count`；`known_codes_complete` 为 0。任意其他事件字段、原始界面、字符串和凭据不进入这份导出。

## 覆盖边界

新增 Inbox snapshot 元数据 `epoch_events_enabled`、`epoch_events_retained`。App 固定 header 包括 `available`、`enabled`、`capacity`、`retained_count`、`exported_count`、`evicted_count`、`observed_count`，其中 `observed_count=retained_count+evicted_count` 仅表示被事件采样器记录的数量；它不是媒体帧数或全部系统事件数。旧 APK 缺少这些元数据时，`available=0`、`enabled=-1`，不从空数组猜测采样是否关闭。

| availability_code | 含义 | 导出事件 |
|---:|---|---|
| 0 | 没有 Inbox 或缺少新 enabled/retained 元数据 | 无 |
| 1 | 已知未启用采样 | 无 |
| 2 | 未知 worker 收尾状态 | 无 |
| 3 | worker 仍存活，或曾 join 超时 | 无 |
| 4 | worker 收尾已确认，但原始事件数组缺失 | 无 |
| 5 | 已取得完整的最终保留环快照 | 全部保留记录，允许 0 条 |

`worker_alive` / `worker_join_timed_out` 为 0 或 1，未知为 -1；只有两个原始 Boolean 都已知且为 false 才有 `worker_cleanup_confirmed=1`。这里确认的是报告处的 video worker 收尾读回，不是其他音频/宿主进程收尾，也不是成功播放验收。保留历史 join 超时会使事件导出不完整，即使当前 worker 随后退出。

`snapshot_complete=1` 仅表示最终保留环完整导出；有淘汰时 `all_recorded_events_retained=0`。`known_codes_complete` 另行标示能否解释全部保留事件。默认关闭时和缺失时这些完整性标志均为 0。`all_pipeline_events_covered` 永远为 0：即使没有淘汰，也不能认为这个有限事件集合覆盖了接收、codec、显示、全部丢帧或真实画面。

## 上限、失败和未变行为

报告导出直接校验闭合整数字段、环容量 256、保留/淘汰数量和溢出、行数一致性、队列容量 4 帧/2 MiB。缺失、非整数、越界或计数不一致在有完整快照的路径明确失败。它不经通用 `numericTree` 的 128 项截断；保留 256 项不会被假装成完整 128 项。

整个 App summary 的 UTF-8 **64 KiB** 上限继续在所有模块加入后检查，超限抛 `numeric_app_report_limit`。256 条合法事件单独能装入上限，但不能保证它与最大 stage/mapping/audio 模块同时装入；既有 stage 全字段 fixture 加空事件 header 的报告为 54,277 字节。最大事件环与其他模块并存可能使整报告明确失败，本候选不缩短事件、静默丢列或放宽上限来掩盖这一点。

事件 `record`、默认关闭保护、环淘汰、FIFO、4 帧/2 MiB 容量、80 ms、epoch、参考链、codec、deadline、认证、取消、传输和运行服务都未改。新增格式化和数组分配仅在最终报告阶段；没有在媒体热路径增加采样、JSON、时钟读取或日志。

## 离线验证与下一步

- `tests/test_udp_inbox_numeric_events.py` 编译实际 `InboxEvent`/`VideoFrame`/`VideoInbox` 和实际 summary 方法，org.json 使用具备类型保存与合法序列化的 JVM 替身，Python 独立解析结果。覆盖全部固定码、未知码、默认关闭、开启空环、旧 metadata、worker alive/join timeout/未知、真实 admission/recovery/epoch loss/stale 事件、300 次真实记录产生的 256 条保留和 44 次淘汰、数值白名单、恶意字符串不导出、容量/行数/非整数/计数溢出拒绝以及全报告 64 KiB 拒绝。新事件加其他数值模块使报告超限的情况也显式拒绝。
- 既有 `test_decoder_stage_metrics.py` 的实际 summary fixture 仅增加从真实源码提取的容量声明和 JSON `has` API 依赖，原 stage/mapping 数值验收保留。
- 既有 codec startup 和 App session parsing fixture 验证关闭状态、epoch/取消和 endpoint/default 契约未回归。

以上四个 fixture 文件合并运行 **29 项通过**：新导出 10 项、stage 3 项、startup 14 项、App session parser 2 项；`git diff --check` 通过。纯 JVM 测试未使用手机、账号、网络、运行服务、codec 或发布构建。

这不是 Android `org.json`/ART 并发开销验收，不证明手机长空档已经解决。下一轮仍需要单独实现明确的 App 诊断 opt-in、同 APK 采样开销验收，以及同源有界的接收/独立 SF/输入和事件身份关联；不能给旧 alpha8 报告追认新字段，也不能把 callback 当真实呈现或把事件时间强行对齐。
