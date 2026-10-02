# 认证 UDP 手机解码阶段的有界数值遥测

本轮增加 `decoder_stage_metrics`（schema 1），仅由独立 `UdpVideoProbe` 路径启用。正式 `MainActivity` 保留原 `MediaPresentationMetrics(48000)` 构造，实验计数器为 null。采样只写预分配的数值数组；不改变播放缓冲、共享音画时钟、队列容量、恢复策略、Surface 提交目标或 CPU 限制。新字段仅适用于重新构建的实验 APK，不能追认旧 ba84 APK 或历史报告。

## 时间与覆盖

- 所有 `*_ns` 均在手机 Java `System.nanoTime()` 时间域；`clock_domain_code=1`。不包含 Mac 时间换算或单程网络延迟。
- `clock_origin_ns` 为首次完整逻辑 AU 的到达时刻；`origin_present` 区分未进入媒体的失败。`snapshot_ns` 是数值快照时刻，`first_observation_ns`、`last_observation_ns` 为计数器观察时间的范围。
- 固定 120 个 1 秒段，段 i 为 `[origin+i×1s, origin+(i+1)×1s)`；只导出实际触及的段及其前面的空段。前 2 秒为固定 initial band，**不是检测到稳定的时间点**，也不从全会话直方图里删除。按真实 SF 观察窗口再切分。
- 初始化前和超过 120 秒覆盖范围的观察分别计入 `before_origin_observations`、`after_segment_capacity_observations`；这些仍进入全会话计数与直方图，不伪造为段 0 或最后一段。
- `sf_time_domain_verified=0`、`independent_surface_presentation_measured=0`。独立 SF 的实际呈现时间域、窗口与设备仍需本轮另行核对；codec callback 的 target 回显不参与这些阶段计数。
- `clock_reanchor_coverage=0`。本轮没有加入 PlaybackClock reanchor 探针，也不能把 16ms 提交 lead 假定为固定目标位移；接收线程仍可能改变共享 offset。

## 全会话直方图与每秒段

固定直方图的 inclusive 上界依次为 `[-80,-40,-16,0,2,4,8,16,32,64,80,120,250]ms`，最后一个桶无上界。每个直方图包含 `counts/valid/invalid/sum_ns/min_ns/max_ns`。`target_minus_release` 允许负数；其余阶段负数计为 invalid，不塞入正常桶。空直方图 min/max 为 0，并由 valid=0 区分。

| 字段 | 实际观察点与边界 |
| --- | --- |
| `target_minus_release` | 定时 `releaseOutputBuffer` 调用前、现有 `scheduled()` 内的请求 target 减 Java 观察时刻。不是 Surface 实际释放所有权或呈现时间。 |
| `ready_minus_input` | 本次被提交输出的 dequeueOutputBuffer 返回观测时刻减对应非 config queueInputBuffer 调用前时刻。缺失 input、负间隔单独计数。只有 scheduled 输出覆盖，未提交的 late discard 不伪造 ready 观测。 |
| `release_minus_ready` | Java 输出 ready 到定时提交调用前的持有跨度，包含实验 lead 的有界等待。不是完整 codec/Surface buffer 生命周期。 |
| `codec_reserve_wait` | 开始申请 input buffer 到 reservation loop 结束，包括一个或多个实际 dequeueInputBuffer 调用以及 epoch、停止与期限检查。排除 memcpy 与 queueInputBuffer 调用；不是纯 vendor 单次阻塞时间。config 与 media 的调用次数分别保留。 |
| `input_minus_receive` | 对应完整 AU 到达至 queueInputBuffer 调用前的跨度，包括 Inbox 排队、配置及 input 申请。它是调用开始时刻，不证明 queue 调用已完成。 |

每秒段保留 offered count、操作采样的 depth count/sum/max、overflow/cleared/wait-IDR/expiry、codec reserve calls/polls/wait sum/max/timeout及config/media调用次数、media input queue调用开始次数、scheduled count、ready−input sum/max/missing/invalid、target−release sum/min/max。ready−input段内有效分母为 scheduled−missing−invalid；target−release段内分母为scheduled。段的计数和 sum/max 可定位长空档所在阶段；它们不是完整逐帧 trace，也不能证明时间重叠就是根因。

Inbox 深度在每次 offer 完成和 take 完成时采样，`depth_counts_0_through_4` 是**操作次数分布**，不是按时间加权的占用率。take/offer 与 codec 调用保持原来的锁边界。超时与链丢事件发生在不同观察点，不能把事件环条数直接累加成超时帧数。

