# Native mapping 生命周期诊断设计 — 2026-10-03

状态：native源码与owned fixture已完成，独立只读review无阻挡，root核对SHA并完成全仓1010项检查。JNI、Java、App、helper及手机尚未集成或验收；此前APK不含新生命周期字段。新native诊断默认关闭，设备、服务及正式默认值未改。下一轮先完成同步契约与逐帧采样，再决定是否做仅针对明确 core-settled mapping 的 owner opt-in 回收实验；不扩大 cap8，不延长原 80ms grant，不以 `core_pending==0` 全清 adapter。

## 已知路径与尚缺的证据

`experiments/moonlight-v2/transport/media_datagram.hpp:193` 的 `Receiver::settle` 只保留 settled ID。终止路径包括：`:212` / `:227` 的交付推进清理、`:216` 的期限失效、`:234` 的参考链拒绝、`:247` 的 core 交付。只有 `:245` 调用输出回调；参考链拒绝不调用它。

`experiments/moonlight-v2/transport/android-udp/phone_receiver.hpp:150` 的 adapter `finish` 只回收 `id<=deliveredThrough_`；`:158` 先 core expire，再 finish，最后按 adapter 自己的原期限回收。`:194` 在新 mapping 进入 core 之前检查 adapter 容量8。因此 core 已终止、ID 又高于交付推进点的 mapping 可以暂留到自己的期限。这个源码路径不等同于永久泄漏，也不能单凭两个深度数值指认实际占槽 ID。

真实 case3 的容量事件已由 `docs/native-mapping-capacity-lifecycle-20261003.md` 独立只读核对：被拒帧389/390各4个数据报，adapter8/core0，最老 grant 为185343085570µs，最后一次拒绝离其原 80ms 期限还剩3.964ms。现有记录没有八个占槽 ID、它们的 header/reference、各自 core 终止原因，不能从全会话计数补造这些身份。稍后 ENOBUFS sender 失败是另一条证据链。

现有契约必须保留：

- `phone_receiver.hpp:32`：292 longs = 36 header + 32×8 mapping 事件，schema1。
- `NativeUdpFec.java:31` / `:72`：固定292长度与现有校验，及原21-field stats。
- `native_udp_fec.cpp:7` / `:34` / `:75`：同一个 State mutex 串行 accept/expire/snapshot。
- `UdpVideoProbe.java:1126` / `:1129`：App 只复制明确白名单中的数值对象，compact UTF-8 summary硬上限64KiB。原 frame-event drain 未进入此白名单，不能假定它已经随 App 数值报告上传。

## 最小新增观察点

新增独立、默认关闭的生命周期诊断，不扩展或解释重写现有292/21字段。core 已增加可选的 settle observer；无 observer 的其他 `Receiver` 使用者保留现有 constructor 调用。native实际接口通过独立setter传入 `void* context` 与 `void (*sink)(void*, SettleReason, uint64_t, uint64_t) noexcept`，默认null，避免可抛异常的公共std::function和新的每帧分配；准确接口及fixture结果见末尾checkpoint。

将所有实际 settle 调用统一为 `settle(id, nowUs, reason)`。observer只接收固定数值 `(reason, id, local_now_us)`，不接收媒体 body，不使用新的系统时钟。PhoneReceiver 通过现有 accept/expire 把手机 `System.nanoTime()/1000` 域传入；generic Receiver 的这个时间仅称 caller-local time，不自行称手机或网络延时。

原因固定为以下四类，所有分支均有独立 fixture：

| Code | 名称 | 来源与含义 |
| --- | --- | --- |
| 1 | `core_delivered` | core 输出回调返回后的交付 settle；不保证 logical parser 接受、Java 入队、codec 呈现或用户看到 |
| 2 | `superseded_by_delivered` | `id<=lastDelivered_` 的终止清理 |
| 3 | `assembly_deadline` | core 原 grant/期限失效 |
| 4 | `dependency_rejected` | 完整帧不满足现有参考链规则；现源码把缺关键帧和旧 reference 条件合在同一分支，不凭本 reason 区分两者 |

通知点紧邻原 settle，保持 stats、输出调用、settled tombstone 和 erase 的原顺序。若通知发生在 erase 前，observer只向固定诊断存储写入元数据：不得在 observer 中调用 `PhoneReceiver::retire`、再次进入 core、JNI、Java、文件或等待。callback 不增加锁，不更改 core pending 容器，且本实现必须不抛异常。对一般 callback 抛异常的契约不能宣称已自动保持原语义；优先限定私有/noexcept sink，fixture验明。

