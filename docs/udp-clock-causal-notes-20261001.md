# UDP 公共播放时钟与显示停顿的因果边界

日期：2026-10-01（Asia/Shanghai）。本文记录只读分析；不把时间相关性当作已经验证的唯一因果。工程源文件、APK、设备和服务不由本文变更。

## 分析对象与范围

分析对象是 `evidence/native-iteration-20261001/phone-private-udp-av-540p4M-loss2.json`，以及同名 `-phone-surface.json`、`-source-surface.json`。

这是一加 15 通过家中 Wi-Fi 连接 M1 有线接口的加密 UDP 音视频组件测试，真实 Morphe/YouTube Big Buck Bunny 视频，串流 540×960、目标 60 FPS、4 Mbps、16 Mbps 发送突发预算、60 ms 手机缓冲。每 50 个视频 datagram 在发送 socket 前人为丢一个；音频未注入丢包。该丢包分布不能代替移动网络或公网自然丢包。结果也不能代表火锅 V50、NPS 公网或 ICE 打洞验收。

## 已观测到的现象

从手机记录的 `scheduled_ns`、`pts_us`、`received_ns`、`decoder_ready_ns` 推导以下量；计算使用同一手机单调时间轴，不需要把 M1 时钟直接与手机时钟相减。

| 指标 | loss2 结果 |
| --- | ---: |
| 配置播放缓冲 | 60 ms |
| 请求显示目标减完整画面处理时刻，p95 / p99 / 最大 | 420.6 / 507.6 / 560.2 ms |
| decoder 输入入队至输出可取，p95 / p99 / 最大 | 166.7 / 263.8 / 505.2 ms |
| decoder ready 至 callback 到达，中位数 / 最大 | 0.65 / 5.62 ms |
| 约 5.978 s 时请求显示时间与 PTS 的映射单次增加 | 413.8 ms |
| 手机 SurfaceFlinger 最大呈现间隔 | 499.1 ms |
| 源端 SurfaceFlinger 最大呈现间隔 | 95.4 ms |
| AAC worker late drops / PCM late drops | 87 / 2 |
| 注入音频丢包 | 0 |

这里的“请求显示目标”是 App 传给 `releaseOutputBuffer(index, targetNs)` 的目标，不是独立观测的实际光子呈现时间。decoder 输入至输出可取包含排队与缓冲周转，不能全部解释为硬件解码计算耗时。

5.978 s 左右的请求时间映射上跳后，手机 SurfaceFlinger 出现以下三个相邻大间隔（相对首个视频包）：

| 间隔起止 | 实际呈现间隔 |
| --- | ---: |
| 6.002–6.500 s | 497.8 ms |
| 6.500–6.977 s | 477.0 ms |
| 6.977–7.476 s | 499.1 ms |

这些时间窗口内，请求目标相对完整画面处理时刻最高达到 560.2 ms，decoder 排队至输出可取也达到数百毫秒。它们支持“公共时间轴后移与显示缓冲回压参与停顿”的候选解释，但没有建立逐源帧 PTS 到真实呈现时间的一一映射。

必须进一步区分：`scheduled_ns - pts_us * 1000` 不是独立记录的 `PlaybackClock.offsetNs`。`MainActivity` 在 clock 返回过期目标且没有下一个可取输出时，会把最终 target 临时设为 `now`；这也会使上述推导量上跳，但不会改变公共 offset。只有新增 clock 内部快照、原始 clock target 和最终 clamp 标记，才能区分持久 reanchor、临时 clamp 与 decoder hold。本文没有据此宣称实际公共 offset 已增加 413.8 ms。

报告中 1908 个 codec callback 全部精确回显 requested target，其中 1907 个不满足记录中的因果时间校验。因此不能用这些 callback 证明真实显示延迟、声画同步或触摸到光子延迟。独立 SurfaceFlinger 间隔仍可用于观察停顿与帧率，但其 ring 采样也有容量、层重建和 ADB 读取延迟边界。

## 代码中的候选反馈链

