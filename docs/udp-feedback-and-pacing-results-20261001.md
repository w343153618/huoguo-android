# UDP 反馈、关键帧发包和 LTR 实践

日期：2026-10-01。此记录是独立组件实验，不是正式 App 的升级发布。正式媒体服务和已保存的用户参数保留；本轮没有修改 NPS、Clash、M5、账户或 Mac 显示器配置。

## 已落实的改动

- 手机通过既有 AES-GCM UDP 会话回传 100 ms 区间的收包、组帧过期、参考链丢失、FEC 恢复和接收线程耗时；主机使用自身时钟的 ping/pong 测 RTT。只有匹配编码器 ACK 才把目标调整视作成功。降档、回升、冷却和本地恢复上限均有界；这不是完整 GCC，也不能用收发字节比冒充丢包率。
- 加入实际 UDP socket 的可选发包调度，计入加密、IPv4/UDP 和 FEC 开销。音频、触控 ACK 和 ping 不等待视频的 sleep，其字节费用进入下一次视频排程。
- 记录编码 AU 的 packetizer 入口、原始 PTS、FEC quorum、手机接收/解码输入、真实 SurfaceFlinger 呈现和 inbox epoch。各自只在同一个时钟内算间隔，跨设备通过 frame id 关联。`host_capture_us` 实际是读取已编码 AU 后的入口时间，不能称作 Android 捕获时间。
- 修复尾部 parity 误断链：每个 block 的原始 data 都已输出时，丢最后的可选冗余分片只降低 FEC 余量，不停后续 P 帧。缺原始 data、未知中间 block 或参考不匹配仍请求恢复。报告分别保留 `complete`（全部记录）和 `media_data_complete`（原始数据完整）。
- 可选 2,048 字节有限追赶额度，并在实际写出时结算，避免长期停顿后喷发整帧。默认仍是 0 字节严格模式。最大允许 4,096 字节；帧准入与发送期限仍为 80 ms，没有增加手机播放缓冲。
- 修正 RTT 冷启动误降档：最近 3 秒至少三个有效 pong，近期中位数、EWMA、最新原始值全部持续高于基线阈值才有 RTT 压力。实际组帧失败和本地发送压力独立响应。

实现入口为 `experiments/moonlight-v2/transport/android-udp/`；反馈及 socket 调度的协议说明在 [NETWORK-FEEDBACK-AND-SOCKET-PACING.md](../experiments/moonlight-v2/transport/android-udp/NETWORK-FEEDBACK-AND-SOCKET-PACING.md)。实验会话不新建用户账号，随机会话密钥只留在受限测试会话中，并由 runner 清理；报告只留有界性能元数据。

## 第一轮真实视频矩阵与故障证据

M1 的真实 Morphe/YouTube 视频到一加 15，通过家中 Wi-Fi、M1 的物理有线接口传输加密 UDP 音视频。USB 仅控制和读取手机。虚拟机仍为 720×1280；串流缩放至 540×960、目标 60 FPS、4 Mbps 平均编码目标、16 Mbps wire 发包预算、60 ms 播放缓冲。手机实际显示 90 Hz。每个条件两次 35 秒，第二次反序；不是相同编码字节的随机实验。

以下 FPS 来自独立手机 Surface 时间戳在第 5–30 秒的完整 25 秒窗口，不能用 codec 的 vendor callback 或活跃区间替代。

| 条件 | 第一次 FPS | 第二次 FPS | 稳态 >100 ms 呈现间隔次数，第一次/第二次 |
| --- | ---: | ---: | ---: |
| 诊断，无反馈/实际 socket 调度 | 55.84 | 56.40 | 0 / 1 |
| 网络反馈 | 55.64 | 56.40 | 0 / 0 |
| 实际 socket 调度 | 55.96 | 56.92 | 0 / 0 |
| 反馈 + 实际 socket 调度 | 56.08 | 56.76 | 0 / 1 |

完整记录在 [第一矩阵](evidence/udp-feedback-20261001/matrix/matrix.json)。这组小样本没有证明反馈或双层调度已经显著提升 FPS，更没有证明稳定满 60。

两个可核对的问题：

1. `diagnostics-01` frame 1773：AU 102,075 字节，共 120 个分片；只输出 117 个。理论 16 Mbps 序列化约 68.7 ms，实际输出跨度约 78.9 ms，随后撞上 80 ms 截止。最后一个 block 只收到 9 个分片，而恢复需要 10 个，缺少 data 与两个 parity，完整帧无法交付。此后 5 个依赖帧停发。新诊断进一步记录每帧计划/实际 sleep、最大超时、处理耗时和失败阶段；旧记录没有这些精细字段，不能把全部余量都确定归因于 sleep。
2. `paced-02` frame 714：128 个分片已输出 127 个，仅最后一个 parity 超时；手机已经成功交付关键帧，旧 guard 仍停掉 715–719 等新 IDR。这是已经修复的误断链。与之不同，`combined-02` frame 246 的原始数据并不完整，手机确实发生 assembly expiry，不能取消必要恢复。

