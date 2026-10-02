这组四轮 ABBA 没有给出足以改变 pacing 推荐的收益。关闭实际 socket pacing 及其 deadline/dependency guard 后，手机固定窗口的 FPS、显示 gap 和发送失败没有稳定改善；两轮保留组合的均值反而略高。此前异常 IDR 的巨大 pacing sleep overrun 在这四轮中都没有重现，因此也不能据本组断言关闭组合可以修复此前的 356 ms 停顿。

分析只读取已完成的四个 `matrix.json` 及各轮保存的 trace、手机/源端 SF、report、invocation，没有操作设备或修改运行代码。完整检查、分帧分布及 24 个输入文件的 SHA-256 在 [pacing-analysis.json](evidence/overnight-20261002/pacing-analysis.json)。此前异常的严格帧身份关联见 [long-stall-results-20261002.md](long-stall-results-20261002.md)。

实际控制按 **A-B-B-A** 核验通过：

| 轮次 | 实际 socket pacing | 实际 SocketVideoGate guard | native wire rate / catchup | native expected FPS | raw submit / phone FPS | 手机 buffer |
|---|---|---|---|---:|---|---:|
| pacing-01-double，A | 开 | 开 | 32 Mbps / 2048 bytes | 120 | 120 / 120 | 80 ms |
| pacing-02-nativeonly，B | 关 | 关 | 32 Mbps / 2048 bytes | 120 | 120 / 120 | 80 ms |
| pacing-03-nativeonly，B | 关 | 关 | 32 Mbps / 2048 bytes | 120 | 120 / 120 | 80 ms |
| pacing-04-double，A | 开 | 开 | 32 Mbps / 2048 bytes | 120 | 120 / 120 | 80 ms |

`--disable-socket-pacing-bundle` 实际只让矩阵不传 `--socket-pacing`。B 仍启动 native packetizer pacing；诊断 events 保持打开，所以 `SocketVideoGate` 对象仍存在，`guard=false`。B 同时取消实际 socket 等待、socket 整帧期限检查及依赖链 gate，是组合对照，不能将结果归因于仅少一层 sleep。native `catchup_bytes` 逐帧回读全为 2048，summary wire rate 全为 32M；A 的 socket pacer readback 为 32M/2048，B 的 pacer 状态为空。

其余共同配置为独立 VT 硬件编码器 720×1280 / 8M、120 Hz 手机 panel 与 content hint 120、FIFO、surface submit lead 0、async video、audio/touch 开启、decoder reanchor 关闭，无注入丢包及触控 exercise。原始帧预算只控制 submit，不限制 gRPC 采样。每轮 source restart、15 秒 warmup；真实 YouTube 播放状态与持续窗口验收通过，但媒体文件 FPS 和播放器质量仍未核验。四轮三项 binary SHA 相同，十项 source fingerprint 既逐轮不变，也四轮一致。

手机窗口使用自己首包后的 `[5,30)`，SF FPS 等于窗口内唯一 presentation 数除以固定 25 秒。只有 gap 两端都在窗口内时纳入主分布；跨边界 gap 单列。宿主窗口使用自己的首次 gRPC return 后 `[5,30)`，不把两端时钟当成同步，也不计算单程延迟。

| 运行 | SF presents | SF FPS | SF gap p99 / max ms | >50 / >80 / >100 ms | 宿主 capture FPS | 完整 AU 接收 FPS |
|---|---:|---:|---:|---|---:|---:|
| pacing-01-double | 1491 | 59.64 | 24.8640 / 66.3054 | 1 / 0 / 0 | 59.60 | 59.76 |
| pacing-02-nativeonly | 1489 | 59.56 | 24.8635 / 66.2983 | 1 / 0 / 0 | 59.64 | 59.64 |
| pacing-03-nativeonly | 1490 | 59.60 | 24.8626 / 66.2979 | 2 / 0 / 0 | 59.60 | 59.60 |
| pacing-04-double | 1495 | 59.80 | 24.8633 / 41.4352 | 0 / 0 / 0 | 59.80 | 59.84 |