PhoneReceiver observer可在现有 mapping（按同一 ID 查找）写两个只用于诊断的值：`observed_core_settle_reason`、`observed_core_settle_phone_us`。mapping 不存在则只记录 settle ring。关闭诊断时不记录 ID/时间/ring，也不查八个 slot；实现中core仅保留原settle点的实际标量总计数，`core_settle_unobserved`由该总计数减去已观察通知数得到。不能用 `framesDelivered + dependencyDropped + framesExpired` 代替实际settle次数：原output回调可在增加framesDelivered之后、settle之前抛异常。disable同时清ring/dedup以及至多8个现存mapping的观察标记，reenable不补造此前通知。这个 fast branch 和新增固定存储仍有成本，不能声称零开销。

core 已新增只读 `frameState(id)`，仅在容量诊断快照时查询。实际编码：0 absent/unclassified，1 pending-incomplete，2 pending-complete，3 retained settled tombstone，4 ID不高于 lastDelivered（隐含交付推进）。查询使用现有 pending/settled/lastDelivered，不推进 expire，不分配、不改变依赖。Code0不能推定为 settled，Code2不能单凭状态认定 vendor decoder 或网络堵塞，Code4不证明这个具体 ID曾输出。确切终止原因仅来自上述 observer。

## 独立定长 schema1 提案

新增 `nativeSetMappingLifecycleDiagnostics(long, boolean)` 与 `nativeMappingLifecycle(long)`，Java添加独立 checked wrapper / status / validation。建议输出**恰好240个非负 signed longs**：32 header + 16×4 settle rows + 2×(8 snapshot header + 8×8 occupant rows)。JNI payload为1920 bytes；这不是序列化 JSON 大小。常量、名称、索引必须在 C++、JNI、Java 同版本冻结，旧方法仍返回原292。

Header 32项固定契约如下；native源码已实现，JNI/Java/App仍待同步，当前APK不含这些字段。

| Index | 字段 |
| --- | --- |
| 0—2 | `schema_version`, `enabled`, `coverage_mask`（15只声明hook支持） |
| 3—4 | `core_settle_observed`, `core_settle_unobserved` |
| 5—8 | `settled_core_delivered`, `settled_superseded`, `settled_deadline`, `settled_dependency_rejected` |
| 9—14 | `settle_events_total`, `settle_events_retained`, `settle_events_evicted`, `settle_events_cleared`, `settle_event_capacity=16`, `settle_event_columns=4` |
| 15—16 | `capacity_calls_observed`, `capacity_calls_unobserved` |
| 17—21 | `capacity_snapshots_total`, `capacity_snapshots_retained`, `capacity_snapshots_evicted`, `capacity_snapshots_cleared`, `capacity_snapshots_suppressed_duplicates` |
| 22—25 | `capacity_snapshot_capacity=2`, `capacity_header_columns=8`, `occupant_capacity=8`, `occupant_columns=8` |
| 26—28 | `enable_transitions`, `disable_transitions`, `snapshot_phone_us` |
| 29—31 | `capacity_dedup_capacity=32`, `capacity_dedup_evicted`, `last_enable_phone_us`（冷启动未有有效tick时可为0） |

Settle ring每行4项：`sequence, frame_id, reason_code, settle_phone_us`。固定16行，按最新保留的 chronological tail输出，未使用行全0。每次明确终止最多写一个通知；计数不以 ring 的保留范围代替全部发生量。16行不是整段媒体的完整历史。

Capacity snapshot只在现有 `MappingCapacity` 分支、插入被拒 mapping之前生成。每个snapshot header 8项：`sequence, rejected_frame_id, rejection_phone_us, rejected_flags, rejected_reference_id, adapter_active, core_pending, occupant_rows`。它明确记录被拒帧是否关键帧，避免凭 frame ID猜测恢复 IDR。最多2个snapshot，当前cap8拒绝应有8 occupant行。

每个 occupant行8项：`frame_id, grant_phone_us, deadline_phone_us, original_flags, original_reference_id, core_state_code, observed_core_settle_reason, observed_core_settle_phone_us`。grant来自当前 mapping 最初成功准入的 arrival；deadline等于同一 mapping `arrival+original.lifetimeUs`。不使用 first-capacity-reject时间续期。reason/time都为0表示没有观察到该 mapping的明确通知，不表示没有settle。

