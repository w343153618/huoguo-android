# App 数值报告预算与下一轮采样边界

这是对 `3ac6774` 已合入 Inbox 数值导出的离线审查。实际格式器输入源码基线为 `a83b668`，SHA 和脱敏数字收据见 [app-report-budget-20261003.json](app-report-budget-20261003.json)。没有操作手机、真实媒体、服务或诊断默认值，没有改生产算法或截断规则。已发布 alpha8 APK 没有新的 Inbox 数值列，不能把本候选字段追认给它。

## 明确的接受与拒绝条件

`UdpVideoProbe.numericAppSummary` 在组装全部模块后检查 UTF-8 字节数：**不超过 65,536 字节接受，超过则抛 `numeric_app_report_limit`，不返回被截断的报告**。对象 key 与数值位数都占预算，数组有固定上限不等于全报告必然可装。

现有固定形状为 stage 40 列 × 最多120个分段、13 列 × 最多64个事件；mapping 8 列 ×32个事件；Inbox 新模块9列 ×256个事件。通用 `numericTree` 的每个数值数组仍有128项的既有边界，stage/mapping形状均在其内；Inbox使用专用格式器，不能用通用截断让256条看起来完整。

下表调用实际 Java 格式器。stage数字来自实际固定数组 API 的合成输入，mapping通过现有严格 validator；Inbox事件是闭合 schema 的合成整数数据。表中不包含音频、native FEC、触控、网络绑定、显示模式和其他额外标量。因此“剩余”是供遗漏模块共同使用的总预算，**不是整个实际 App 的保证**。接受样例均逐份对照了实际 `numericAppSummary` 的字节数；只使用整数，避免 JVM JSON替身与 Android浮点格式的差异。

| 组合 | UTF-8 字节 | 剩余预算 | 实际格式器 |
|---|---:|---:|---|
| 仅 Inbox 256 条常用位数事件及基础元数据 | 16,521 | 49,015 | 接受 |
| full stage样例 + mapping样例 + 空 Inbox | 46,952 | 18,584 | 接受 |
| 同上 +128条常用位数 Inbox | 54,541 | 10,995 | 接受 |
| 同上 +256条常用位数 Inbox | 62,349 | 3,187 | 接受，但额外模块容易超限 |
| 同上 +163条最宽整数 Inbox | 65,533 | 3 | 接受，仅此精确样例 |
| 同上 +164条最宽整数 Inbox | 65,647 | -111 | 拒绝 |
| 同上 +256条最宽整数 Inbox | 76,135 | -10,599 | 拒绝 |
| 合成45秒/30次每秒 stage + mapping +256条常用 Inbox | 44,720 | 20,816 | 接受，仅此精确样例 |
| 同上 +256条最宽整数 Inbox | 58,506 | 7,030 | 接受，仅此精确样例 |

full stage样例本模块42,311字节，mapping样例3,466字节；常用Inbox256条本模块15,927字节，最宽Inbox256条29,713字节。45秒合成 stage 因部分操作的 supplied 时间晚于供给时刻而保留46个分段，本模块24,682字节；这不是实际45秒手机时序或30FPS呈现结果。

为了验证编码上界，还在真实 stage 数组形状中填入最宽 signed long：stage模块121,291字节，单独带基础header已122,440字节；再加mapping和最宽Inbox共155,115字节，全部明确拒绝。这是**整数表示包络，不是合法物理时序或一小时内可达的性能样本**。它足以否定“固定容量意味着任何内容都能同时装进64KiB”，不能用它预言真实手机会生成该体积。

本轮 `tests/test_app_report_budget_contract.py` 3项通过，检查真实固定形状、合并字节数/硬阈值、163/164边界和表示包络拒绝。数字收据不包含原始手机日志。其他模块和浮点序列化尚未纳入本轮全量手机预算，正式采样前仍须从最终 Android报告读回实际 UTF-8字节数。

## 已发现的收尾风险