第一轮 `feedback-01` 有 325 个有效反馈、34 个 pong，8 次码率调整全部获得匹配 ACK。但三个降档都由旧 RTT EWMA 启动残留触发，不能证明网络拥塞；新策略已修正。后来无压力样本保持 4 Mbps 是正确行为，不是反馈失效。

## 修复后的六轮真实视频结果

条件保持前述 90 Hz、4 Mbps、60 ms，源码和二进制 hash 在矩阵始末一致。[修复矩阵](evidence/udp-feedback-20261001/matrix-fixed-90hz/matrix.json) 保留全部成功与异常样本。

| 条件 | 两轮稳态 SF FPS | 两轮最大稳态间隔 ms |
| --- | --- | --- |
| 严格实际 socket 调度 | 56.68 / 55.56 | 77.44 / 133.09 |
| 2 KB 有限追赶 | 57.24 / 55.72 | 66.55 / 165.96 |
| 反馈 + 2 KB 有限追赶 | 56.12 / 56.76 | 77.44 / 66.38 |

`paced-01` 的 frame 375 在 socket 只缺最后两个 parity 时，手机成功交付并继续 376–380 的 P 帧，未触发参考丢失，直接验证了误断链修复。两个反馈轮共 655 个有效 HGUF、69 个 pong；初始约 52–54 ms RTT 很快回到约 5–9 ms，旧 EWMA 尚高时新门控正确不降档。

边界同样保留：`combined-credit-01` 的 frame 1746 计划 sleep 48.725 ms、实际 76.225 ms，尾部进入 socket 时已超期限，缺最后原始 data，仍需真正恢复。`paced-credit-02` 的 frame 229 为 130,110 字节 IDR，经开销变成 175,652 字节，仅理论序列化就要 87.826 ms；2 KB 追赶额度无法改变绝对超额。恢复控制实际将 4 Mbps 降为 3 Mbps并收到编码 ACK，231 IDR 恢复。

该轮最大 165.96 ms 间隔发生在另一个时段，并非 frame 229。651→652 的 encoded-AU intake 间隔为 103.365 ms、截图 PTS 间隔 30.758 ms；99.276 ms 的 PTS 空档属于 652→653。不能把不同帧对拼成同一事件。

更明确的上游空档在 `paced-02` 1665→1666：intake 127.978 ms、截图 PTS 119.044 ms，相关数据包完整，无 deadline/assembly/inbox 失败，gRPC collector 同窗口也有约 119 ms 空档。截图 PTS 是 emulator 估计的截图生成时间，不是视频文件的 MediaCodec PTS；目前只能把范围收敛到捕获/上游供帧，尚不能进一步认定 guest 解码器、VT 或宿主调度哪一个是唯一原因。

## 发送锁截止修补和最终 90/120 Hz 对照

只读审查另查到锁等待和 AES-GCM seal 后未复查截止时间。实际 sender 的假时钟/假 socket 回归在修前失败，修后获得锁及实际 send 前均复查完整 wire 费用；过期包拒绝，已消费 nonce 不复用。已开始的系统调用不能撤回，迟完成只审计。[修补证据](udp-sender-deadline-regression-20261001.md)。`max_send_syscall_ns` 包含检查与调用间的调度，不是纯内核耗时。

这份最终 source 冻结后，同一反馈+2 KB条件仅切换手机请求显示模式，两轮反序。[最终矩阵](evidence/udp-feedback-20261001/matrix-final-display/matrix.json)：

| 手机实际模式读回 | 第一次/第二次稳态 SF FPS | 最大稳态间隔 ms | 第二次组帧过期 |
| --- | --- | --- | ---: |
| 90 Hz | 55.92 / 55.84 | 88.50 / 122.02 | 1 |
| 120 Hz | 50.60 / 53.00 | 133.08 / 66.54 | 0 |

120 Hz 首轮有两次真实发送失败，不能把其低 FPS 全归给刷新率。但第二轮发送、组帧和 inbox overflow 都正常，仍只有 53.00 SF FPS：同一稳态窗口有 1,436 个提交 target、1,437 个 callback，仅 1,325 个 SF 新呈现。90 Hz 第二轮为 1,424 个 target、1,396 个 SF 呈现。窗口边界并非逐帧完全配对，不能把差值精确叫作某个环节丢帧数；它支持进一步隔离 decoder release → Surface 调度与帧率提示。

两个 120 Hz 样本的 mode readback 为 120 Hz，而 SF latency 头部为 16.667 ms，实际呈现间隔又有约 8.31/16.63 ms 格点。头部不是已验证的物理面板周期，不能据此宣称精确 VSYNC 冲突。所有 QTI vendor callback 时间戳回显提交 target，仍不能用 callback 当上屏时间。两轮未证明 120 Hz 更流畅，当前不把它升级为实验优选。