同一 JNI State mutex 下捕获容量快照，读的是决策时八个真实 mapping；不能100ms后读取当前map并冒充历史占槽。per-ID core_state在容量决策处读取，此时不在 core settle/erase迭代内。快照记录只存数字，固定数组，不产生每数据报JSON或新的采样线程。

避免每个被拒shard重复快照：独立32-ID有界dedup cache，记录当前保留关联中的首个容量拒绝。重复调用只增 `capacity_snapshots_suppressed_duplicates`。dedup eviction和disable clear后可重新捕获同一ID，所以“首次”是当前保留窗口中的首次，不是全会话绝对首次。不要复用旧292的cache并让旧开关改变新契约。snapshot只有2份，eviction必须显式；若连续多个被拒帧覆盖过去事件，不宣称历史占槽完整。

## 校验、覆盖与报告尺寸

Java严格校验240长度/schema/actual enabled/支持mask/固定容量与列数、所有非负值、深度≤8、unused padding、reason范围、sequence/time chronological tail以及安全加法。主要计数关系：

- 四种observed reason之和等于 `core_settle_observed`，并等于 `settle_events_total`。
- 每个ring的 total 等于 retained + evicted + cleared；不得把disable clear算作eviction。
- `capacity_calls_observed == capacity_snapshots_total + capacity_snapshots_suppressed_duplicates`。
- occupant grant正数、deadline大于grant且差值1000—80000µs；记录的rejection time落在该占槽mapping原期限之前。无需把所有mapping的期限强制等于80ms，当前合法header允许较短lifetime。
- occupant ID/flags/reference须满足原header关系；observed settle reason/time成对为0或有效，时间不早于grant、不晚于rejection。若settle通知缺失，不补造原因。
- frame ID仍遵循已有JNI signed-long saturation边界：饱和ID不能作唯一关联key；不以饱和值生成确定的逐帧因果结论。精确非饱和样本才能支持回收候选。异常长度/缺新symbol/无效enabled不得零填充或静默读旧JNI。

冷读、现有约100ms poll、final读均用独立240-long snapshot；snapshot不清ring、不推进tick。单独记录JNI+validation read次数/失败/total/max，与后来JSON序列化和native写入成本分开。先评估off/on并发采样成本，再做真实同源观察；不把microbench或较低FPS自动归因给诊断。

新增summary白名单对象建议为 `native_mapping_lifecycle`。采用数字列数组：settle4列各≤16项，capacity header8列各≤2项，occupant8列各≤16项，并用生成的 `snapshot_row_index` 列关联所属快照，显式输出capture/retention数量。它符合现有 `numericTree` 只保留数值array且单列最多128项的规则。不要使用JSONObject数组而被converter静默丢弃。

**该新对象的compact UTF-8预算目标≤8KiB，完整App报告仍≤64KiB，不能增加上限。** 已有最大样本fixture报告53,680bytes（前一契约的JVM替身测量，非当前ART保证）；加8KiB仅是设计预算，不是本次已验收的大小。当前ENOBUFS两轮实际完整首次报告经Python重新compact估算31,961/32,117bytes，但文件pretty尺寸超过65KiB，不可据pretty尺寸判定App已违反上限；Python重序列化也不是原Android实际bytes读回。

实现后须用实际当前Java `numericAppSummary`、最大stage segments/events、完整旧32-row mapping ring、满新16-settle/2-capacity ring、audio/touch/startup字段及19位时间/ID进行联合最坏样本fixture；再核对Android生成的实际JSONbytes。若联合样本超限，在构建阶段调整**新的诊断容量/表示**并明确冻结契约，不动态静默删字段，不升64KiB，不改旧292。报告失败必须显式保留失败/正常收尾状态。

## 实施前检查与下一单因素实验

第一阶段只增加观察，不改变retire、cap、grant、reference、Inbox、clock、host sender或媒体协议。需要同版receiver/core/JNI/NativeUdpFec/Probe/App/helper与实际APK JNI SHA；旧APK不含这些字段，不能追认历史占槽身份。缺新JNI明确失败并尝试现有有界清理。正式App不打包新UDP类或native库。