## 固定事件环

最多保留 64 个事件，以并行数值数组输出 `time_ns/pts_us/kind/duration_ns/queue_frames/queue_bytes/epoch/polls`。事件追加遵循锁获取次序，各线程传入的时间戳可能小幅倒序；与 SF 关联时按数值时刻筛选，不用数组顺序推定因果。`event_retention_chronological=0`；`event_window_start_ns/end_ns`为当前保留时刻的min/max，不代表其间所有事件都完整。`events_observed/events_retained/events_evicted` 明确截断情况。若目标空档的事件已被挤出，只能使用每秒段做粗关联。

| kind | 含义 |
| ---: | --- |
| 1 | Inbox frame capacity overflow，链被清空之前的深度、字节数与 epoch |
| 2 | Inbox byte capacity overflow |
| 3 | input reservation deadline timeout，包含实际等待跨度和 polls |
| 4 | worker 完整 AU 已过 80ms 输入预算 |
| 5 | 保留的 stale failure 枚举，当前没有新的派发点 |
| 6 | input reservation loop 等待至少 20ms（不包括 timeout 的重复记录） |
| 7 | 其他 Inbox chain loss |
| 8 | codec input timeout 导致的 Inbox chain loss，和 kind3 是同一错误的不同阶段 |

reservation 事件没有另取 Inbox 原子快照，`queue_frames/queue_bytes=-1` 明确未知；不能当作空队列。ring 不记录输入数据、图片、URL、账号、密钥或无限逐帧数组。

## 数值 App 摘要与参数读回

`numericAppSummary` 保留上述整个数值树，数组上限 128 大于段上限 120、事件上限 64、直方图 14 桶；没有 JSONArray<JSONObject> 被静默过滤的问题。最后 UTF-8 JSON 超过 64KiB 明确失败，不悄悄删掉前后的性能事件。

同时保留 `surface_submit_lead_ms/applications/wait_count/wait_total_ms/max_output_hold_ms/max_park_ms/budget_fallbacks`。`surface_submit_status_code` 为 0：原 lead0 提交路径；1：已观察到有界等待；2：启用但未观察到等待；-1：无效或未验证。16ms B 轮须真实读回 lead16、status1、wait_count 与 applications >0；0ms A 轮应保持这几项等待计数为0。

## 验证与采样开销的实际边界

`python3 -m unittest discover -s tests -p test_decoder_stage_metrics.py -v` 通过3项：固定边界、有符号直方图、missing/invalid、并发 ring、快照无别名；正式构造未开启；从实际 Java 源码抽取数值摘要方法，在窄 org.json API 替身下检查数组保留与 64KiB 超限失败。已有 `MediaPresentationMetricsProbe` 也通过。SDK37 javac 对 UdpVideoProbe 和实际依赖编译通过。

最终冻结源码：`MediaPresentationMetrics.java` SHA256 `5f210c2c8e3631d66c4497e46592a3f29a4faa1f252801d99073b7fd15f97e0b`，`UdpVideoProbe.java` SHA256 `c5a59ece3ebe899558386df771a5bbc4f1b1acbc69b75605c7e94f364d548166`。数值shape fixture构造14400个逻辑帧、120个一秒段、64个事件以及200个音频数值字段，摘要为 **28401 字节**。这是Java数值树和离线JSON API替身的结果，不是Android JSONObject或真机App最终报告尺寸验收；真机每轮仍需读回byte count。

owned host JDK21 的 stage-only 微基准：最终源码20k帧冷样本267ns/帧，三组200k帧为127/48/68ns/帧，每帧包含五个计数器调用。JIT、锁消除与宿主CPU会影响这些数字；该基准不包含旧Map/JSONArray、codec、ART、UI或并发媒体线程，**不能推导手机采样开销可以忽略**。热路径新增的计数器本身不分配对象，快照和JSON构造发生在采样窗口结束后。

实际下一轮采用同一个新 APK、完整 native feed、固定真实1080P60内容/位置、4Mbps/80ms/PCMoff与手机120Hz，做 lead0/16 单因素 ABBA。先确认来源当前 codec/itag，保留 CPU limiter 前后读回和源 SF 供给；每輪同时采独立手机 SF 长空档，并将它们对到这些计数器的手机时间范围。不要通过 callback 回显构造 FPS，不根据单轮结果推广默认值。