三组矩阵共 18 个 35 秒真实视频 UDP 样本，另保留一个前置 baseline；手机合成文件兼容性共 5 个样本。它们不支持稳定满 60/120 FPS 的结论。下一轮先补按截图 seq/PTS 关联的 gRPC→pipe→VT→输出事件，以及源 SF desired/actual/ready 三列和时钟校准；手机侧单独对照 Surface 排期策略，避免把源供帧空档与手机呈现合并都叫成网络抖动。

## 原生恢复和有限追赶的离线检查

修复后的 native packetizer 对 120 帧真实 H.264 合成码流，在无丢包、每个 block 丢两个 data 且乱序、2% 随机丢包且乱序三种模式下，离线解码均逐帧 hash 一致。它不是 VideoToolbox、真机或公网结果：[最终离线记录](evidence/udp-feedback-20261001/offline-native-catchup-2048-final.json)。

有限追赶模型不掩盖边界：100 µs/包的 sleep 超时模型中，严格模式与 2 KB 模式都能完成 120 包；另加每 10 包 2 ms 调度停顿时，两者都可能触及 80 ms，有限追赶可以多输出若干包，但不是任意停顿都能修复。实际发出时间的短窗口字节包络另有纯检查，不能把模拟结果称作 Wi-Fi 提速。

## Apple LTR 与手机解码的边界

默认 Baseline 硬件编码器的 `EnableLTR` 返回 -12900；专用低延迟 RTVC 可以启用，实际生成 ConstrainedHigh、最多 10 个参考帧的码流。在同一合成场景，有 ACK 的 LTR-P 刷新 11,045 字节，IDR 14,808 字节，减少 25.4%；离线丢掉 16 帧后恢复像素 hash 一致。无 ACK 的回退是非 IDR I slice，不能说成 LTR-P。详见 [Apple LTR 实验](apple-ltr-experiment-20261001.md)。

一加 15 的文件兼容性测试全部使用硬解 `c2.qti.avc.decoder.low_latency`、60 FPS 时间轴和 60 ms 缓冲：High+IDR 完整链 1,440 输入/1,439 callback；High+LTR 完整链相同，恢复链 1,120 输入/1,119 callback、保持原 PTS 缺口。回调吞吐和目标时间不等于真实显示 FPS，测试也没有 UDP 网络或声学音画采样。

未补 VUI 的合成 Baseline 控制组有约 170 ms 解码排队，而 High 组约 20 ms。当前真实 UDP 的 Baseline 编码流程已有低延迟 VUI 修补；不能从这个未补的控制组推断线上 Baseline 就有 170 ms 排队，也不能把差异全部归于 High Profile。

追加只补 VUI 的 Baseline 控制后，同一组 1,440 个媒体 AU、PTS 和 keyframe 标记完全一致：硬解 1,439 个 callback、late=0，稳态排队 p50 19.97 ms、p99 22.27 ms；未补组为 169.97/172.36 ms。这支持 SPS 低延迟限制确实影响该手机的解码排队；仍不是正式视频的测量，也不是物理上屏 FPS。[手机五组对照](evidence/udp-feedback-20261001/ltr-phone/comparison-20261001T032556Z.json)。

LTR 暂不直接接入 UDP：当前 BaselineGate 只接受单参考帧、上一个参考 id 的依赖链。需要升级长期参考依赖图和真正接收/解码 ACK，再验证手机及 V50，才有上线条件。

## 复现与验收范围

源码和报告均在当前工程。外部状态依赖为 `~/Documents/ChatGPT/others/android-remote/m1-compare`，Android SDK 为 `~/Library/Android/sdk`，nanors 来自固定提交的 Moonlight cache。二进制位于 `/private/tmp/huoguo-udp-paced-credit/host`，不提交。

```sh
python3 scripts/probes/run_udp_feedback_matrix.py \
  --packetizer /private/tmp/huoguo-udp-paced-credit/host/h264_udp_packetizer \
  --encoder /private/tmp/huoguo-udp-burst-encoder \
  --cases paced paced-credit combined-credit --rounds 2 --display-hz 90 \
  --output-dir docs/evidence/udp-feedback-20261001/matrix-fixed-90hz
```

IP、接口和手机 serial 可以用参数指定；报告会保存实际参数、源码和二进制 hash。已有输出拒绝覆盖。当前结论只覆盖 M1、真实视频、一加 15、物理 Wi-Fi LAN。它不代表 NPS 公网、蜂窝网络、真实 NAT/ICE、火锅 V50、触控到真实画面的延迟或声学音画同步验收；正式 App 的 TCP 媒体没有在这一轮变成已发布的 UDP 产品。

最后检查：73 项 UDP Python 检查、48 项实际 Java 有界反馈/诊断检查、12 项 native 诊断检查、native CTest 与解码/FEC回归通过。实验后 [健康读回](evidence/udp-feedback-20261001/m1-after-feedback-readback.json) 为 6 核、16 GiB、720×1280、320 dpi，常亮设置有效；原 gateway/download 均 loopback HTTP 200。固定手机会话、报告和 codec fixture 已清理，线上账户保留。