Fixture至少覆盖：四种settle来源；callback不重入/不改变core迭代；先入adapter而尚未进core的ID（state0不能算settled）；仍未完成和等待合法参考的pending；core dependency-drop的8个真实slot及恢复IDR被拒；各slot原grant/deadline和79999/80000µs边界；logical parser拒绝与core-delivered的区别；更新IDR交付推进；晚到settled shard与旧ID窗口；ring/dedup wrap、disable/reenable、缺JNI与240/292交叉拒绝；数字summary白名单/实际bytes与超限失败。既有 `tests/native/udp_mapping_diagnostics.cpp:90` 的 droppedCoreAndReadmission可作真实receiver fixture基础，但不能替代新观察字段验收。

只有下一真实采样在决策时给出八个slot的确切ID，并把它们与明确core-settle通知对应，才考虑独立的 **owner显式 `retire_core_settled` opt-in**。observer仍只记录；等core receive/expire完成后，在安全外层边界仅retire正面通知匹配的当前mapping，保留既有retired/tombstone与旧ID规则。可先复制最多8个匹配ID到固定数组再erase，不能在core callback里退役，不能凭pending0或state0全清。未观察通知的mapping保持原期限。任何新retire计数独立说明，不重写原mapping-expired语义。

该后续开关必须与本诊断开关分离、默认关闭，比较同一APK、内容格式/位置、CPU限制、host sender、cap8、80ms、reference/epoch/lead0等实际读回；事件覆盖和未知尾部独立记录。结果看恢复IDR准入、capacity rejection、FEC/参考链/Inbox损失及独立SF长空档，不只看callback或平均FPS。没有异地、公网或V50实测，不扩大验收边界。

## Native实施 checkpoint

2026-10-03完成范围仅为 `media_datagram.hpp`、`phone_receiver.hpp` 和新增 `tests/native/udp_mapping_lifecycle.cpp` / `tests/test_udp_mapping_lifecycle.py`。源码任务与四文件SHA保存在ignored的 `docs/evidence/mapping-lifecycle-diagnostics-20261003/source-task-state.json`，状态done。没有修改JNI、Java、gateway、sender、build、APK或运行设备/服务，没有commit。

已冻结的native接口为 `Receiver::SettleReason`（1—4）、`SettleObserver`（noexcept函数指针）、`setSettleObserver(context, sink)`、`settleCalls()`、`frameState(id)`（0—4），以及 `PhoneReceiver::setMappingLifecycleDiagnostics(bool)` / `mappingLifecycle()`（240值）。observer在原tombstone更新后、pending erase之前接收caller-local时间；只写固定settle ring和现存Map诊断标记，不调用retire或core、不阻塞、不分配、不打印日志。默认OFF卸载observer，原settle点的标量计数仍精确记录通知覆盖缺口。

Owned native10项、既有mapping7项、native build boundary12项通过，共29项；真实C++/既有pinned FEC编译使用 `-std=c++20 -Wall -Wextra -Werror -O2`。覆盖四种实际settle来源、core状态0—4、决策时occupant身份/历史快照、16/2/32边界与clear/reenable、79999/80000µs和1ms短合法grant、logical parser拒绝、原output回调异常。逐数据报对照保持旧21统计、292诊断、旧frame事件及交付结果一致，cap8、grant、参考链和retire策略未改。root随后完成1010项全仓检查，核对四文件SHA；独立只读review未发现阻挡。

Header SHA-256：`media_datagram.hpp` 为 `746651ea2613fc92b55bb21a5b52d1befbe6a10d229cd2d2459600164e7f615c`；`phone_receiver.hpp` 为 `c3b7732ce696f509f744e26c63625b1b8bd8bd173e428ffcccc8900ad847299f`。这些是本次native源码pin，不是此前APK/JNI新字段验收，也不能追认历史报告的八个占槽ID。

下一步仍须同版JNI/Java/App/helper集成、240契约/缺symbol/交叉长度校验、实际numeric summary联合64KiB最坏边界、独立并发采样开销及手机真实内容验证。native fixture通过不代表Android构建、真实生命周期样本或流畅度收益。新的retire策略尚未实施，默认仍OFF。

本设计与 `docs/native-mapping-capacity-lifecycle-20261003.md` 对齐。原设计阶段只新增文档；上述checkpoint明确区分后来完成的native源码/fixture和仍未开始的Android/手机验收。
