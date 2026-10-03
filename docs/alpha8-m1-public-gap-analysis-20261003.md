# Alpha8 M1 公网 UDP 长空档：离线分层分析

本轮只读取已完成证据、计算数值并写入这份报告，没有操作手机、媒体、Mac 或云服务。分析基线是已发布源码 `73d196a8193394c9362250aa0e8be92fae15e125`；实际 APK SHA256 为 `a663c4d046f1048ae32b23875f8614039c73227a484835d6af024d71a8744f51`。结果不是新一次手机实验，也不是修复验证。

本次观察足以说明：**M1 这轮的手机接收供给在多个约三秒区间内已经低于 30，同时独立 SurfaceFlinger 存在长空档。现有证据不能把全部空档归因于那一次 Inbox 溢出，也不能证明安卓镜像、网络或 decoder 中某一项是根因。**

## 证据与环境

可提交的[数值摘要](alpha8-m1-public-gap-analysis-20261003.json)为闭合白名单，体积小于 16 KiB，保存四份输入 SHA256。原始文件没有复制到此分支：

- canonical tree 的 `docs/evidence/alpha8-30hz-public-20261003/phone-m1-public/App-first-report.json`、`phone-cadence.json`、`ui-acceptance.json`。
- 受限本机文件 `~/Library/Application Support/AndroidRemote/udp-owner-persistent-20261003/evidence/host-session-1791021491699347000.json`，仅提取固定数值项；没有提交原文件、账号、session key 或原始日志。

这是限频一加 12、同家 Wi-Fi 经 NPS 公网入口的机主非隔离试验。实际 App 媒体为 UDP；NPC 外层路径没有由这三份手机文件逐包验证。请求及接收 geometry 为 540×960、30 FPS 上限、80 ms，4 Mbps VBR 为本次受信部署/请求值；App 报告并未独立记录码率读回。stage 诊断、PCM 队列和 codec startup 实验关闭。不是 V50、流量、异地、光学延时或声学同步验收；CPU 上限没有本轮前后读回，源 BBB 格式/播放位置也没有重新核实。

## 不同窗口分别计算

| 观察层 | 窗口或分母 | 数值及含义 |
| --- | --- | --- |
| host native | 141.465269 s，整个 native 生命周期 | 源 AU 3900，即 27.5686 records/s；输出 AU 3897，即约 27.5474 records/s；不是源 SF 或唯一内容 FPS |
| App 首 HGUD 至接收结束 | 140.953997 s | 接收媒体 3891、queued 3884、callback 3864；不与 SF 数量直接相减 |
| helper 进度样本 | 46 rows、45 intervals、135.285442 s | 接收增长 3735，即 27.6083/s；callback 增长 3715，即 27.4605/s；callback 不是呈现 |
| 独立手机 SF | sampler 135.007 s；首至尾观察跨度 134.391115 s | 3552 个呈现时间记录；cadence 26.423、全采样窗口 26.310 FPS |

helper 完整等待 137.042943 s，超出旧 120 s 会话限制，但没有测满一小时。SF poll 链连续观察通过，无丢层、ADB 失败或无重叠风险；**最后 429.435 ms 没有观察到，尾部仍未知**。这不等于完整覆盖每个时刻。

四组窗口不同。host 与手机时钟属于不同设备，SF 时间域也没有本轮独立证明，不能为方便而平移窗口、对齐最大的 gap，或把不同分母的数量差视为丢帧。App 的 3864 个 callback 全部精确回显请求 target，`usable_for_presentation_timestamp_estimate=0`；本报告完全不把它作为独立呈现时间。

## 接收供给和 SF 空档

45 个进度区间均约 3.00 s。接收速率最小 20.2994/s、中位 28.6071/s、最大 30.2838/s；6 个区间小于 25、17 个小于 27、28 个小于 29。短区间瞬间超过 30 是计数边界/到达节奏现象，不代表源提供超过 30 个唯一内容帧。多个低于 30 的区间已经出现在接收端，因而不能只用手机 Surface 丢弃解释全部不足。

SF gap 为 p50 33.155 ms、p95 82.884 ms、p99 124.332 ms、最大 265.225 ms；308 个超过 50 ms，78 个超过 100 ms。`steady_media_progress_healthy=true` 是每 250 ms 检查“接收或 callback 任一有进度”，只判定三秒级停滞；它不是 30 FPS 稳定度检查。该布尔值与 SF 的 265 ms 空档没有逻辑冲突，也不能排除某一个阶段已经失速。

