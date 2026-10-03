# Native mapping 容量与生命周期只读审查 — 2026-10-03

Case3 的八条容量拒绝记录对应两个帧、八个数据报。拒绝时 adapter 占用八个 mapping，而核心待重组帧数为零。这更符合已由核心 settle 的帧仍占用 adapter mapping 至原期限的有界生命周期浪费，不能直接解释成八个核心未完成帧同时在途。现有证据不支持永久内存泄漏、扩大容量或已经找到所有卡顿原因的结论。

本次只读取已完成的真实机主实验记录和源码，没有改变源码、实验脚本、设备、网络路由或服务，没有运行新的 fixture。M1 环境为机主非隔离实验；手机为保留 CPU 限制的一加12，同家 Wi-Fi、登记 Tailnet 内层认证 UDP。该轮是 startup 候选的部分 OFF/ON/ON 比较，不是公网、异地 V50 或正式 UDP 产品验收。CPU 上限与视频位置有漂移，第四轮未运行，不能形成严格 ABBA 因果结论。

## 实际记录与数值边界

本地原始证据保留在 ignored 目录，不将凭据、APK 或原始日志提交到 Git：

- `docs/evidence/codec-startup-ready-followup-20261003/case-3-on/App-first-report.json`：`native_mapping_details`、`native_fec`、`codec_startup_gate` 与 `decoder_stage_metrics`。
- `docs/evidence/codec-startup-ready-followup-20261003/host/host-session-1791002959304324000.json`：本轮 `assembly_lifetime_ms=80` 和稍后独立发生的 UDP sender 失败。
- `docs/evidence/codec-startup-ready-followup-20261003/build-pins.json`：实际 APK/JNI 与冻结源码依赖。
- `docs/evidence/codec-startup-ready-followup-20261003/analyzed-numeric.json`：独立数值分析；callback 不是独立呈现数据。

`native_mapping_details` 的八条事件完整保留，`events_evicted=0`、`rejections_unobserved=0`，reason code 均为2（mapping capacity）。`clock_mapping_rejected=8` 是数据报计数，不能写成丢失八帧。

| 被拒帧 ID | 数据报记录数 | 首次至最后 RX arrival（phone µs） | adapter/core 深度 | 最老 mapping 年龄 |
| --- | ---: | --- | --- | --- |
| 389 | 4 | 185343141652—185343142381 | 8 / 0 | 56.082—56.811ms |
| 390 | 4 | 185343157947—185343161606 | 8 / 0 | 72.377—76.036ms |

八条事件的整体跨度为19.954ms。每条 `arrival_phone_us - oldest_mapping_age_us` 都得到同一最老 mapping 首次准入时间 `185343085570µs`。本轮 assembly lifetime 为80ms，因此该 mapping 的原期限为 `185343165570µs`；最后一次拒绝时距离其期限还剩3.964ms。这是从原准入起计的期限，不能从新 shard、拒绝或后来 polling 再续期。实际资源回收由后续有效arrival或expire tick触发；80ms是原grant失效边界，不是CPU调度下准时执行回收的保证。

首次 startup commit 为 `185337100502318ns`。容量拒绝发生在该 commit 后6041.150—6061.104ms，bootstrap 后6443.894—6463.848ms。所有这些计算仅使用手机本地 RX/System.nanoTime 域；未将主机时钟、SF 时钟或物理触控延时混入计算。

全会话 `clock_mapping_expired=40`，`frames_expired=3`，`dependency_dropped=37`，数值上满足 `40 = 3 + 37`。这与 core settle 后 adapter 等各自期限清理的路径一致，但它是全会话总量关系，不能作为八个占槽帧各自 settle 原因的逐帧证据。

手机 stage 的第一次相关 FEC polling 读回位于 `185343158305649ns`，包含 expiry/reference loss 各1、dependency drop8、mapping reject5；下一次位于 `185343258217628ns`，包含 dependency drop1、mapping reject3。这些是有界 polling 的增量观察时间，不是异常真实发生时间，也没有具体 FEC 帧 ID。禁止据此把所有异常同时发生、或某次 polling 中的8次 dependency drop直接指认给八个占槽ID。

稍后另有 sender `udp_socket_send / OSError / Darwin errno55 ENOBUFS`，触发会话撤销并造成末段约13秒无新媒体进度。该失败是另一条证据链，不能用来解释上述6秒附近的 mapping 容量事件，也不能用 native 正常 exit0 否认 sender 失败。

## 源码生命周期

以下行号为本次2026-10-03只读审查时的本地源码位置，后续变更应重新核对。