当前 Probe 的统计格式器异常只记录 Bundle failure，`appReport` 保持null，最终 AppListener 获得空对象和 failed=true。`AuthenticatedLanUdpUi.finished` 将缺少 `audio_cleanup_confirmed` 的空对象当作音频收尾未确认，置入 `retiring` 并禁止重连。因此统计超限可能在实际音频已收尾时表现为资源收尾失败。此审查不改保护条件；下一独立候选应把真实收尾收据与统计报告接受/拒绝分离，未确认或真正未收尾仍必须禁止重连。不得伪填audio确认、静默裁切统计或跳过资源保护。

## 下一轮真实30源长尾可以关联什么

| 数据 | 可支持的关联 | 边界 |
|---|---|---|
| 新 Inbox event_code/reason_code、epoch/previous_epoch、time_ns、pts_us、received_ns、queue_frames/bytes | 同一手机 Java单调时钟内的准入、参考链丢失、输入超时、过期、旧epoch失败和IDR提交事件 | 仅现有事件集合；淘汰/disabled/worker cleanup不完整时不能称全窗口覆盖；recovered指IDR输入提交，不是画面恢复 |
| stage segments与kind/pts/time/epoch事件、输入call/copy/reserve、offer/take间隔与coverage flags | 有界窗口内区分供给停顿、FIFO突发、consumer/input调用长耗时、丢弃和guard | stage不同事件的frame_id覆盖有限，FEC poll时间不是异常真正发生时刻；event retention不保证全局时间顺序 |
| native mapping 32×8、实际FEC累计/差分 | 已保留mapping拒绝身份、容量/过期/参考链变化 | mapping无拒绝不代表没有codec或显示问题；uint64饱和ID不能保证唯一 |
| 独立 phone/source SF cadence、完整feed序号与host record时序 | 各层供给和独立呈现长空档 | 当前callback仍可能回显请求target；SF时间域未验证前不能强行把某个gap与Java事件逐帧对齐 |

建议下一真实轮固定现有账号、同一视频/itag/实际尺寸/播放位置、30源、目标FPS、80ms、码率和发送路径，窗口45秒。初始化、稳定段、结束未知尾部分开。先证明source SF/完整feed及手机独立SF的覆盖，再用同域PTS/time/epoch讨论事件关联；仅统计同窗出现不能认定根因。给每份报告记录schema、采样读回、retained/evicted、worker状态和字节数，不能把alpha8旧APK缺少事件解释成没有事件。

## 开销验收计划

1. **纯编码边界先做离线检查**：实际Android JSONObject数值格式、最终byte count、最大ring组合、失败路径和内存上限；JVM microbench最多用于发现明显分配/编码问题，不等于ART或完整并发零开销。
2. **同APK的明确owner opt-in**：默认仍关闭。当前 `diagnostic_events` 同时启用 native诊断、约100ms drain（每tick最多128条）、8192条native Java事件保留以及Inbox记录；OFF/ON比较测的是这组联合成本，不能归因成Inbox一个环。mapping约100ms读取、stage独立参数保持相同，记录各层实际读回。
3. **重复且有界的真实媒体比较**：保护正式和owner会话，source/位置/网络/CPU限制相同，按OFF/ON/ON/OFF进行45秒配对；记录前后CPU上限、source供给和实际渲染差异。真实内容/供给明显漂移的轮不可当开销对照；不增大缓冲来覆盖变化。
4. **同时量多个代价**：接收/独立SF FPS与>50/>100ms长空档、Inbox overflow/expired/input timeout、native/FEC变化、RX diagnostic drain/read耗时、手机进程CPU、GC/pause与RSS增长、收尾/序列化wall time和最终UTF-8字节数。开始前明确关联窗口和不确定尾，不能只报告均值或一个“0ms开销”结论。
5. **收尾必须独立验收**：正常close、真实资源未结束、状态未知，以及每一种情况下统计接受或64KiB拒绝；只把确认的实际资源收尾允许重连。超限拒绝保持显式，不修改既有截断语义。真实并发开销与phone/V50/公网边界分别记录，不由源码/JVM结果推断。
