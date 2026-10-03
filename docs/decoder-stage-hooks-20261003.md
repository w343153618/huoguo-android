# 机主 UDP 实验：输入阶段与异常观测契约

本次只增加独立实验客户端的观测，不更改正式 v1.30 的媒体协议或参数。`UdpVideoProbe` 的 `stageDiagnosticsEnabled` 在独立 runner 线程启动前冻结；原 `startApp` 入口保留诊断开启，新五参数入口供机主 instrumentation 进行同 APK 的 `off/on` 对照。这个选项不进入服务器 descriptor、不保存到公众设置。关闭它只关闭本次阶段诊断；已有呈现计数和其他实验日志仍存在，不能把 `off` 称为全链路零采样。

## 线程、锁和时间边界

- RX 线程生成完整 AU，`nativeAccept` 可以一次交付多个 AU。Inbox offer 节拍使用每次实际 `System.nanoTime()`，不是一个 burst 里可能相同的 `receivedNs`。take 记录成功弹出、队列余深、字节数、与前一次 take 的间隔和 AU 到 take 的年龄。
- Inbox 仍为四帧／2 MiB。Inbox monitor 可以单向调用 `StageDiagnostics` monitor；codec 调用和 consumer 观测在 Inbox monitor 外。诊断器不会回调 Inbox、PlaybackClock 或外层指标对象。
- async 模式只有 video worker 是 codec input owner；同步模式 RX 是 owner。consumer wall 与 `Debug.threadCpuTimeNanos()` 在同一个 owner 上成对采集。CPU 读只在诊断开启时执行。wall 减 thread CPU 表示非 CPU 的经过时间，其中可能含锁、SDK、休眠与调度，不能仅凭这个差值判定哪一项是根因。
- configure 起止覆盖现有 `MainActivity.configure` 的完整调用，包括 Surface 就绪等待、旧 codec 收尾、选择／创建／配置／启动和监听器安装。初始化 configure 与稳态不同，不能用一次初始化耗时解释稳态所有长空档。
- reserve 起点在现有 deadline 赋值后，记录年龄已超时（1）、epoch 不同（2）、停止条件（4）和 config（8）四个独立 bit，同时保留 frame/current epoch。读回是非原子的诊断快照，不是新的 admission guard。原来的 while 和 timeout／stale／stop 分类保持不变。
- copy/prep 跨度覆盖 `getInputBuffer`、容量检查、已有 stale/stop 检查和实际 copy。成功媒体的 `queueInputBuffer` 另有起止跨度。已经失效的 reservation 仍用原来的空非 EOS 提交返回；该取消调用包含在失败的 copy/prep 跨度内，不混入成功媒体的 SDK 提交跨度。计数、异常传播和恢复策略不变。
- FEC 数据来自已有累计 native counters，每次约 100 ms poll 返回后重新读取 phone nanoTime；结束收尾后再读一次。异常记录的时间是“本次发现累计差值的时间”，不是原始 native 异常发生时间，frame identity 为未知。每个 poll 都计数，因此最大 poll 间隔不会误用两次稀疏异常之间的间隔。最后一个 poll 在 worker 收尾和有界 drain 后，故最大间隔可能包括这个收尾间隔，不能将其直接解释为 active RX 阻塞。
- PlaybackClock 的 observer 在实际共享映射变更处记录初锚、transport reanchor、offset decay、audio anchor、hold gain、decoder reanchor、hold decay。初锚无可比前值，delta 保持缺测。正式 clock 默认 observer 为 null；诊断器不会修改 target 或共享音画时钟。

## 有界报告

报告仍只保留数值。旧 totals／histograms 的字段保留，追加覆盖 mask、固定 histogram、120 个一秒 segment、活动 phase 读回和共用 64 项异常事件 ring。普通 offer/take gap 的 ring 门槛为 80 ms，完整 histogram 与分段最大值仍保留；consumer／SDK stall 门槛为 20 ms。ring 淘汰后，窗口外异常是缺测，不能读成零。`numericAppSummary` 仍以 UTF-8 64 KiB 为硬限制，超限失败，不悄悄截断成“完整报告”。

`clock_reanchor_coverage` 和 `coverage_mask` 表示 hook 连接；具体发生次数另由 totals／segments 给出。已连接但没有变更，与旧 APK 未连接这些 hook 是两种情况。SF 的物理显示时钟还没有仅凭这些 Java 时间被证明相同，`sf_time_domain_verified` 保持 0。

## 下一轮观测流程

使用同一个 APK，在现有受认证机主入口通过 `run_authenticated_lan_ui.py --stage-diagnostics off`／`on` 执行有限对照。固定真实视频及播放阶段、1080P／4M／60 cap／80 ms、lead0、PCMoff、反馈／reanchor 与手机实际刷新率；手机限频须在前后读回。独立 SF 30 秒稳态窗口、完整 feed 和源供给同时保留。初始化、steadystate 和窗口外重连分别分析；consumer／take／copy／queue maxima 需与完整事件窗口及 SF 长空档覆盖共同判断。初始 polls0 且 age/stale bit 同时出现，不是 dequeue 阻塞证据。

本次离线检查包括 actual Probe FIFO 27 项、actual queue 方法的 SDK substitute fixtures，以及指标／clock 的 JVM 契约与有界报告检查。它们没有使用手机、codec、网络或服务，不证明手机 FPS、物理触控延时、音画同步或公网性能。真机结果须另行记录。