- `experiments/moonlight-v2/transport/media_datagram.hpp:193` 的 `Receiver::settle` 将结束的帧 ID 加入有界 settled 集合。
- 同文件 `:209` 的 `expire` 处理已交付推进或自身期限失效；`:232` 的 `drain` 对没有可用参考链的已完成 P 帧执行 dependency drop、settle 和 `pending_` 删除。该分支不调用交付回调。
- 同文件 `:245` 仅在交付路径调用 `output_`；`:260` 会拒绝已 settled 帧的晚到数据报。settle 与交付不是同一事件。
- `experiments/moonlight-v2/transport/android-udp/phone_receiver.hpp:166` 的 core 交付回调推进 `deliveredThrough_`；`:150` 的 `finish` 只 retire `id<=deliveredThrough_` 的 adapter mapping。
- 同文件 `:158` 的 `expireInternal` 先做 core expire/drain，再做 finish，最后按 adapter 原 `arrival + lifetimeUs` retire 余下 mapping。核心提前 dependency-drop 的较新帧若没有后续交付推进，adapter 占位可保留至自己的期限。
- 同文件 `:185` 在每次有效 arrival 上先执行上述清理；`:194` 在新 mapping 插入前检查 adapter 容量8。容量事件由 `observeMappingRejection` 记录此刻的 adapter 深度和 `core_.pendingFrames()`，不是对两层容量的估算。
- 同文件 `:204` 把最初准入的 arrival 写入 core 本地 capture 字段；后续 shard不延长原 assembly grant。

因此 `adapter_active=8 / core_pending=0` 排除了八个核心未完成帧正常并发占槽这一具体解释。已知源码路径允许核心结束任务后 adapter 仍占槽；这是值得鉴别的回收策略问题，不等于永久泄漏。观察到的容量记录本身没有列出各 mapping 的 ID、flags、reference 或 core settle 原因，尚不能把真实八个占槽帧完整归因。

## 既有 fixture 覆盖与缺口

`tests/native/udp_mapping_diagnostics.cpp:90` 的 `droppedCoreAndReadmission` 已有真实 `PhoneReceiver` fixture：先喂入没有前置 IDR 的 P 帧，使 core dependency drop8、adapter mapping8、core pending0，再验证第九个 mapping 被拒、原期限清理以及之后的准入。`:98` 明确核对 adapter expiry8而 core expiry0；`:109` 验证成功准入后的79999µs期限边界；`:118` 验证后来准入的部分帧仍按自己的原 grant失效。

这是既有源码 fixture 的覆盖，能够独立构造相同的深度关系。本次只读审查没有重跑该 fixture；它不证明真实手机里占槽的八个ID就是 fixture 使用的2—9，也不证明真实被拒的389/390是恢复IDR。

真实证据的缺口为：

1. 八个占槽 mapping 的确切 ID、首次 grant、deadline、flags/reference，以及是否仍有可完成的 core 工作。
2. 对应 core settle 的时间和原因：交付、参考链拒绝、交付推进清理、期限失效，不能只看 `pendingFrames()==0`。
3. 被拒389/390的 header flags/reference，以及这些拒绝是否影响恢复IDR准入。
4. retire 与后续晚到 shard 的对应关系；当前 `admissions_after_capacity_reject=0` 只表示未观察到同一被拒 ID 后来准入，不能证明网络没有其它晚到数据报。

不能猜测占槽ID必然为381—388，不能将8个数据报改写成8帧，也不能从全会话计数关系补造逐帧身份。

## 下一候选：定长诊断先行

下一项可区分实验应保留容量8、80ms原 grant、参考链策略和当前 ENOBUFS 冻结源，先增加范围明确的数值生命周期诊断，默认关闭且仅用于机主独立实验：

- 在真实 core settle 点记录 `frame_id / reason_code / phone_rx_us`，并明确区分交付、参考链拒绝、交付推进与期限失效。使用固定容量环形存储和固定 reason enum，声明完整性/eviction，不保留媒体字节、地址或凭据。
- 在一次容量拒绝时记录至多8个 occupant 的 `frame_id / grant_us / deadline_us / flags / reference / core_pending_for_id / core_settled_for_id`，连同被拒帧的同类 header 元数据。避免在同一拒绝帧的每个 shard重复产生无界日志。
- 同步核对 JNI/Java/APK 的新契约与实际 SHA；先做真实纯 native fixture，再独立评估并发诊断开销，随后做同源有界手机采样。

若诊断证实某个 adapter mapping 对应核心已明确 settled 的 ID，再考虑仅对此类 ID及时 retire的 owner opt-in单因素候选。需保留 retired/tombstone，晚到 shard不能重新建立 grant；不能仅凭 `core_pending==0` 全清，也不能在 mapping刚插入、尚未进入 core 的边界误清。

fixture应覆盖仍未完成的活帧、等待参考帧的合法队列、dependency drop、期限边界、更新IDR推进、logical body拒绝、晚到同帧shard和旧帧窗口；正常交付与反重放结果须保持。真实对照应按逐帧身份观察恢复IDR准入、capacity reject、reference/FEC丢弃和独立SF长空档，区分初始化/稳态并保留事件覆盖限制。

该候选尚未实施、构建或在手机运行。当前不扩大cap8，不通过提高播放缓冲掩盖问题，不把诊断或fixture当作稳定60FPS、公网P2P或V50验收。