`PlaybackClock.videoDeadline()` 在连续晚帧时先增加最多 30 ms 的 `decoderHoldNs`。若仍明显落后，它把公共 `offsetNs` 重锚到晚解码帧。这个 offset 推移量本身没有 80 ms 上限。`observe()` 后续只按墙钟时间的 5% 逐渐回收多余偏移，数百毫秒的推移因此可能持续数秒。

Android 官方说明，SurfaceView 的未来呈现时间会让 buffer 等到时间过去且 Surface 不再使用才归还 codec；Surface 顺序处理也可能挡住后续画面。因而数百毫秒的未来目标可能占住 codec/Surface 缓冲，形成回压。[MediaCodec 官方说明](https://developer.android.com/reference/android/media/MediaCodec#releaseOutputBuffer(int,%20long))

现有候选链为：

1. 启动积压或局部停顿使帧进入延迟状态。
2. 晚解码恢复把公共播放时间轴向后推。
3. 新帧被提交给 Surface 的未来时间过远，buffer 周转受限。
4. 同步视频输入等待可能挡住 UDP receive，视频、音频和触控反馈共用接收入口因而受影响。
5. 公共 offset 同时影响 AAC PCM 输出的等待目标；AAC render 在等待中持有输出 buffer，可能进一步限制 AAC decoder 输入。

第五点仍需要逐 AAC 记录的等待指标验证。当前 `worker_late_drops` 同时包含工作队列记录过期、PTS 过旧和 decoder 输入超时，无法把 87 次全部归给一个环节。`AudioTrack.getTimestamp()` 提供呈现或已承诺呈现的估计，不能代替声学测量。[AudioTrack 官方说明](https://developer.android.com/reference/android/media/AudioTrack#getTimestamp(android.media.AudioTimestamp))

这不是已经证实的“音频硬件时钟反向拖动视频”。`observeAudio()` 只在公共时钟尚未初始化时建立映射；AudioTrack 队列尾部估算不会持续调整公共 offset。主要候选方向是视频反馈影响公共时钟，随后影响声音调度。

## 启动偏移是另一项独立问题

loss0、loss2 都出现启动时源 PTS 在短时间内快速前进而接收侧批量赶上的现象。loss0 的前若干帧，源 PTS 推进约 371 ms、手机完整画面处理时刻只推进约 142 ms，请求目标因此升至约 280.6 ms；loss2 启动也升至约 319 ms。首帧锚可能使用了编码/解码器启动积压中的旧帧，之后按 50 ms/s 左右慢降。

这个现象不宜直接命名为网络抖动。关闭 decoder reanchor 也不会修改 `observe()` 的启动映射和慢降；它必须留作另一次单变量实验。

## 新矩阵首轮的反例与修正

`ingress-clock-matrix/phone-udp-ingress-sync-legacy-1.json` 首轮没有复现 AAC late drops（worker 与 PCM 均为 0），FEC 帧过期与参考丢失也均为 0，但手机 SurfaceFlinger 在 5.776–6.408 s 出现 632.3 ms 间隔。严重显示停顿因此不能一概归为音频过期或 FEC 丢参考。

这一轮约 5.725 s 入队的旧 AU，到 6.376 s 才有输出可取。其最终请求时间映射上跳 587.9 ms，紧接下一帧又下降约 565.3 ms、随后恢复原值。这不符合仅按 5% 墙钟慢降的持续 offset 调整，进一步暴露出“临时 target=now clamp 被误标为公共 clock shift”的风险。旧 loss2 的多帧长平台仍可能包含真正 reanchor，但需要内部时钟事件或单变量对照验证，不能仅靠这些衍生量确定。

## 当前矩阵的区分目标

`PlaybackClock(bufferMs, avSyncOffsetMs, decoderReanchorEnabled)` 的默认选项为 true，保留既有行为。false 只关闭晚 decoder 调整公共 offset/hold 的反馈，不改变 arrival 映射、启动慢降、音频共享时钟或校准。

当前同一 APK 矩阵比较：sync＋legacy、async＋legacy、async＋arrival-only，并重复后两种。第一组至第二组区分同步接收入口被视频输入等待堵住的影响；第二组至第三组区分 decoder 推移公共时钟的影响。没有把线程隔离和时钟算法同时视作一个变更。

保留真实视频段、清晰度、目标/发送预算、缓冲、FEC 和人为丢包规则，分别观察启动 0–5 s 与稳态 5–30 s。关键指标包括：源/手机实际 SurfaceFlinger 节奏与间隔、真实接收/重组完成至 worker 开始等待、输入等待、decoder 输出可取时间、公共 offset 内部快照及调整原因、请求目标超前程度、原始 clock target 与最终 target clamp，以及分阶段 AAC 丢弃。不要把 worker 开始时刻当作网络 arrival 重新锚定，不把 clamp 误称作公共 clock shift，也不要把平均 FPS 提升自动称作低延时或唇音同步验收。

## 五轮完成后的结果与下一步

已读取 `evidence/native-iteration-20261001/ingress-clock-matrix/udp-ingress-matrix-comparison.json` 和五轮原始报告。它们是同一 APK、同一真实视频来源的顺序测试，但并非完全相同的编码字节；最终自适应目标降至 2.2–2.6 Mbps，源端实际 cadence 58.0–59.1 FPS，也应作为比较边界。

| 模式 | 手机 5–30 s 完整窗口 FPS | 该窗口内部端点间最大 SF gap | AAC worker late / PCM late |
| --- | ---: | ---: | ---: |
| sync / legacy | 49.80 | 632.3 ms | 0 / 0 |
| async / legacy 1 | 48.40 | 1111.9 ms | 2 / 4 |
| async / arrival-only 1 | 50.08 | 1098.2 ms | 0 / 2 |
| async / legacy 2 | 48.04 | 1095.9 ms | 0 / 2 |
| async / arrival-only 2 | 48.00 | 83.0 ms（边界漏计，见下文） | 9 / 1 |

最后一轮全程实际存在 2107.5 ms 的 SF gap，区间为 4.396–6.504 s。比较脚本只保留两个端点均在 5–30 s 的 gap，因而漏掉了窗口开始后仍持续约 1.504 s 的冻结。其完整窗口 FPS 48.00 已包含这段没有新画面的时间。不能只看 83 ms 或窗口 `over_100ms=0` 就称这一轮已经消除停顿。后续窗口统计应保留原始相邻 gap 与窗口重叠的长度，并同时报告全程最长间隔。

所有 async 轮次的接收处理最大时间下降到约 1.8–5.3 ms，视频 worker 平均等候约 0.18–0.21 ms、最大约 3.3–4.8 ms。这说明线程隔离达成了缩短媒体入口处理的目的；但四轮均有一次容量为四帧的队列溢出、清空四帧并重新等 IDR，整条链仍存在长停顿。默认时钟与关闭 decoder reanchor 的模式都出现长 gap，尚没有一致证据证明某个 clock 模式已解决这个问题。

更直接的共同故障来自 host `native_events`：约 6–7 s，IDR 的 AU 达到 126–135 KB 左右，完整 wire 约 171–183 KB（均为十进制）。在 16 Mbps wire-burst 预算下，序列化约需 85–91 ms，超过 80 ms whole-frame 准入期限，被拒绝后停止发送依赖帧，等待更小的新 IDR。arrival-only 2 的一个 host summary 更明确记录了源端继续产生 56 帧、输出 0 帧、依赖丢弃 54 帧，随后完成恢复。这建立了“host 发送断供确实发生”的证据，并对长显示停顿提供共同候选解释；仍不能把所有中短间隔都归给它。

decoder input→output 的数百毫秒或数秒，也可能包含流水线等待后续输入到达后才吐出最后一帧，不是硬件单帧计算耗时。单纯调 clock 不能补出发送端未送来的依赖画面。

建议下一轮保留 4 Mbps 平均目标，只把 wire-burst 预算从 16 提至 32 Mbps 做短局域网 A/B，区分“关键帧整体超过准入预算”与手机侧调度因素。这不是新增 32 Mbps 画质档位。若 host 准入拒绝、依赖断供和长 SF gap 随之消失，再处理公网真实容量下的关键帧大小约束、恢复策略和拥塞控制；局域网提升突发预算不能自动作为移动网络推荐。另需保留生产时钟默认值，直到因果更明确且音画同步验收完成。