## 现有计数能排除和不能排除什么

- App 全会话只有 1 次 Inbox overflow、拒绝 1、清队列 4、等待 IDR admission 丢 1；最大深度 4、最大字节 37847。late discard 19、input timeout 1，worker expiry/stale epoch 都为 0。**没有保留这一次 overflow/input timeout 的事件时间或 frame identity**，不能证明它是否落在 265.225 ms 空档附近。
- 手机 FEC expired 2、dependency dropped 3、reference lost 2；recovered shards 0。mapping rejection/eviction 为 0、mapping 事件总数 0。本轮没有 mapping 容量拒绝证据。零 recovered shards 不代表网络毫无抖动或丢包。
- host frame-budget drop 0、output-deadline drop 1、dependent-source drop 2、stdout blocked 0。帧数损失较小，不能以这些累计值说明 78 个 SF 长 gap 全由 native budget 丢帧导致。
- native ring 只保留最后四个 summary。其 elapsed 为 138.898713、139.901218、140.971604、141.465269 s；最后两段非 final 的 AU 增长速率约 21.945/s 和 27.093/s。final 的短尾包含退出，不能当稳态区间。所有保留条目的 drop 累计值都已相同，只能知道该一次 deadline drop 在更早阶段发生，无法定位。
- host video `max_send_syscall_ns=101058833`，2 次 send 完成晚于 deadline，最大 overshoot 33.222541 ms；audio 最大 syscall 111.887666 ms，priority-budget drop 18。EAGAIN/would-block、ENOBUFS 和 send error 都为 0。它们是明确值得进一步诊断的长尾，但**墙钟 syscall/span 也可能包含线程调度，不能直接解释为内核阻塞**；缺少时刻/帧身份，无法与某个 SF gap 联结。
- 最大 native wire frame 47310 B，在 32 Mbps native pacer 下的序列化下限约 11.8275 ms，低于 80 ms。这个值不是公网实测带宽，也不能排除调度、QUIC stream 排队或实际链路突发。不能据此增加码率或移除 pacer。

## 下一项最小区分实验

先做一轮有界 **45 s 诊断轮**，保持当前 alpha8 的 physical30、540P、4M VBR、30 FPS、80 ms、lead0、PCM/startup/stageoff、手机限频和公网 UDP 路径不变；只在正式与 owner 会话空闲时启动。不是重新扫完整码率/高刷矩阵。

1. 启动前后读取实际视频格式/itag/内容 FPS、播放位置及两端 CPU 限制。若无法固定同一片段，记录为供给观察，不作 A/B 效果结论。
2. 在同一有界阶段独立采集 guest/source SF、host AU feed 以及手机 SF。源端 SF 层身份必须核对；它仍不是内容帧率。保留每一层的实际开始/结束与未知区间，先比较各层自身供给，不能直接跨时钟配对。
3. 现有 3 s 进度计数不能定位 265 ms 空档。若需要事件关联，只追加固定容量的 host send/span 与 phone Inbox/input/late 事件数值快照，至少带 frame ID/PTS、各自 monotonic time、事件原因和覆盖/淘汰数；不记录账号/密钥、不改变预算/容量/参考链。先 fixture 和采样开销检查，再用；不能拿当前 alpha8 的旧报告追认这些字段已经存在。已有 `inbox_epoch_events` 在 App 数值摘要没有输出，不能假设可以事后取回。
4. 同设备的 SF 与 Java 事件关联前也要检查时间域。跨 host/phone 仅凭同为 ns/us 不可关联，优先使用真实 frame identity 与已验证的映射/误差边界。若仍没有这些证据，就停留在“某阶段供给不足/长尾存在”，不命名根因。

分辨边界应为：source SF 或 AU feed 自身存在长空档，支持继续检查供给侧；供给连续而认证接收出现空档，支持传输/发送路径检查；同一合法帧已及时接收而 input/Surface 显著延后，才支持 decoder/呈现侧检查。当前只有累计或粗粒度字段，尚不能满足最后两项的逐帧关联。

这轮 host cleanup 确认，native 自然退出 0，没有 TERM/KILL。下一轮不修改已发布 alpha8，不重启正式服务/NPS，不放宽认证、取消、国内出口或朋友隔离门槛。