A 的 SF 平均 59.72 FPS，B 为 59.58 FPS；宿主 capture 平均则是 59.70 与 59.62 FPS。这些差异很小，源端供给和真实视频阶段仍可能不同，两次重复不足以推断统计稳定的 A 优势。能直接支持的判断是：**B 没有形成可复现的用户可见收益，四轮都处于较顺畅的供给阶段。** B02 虽有 102.288 ms 的手机完整 AU 间隔和 107.323 ms 的 ready 间隔，SF 最大 gap 仍为 66.298 ms；供给/解码事件间隔不能替代显示 gap。

下表以宿主窗口内 capture 的唯一 screenshot PTS 严格关联 native `frame_id` 和 socket header 同帧，排除重复/缺失匹配。四轮分别配对 1490/1491/1490/1495 帧，无身份冲突或被拒绝配对；这是宿主 capture cohort，不是手机实际 display cohort。

| 运行 | native 累计实际 sleep p99 / max ms | native 累计额外 sleep p99 / max ms | native 单次 overshoot max ms | socket 首次→末次 send p99 / max ms |
|---|---|---|---:|---|
| pacing-01-double | 11.3682 / 39.986 | 4.4588 / 15.311 | 6.672 | 12.3488 / 41.236 |
| pacing-02-nativeonly | 12.8337 / 40.685 | 4.9913 / 17.260 | 5.327 | 14.1978 / 41.251 |
| pacing-03-nativeonly | 12.8530 / 33.130 | 4.2619 / 10.924 | 5.444 | 14.6747 / 33.721 |
| pacing-04-double | 10.9660 / 33.378 | 3.9072 / 11.106 | 4.053 | 13.3501 / 35.896 |

native 时差只使用 packetizer 自己的 steady clock，socket 首末 send 只使用 Python 自己的 host monotonic clock。两层等待有重叠，不能相加当成端到端延迟。关闭组合后 native sleep 和 socket 发送跨度尾部也未稳定缩短。A 的全轮 socket pacing 最大 wait overshoot 为 2.826/4.066 ms，明显低于此前异常轮全轮 20.886 ms；这些是全轮最大值，没有逐帧归属。

四轮已保存的 native source/output 数为 2113/2113、2113/2113、2108/2108、2122/2122。观察到的 native partial、native media incomplete、socket partial 和 socket media incomplete 全部为 0，手机 FEC expiry 也全部为 0。native 后续 summary 的 output deadline、budget drops 及 stdout blocked 都为 0。Swift 和 packetizer 最终 summary 缺失的限制仍保留；这些数字描述保存的诊断事件，不能拿来计算显示 FPS，也不能宣称已经证明所有末尾诊断完整。

**B 的 guard 相关计数不是等价指标。** `deadline_dropped_frames=0` 是关闭检查后无法发生该分支，不能据此称 B 的 deadline 保护效果与 A 相同；`completed_idr_frames=0` 也因为它只在 guard 开启时计数，不代表 B 没收到 IDR。B03 另有全会话 `dependency_dropped=4`，手机 frame5–10 没有 delivery；frame7–10 在首包后 0.083828 秒被已交付 IDR11 标为 superseded。该启动事件不位于 `[5,30)`，也不是 FEC expiry，但必须保留，不能写成全会话零依赖丢弃。四轮都有一次 codec input timeout，inbox overflow 2/2/1/2 次；固定窗口内没有 chain loss/recovery 事件，startup 与 steady 分开记录。

四轮 network feedback 均没有码率动作，保存的 interval target 都为 8M；native wire budget 始终 32M。没有证据说明自适应改变了这组对照。先前 frame300/477 的 deadline 失败仍是有效观察；本组没有相似的源端供给、raw pipe 和 pacing 极端尾部，不能用“这次没有失败”覆盖之前的结论。

本组不足以把推荐改成关闭 socket pacing 与 guard。若后续需要验证此前放大机制，应保留 guard，单独切换实际 socket pacing wait，并使用可重复、有界的宿主调度压力或供给停顿条件，再比较严格 frame ID 对应的 partial、expiry、sleep/send 尾部和独立 SF 固定窗口。该建议尚未作为新测试执行。

适用范围仅为 M1 真实公共 YouTube、物理 LAN、CPU 受限 OnePlus12 与匹配实验客户端。正式 App UDP 未发布，WAN/蜂窝/V50、光学显示、触控和声学 A/V 同步仍未验收；不能把本组 LAN 的顺畅结果推广为远程网络下关闭保护的依据。
