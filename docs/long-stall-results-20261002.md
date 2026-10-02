`buffer-real-01-80` 的 356.34 ms 最大显示空档，关联到了宿主供帧、原始帧管道等待、发送期限失败及手机 ready 延迟共同出现的一段异常。该轮没有自适应降码率；仅增加 20 ms 播放缓冲无法覆盖其中约 395.53 ms 的完整 AU 供给间断。随后两轮 120 秒实测均未重现超过 100 ms 的手机显示空档，因此这段异常也不能代表整个夜间测试的持续性能。

本报告只读取已经落盘的元数据，没有操作手机、模拟器或生产服务。机器可读结果和输入文件 SHA-256 在 [long-stall-review.json](evidence/overnight-20261002/long-stall-review.json)。逐轮汇总口径与 [real-video-summary.json](evidence/overnight-20261002/real-video-summary.json) 一致。

短轮以手机首包后的 `[5,30)` 为窗口，长轮以 `[5,110)` 为窗口。SF FPS 是窗口内唯一 SurfaceFlinger presentation 数除以固定 25/105 秒；gap 只统计两个端点均位于窗口内部的相邻 presentation，跨边界 gap 单列。宿主使用自己首次 gRPC 返回后的同长度窗口，两个设备的窗口没有同步，不做跨设备时钟相减。Source SF 的全采样均值只作旁证，不与手机固定窗口直接相减计算丢帧。

| 运行 | 编码尺寸上限 / 目标码率 | 实际 buffer | 手机 SF FPS | SF gap p99 / max ms | >100 ms | 宿主捕获 FPS |
|---|---|---:|---:|---:|---:|---:|
| resolution-03 | 1920 / 12M | 80 | 59.32 | 24.86 / 82.87 | 0 | 59.24 |
| resolution-04 | 1920 / 12M | 80 | 59.12 | 33.14 / 82.86 | 0 | 59.20 |
| buffer-real-01 | 1280 / 8M | 80 | 53.24 | 78.06 / 356.34 | 7 | 53.52 |
| buffer-real-02 | 1280 / 8M | 100 | 59.60 | 24.86 / 66.29 | 0 | 59.64 |
| buffer-real-03 | 1280 / 8M | 100 | 59.60 | 24.86 / 58.01 | 0 | 59.56 |
| buffer-real-04 | 1280 / 8M | 80 | 59.16 | 24.86 / 91.15 | 0 | 59.24 |
| long-01，120 s | 1280 / 8M | 80 | 59.3143 | 24.87 / 91.15 | 0 | 59.4476 |
| long-02，120 s | 1280 / 8M | 100 | 59.6667 | 24.86 / 74.58 | 0 | 59.6952 |

这些运行都要求 native/phone FPS 120、raw submit budget 120；该 budget 控制原始帧提交，不控制 gRPC 采样。二进制 SHA 分组一致，逐轮 `source_unchanged=true`。实际 source 的重启、请求 seek 和 15 秒 warmup 不能证明播放器每轮落在完全相同的媒体状态，播放器质量及文件 FPS 仍未核验。尤其 resolution 两轮是 1080×1920 / 12M，buffer 轮是 720×1280 / 8M，不能据这组数据推导提高分辨率会减少停顿。

异常轮全程有 14 个 gRPC 捕获间隔超过 100 ms，其中宿主 `[5,30)` 内有 9 个，最大 197.833 ms。Source SF 全采样均值为 51.517 FPS，同样显示这轮源端供给较慢。宿主逐阶段的尾部如下；每项按唯一 screenshot PTS 和 capture sequence 配对，缺失的 raw submit 不补造。

| buffer-real-01 宿主阶段 | p99 ms | max ms | >100 ms 次数 |
|---|---:|---:|---:|
| FIFO enqueue→dequeue | 14.9386 | 66.902 | 0 |
| Python raw pipe write | 29.5526 | 120.397 | 3 |
| native encoder slot wait | 0.001 | 0.034 | 0 |
| VT submit→callback | 13.4403 | 19.984 | 0 |
| gRPC return→encoded socket end | 58.0234 | 188.740 | 5 |

Pipe write 与 native payload read 重叠，不能把两者加成两次串行拷贝耗时。VT 回调包含线程调度，依然没有记录到能单独解释 356 ms 停顿的编码长尾；slot wait 也没有显示持续饱和。FIFO 队列出现 10 次替换，26 个 capture 的 pending count 为 2，进一步表明这轮 raw 管道存在短时积压。

最大 SF gap 位于手机首包后 6.477847→6.834182 秒。SF 不携带 PTS，下面的链条是同手机时钟的时间关联及严格帧身份对应，不能宣称已找到 SF 两个端点实际显示的那两帧。

1. 手机完整 AU `frame299→303` 的间隔为 **395.533 ms**，成功输入间隔为 **396.973 ms**。这两个 AU 经唯一 frame ID 加相同 PTS 验证，对应宿主 `capture_seq309→314`；同宿主 capture 时钟内 gRPC 端点间隔为 347.874 ms、encoded socket 端点间隔为 378.274 ms。这里比较的是各自设备的间隔，没有计算单程延迟。
2. 中间 `frame300` 是 IDR，对应 `capture_seq310`。其前一次 gRPC 返回间隔 157.482 ms，raw pipe write 83.240 ms。后续 `seq311` 又等待 FIFO 62.770 ms、pipe 108.858 ms；`seq313` FIFO 等待 66.902 ms。对应帧的 VT submit→callback 约 10–12 ms。
3. native packetizer 为 `frame300` 计划 136 shards，只写出 119，`media_data_complete=false`、`failure_stage=write_deadline`。Python 实际 socket 只发出 107 datagrams，记录同一个 `frame_id` 的 `socket_frame_deadline`。手机也记录 `frame300` 的 `assembly_deadline`，从首个 shard 到过期决策为 96.358 ms。发送端的记录已足以证明本机没有发完整帧，不能把此事件称为公网丢包。
4. `frame301/302` 有唯一 source 记录而没有 output，随后 `frame303` 为新 IDR，并成功发送、接收、入解码器。恢复链及后续 native summary 的 dependency drop 计数支持依赖帧被丢弃的解释；不存在的 output 记录本身仍不是通用零发送证明。
5. 手机 `frame299` 在 6.415521 秒成功 input，到 6.820477 秒才 ready，**input→ready 404.956 ms**。`frame303` 同时在 6.812494 秒 input、6.821946 秒 ready。这表明旧帧的 ready 延迟与恢复供给重合；input→ready 包含 codec 队列和线程调度，尚不足以定位为硬件解码器执行了 405 ms。源码中 `video-render` 是独立输出线程，持续 `dequeueOutputBuffer(10000)`，不会因为 UDP input worker 等待新 AU 就停止主动 drain；格式也已请求标准及 QTI low latency。没有手机线程/codec 执行 trace，仍不能区分 output 线程被延迟与 codec 内部等待后续输入。Vendor render 时间常回显请求 target，本分析不用它代替 SF display。

第二个 256.871 ms SF gap 位于手机首包后 10.629371 秒，完整 AU `frame476→478` 的间隔为 312.823 ms，中间 IDR `frame477` 也失败：native 计划 131 shards、只写出 101，`reserve_deadline`；socket 只发出 85 datagrams，`socket_frame_deadline`；手机同帧组装过期。这说明最大的两次显示断档都发生了实际本机整帧发送期限失败。其余 5 个 >100 ms SF gap 同样重叠 AU/input/ready 供给间隔，但没有相邻的 receiver expiry 记录，不能把所有停顿归为同一个原因。

自适应策略没有在这轮压低发送预算。65 个 network interval 的 encoder target 全是 8,000,000 bps；`downward_updates=0`、`upward_updates=0`、`actions=[]`、host encoder updates 为 0。两次 local recovery 都只请求 IDR，保持 8M。34 个已保存 native summary 的 wire budget 都是 32M，socket pacer readback 也是 32M。代码中的 feedback 只修改编码器 target，不动态修改 native 或 socket pacer 的 wire rate。异常之后的 feedback interval 确实记下 receiver/local socket pressure，但没有形成降码率动作。

`frame300` 名义全帧 serialization 38.689 ms，留有约 41.014 ms admission margin；原计划累计 pacing sleep 8.357 ms，实际累计 **79.193 ms**，单次 overshoot 最大 8.690 ms。`frame477` 计划 sleep 10.947 ms，实际 79.016 ms，单次 overshoot 最大 21.611 ms。后续 native summary 已覆盖到 frame1836，`stdout_blocked_us=0`、`stdout_max_blocked_us=0`；因此这两个事件没有记录到 stdout `EAGAIN` 引起的管道背压。Socket 全轮也出现最大 20.886 ms wait overshoot，且 AES seal 的全轮最大值为 13.172 ms、socket syscall 最大 3.869 ms；这些全轮最大值没有逐帧归属，不能冒称都发生在 frame300。

证据支持“固定预算下，细粒度 pacing 等待及宿主调度长尾耗尽 80 ms 整帧期限”作为放大机制，尚不能区分 timer oversleep、线程被抢占或宿主其他工作。两层 pacing 同时启用是可检验候选。现有 `run_phone_udp.py` 的 `--socket-pacing` 省略开关会同时关闭 `SocketVideoGate` deadline/dependency guard，因此是一个组合对照。精确下一步应保持 guard、native 32M/catchup2048、encoder8M/raw120/native120/phone120/buffer80 等不变，只改变实际 socket pacing wait；仍记录整帧失败、超时、source SF、host capture、SF 固定窗口及逐帧 socket 等待。此处只提出对照，没有修改实验脚本或执行新设备测试。

两轮长测的 SF 覆盖完整，且所用 105 秒窗口都没有 >100 ms gap。100 ms 的这一轮显示 FPS 较高、最坏 gap 较小，但宿主捕获也高约 0.248 FPS；两轮顺序与真实视频状态仍有混杂，无法保证增加 20 ms 缓冲在每种源端空档下都有同等收益。长轮 native RX 记录分别裁掉 6082/6142 个事件，保留历史从手机首包后约 51.379/51.354 秒开始。因此完整 AU rate 留为缺测，早期缺少 FEC/expiry 记录不能解释为没有故障；完整的 successful input 和 ready 观察仍可以分别统计。Swift 最终 trace summary 全部缺失，不能据 retained trace 证明 native trace 没有诊断丢弃。

本结论属于 M1 真实公共 YouTube / 物理 LAN / CPU 受限 OnePlus12 / 匹配实验客户端。120 是请求 native/phone/submit 配置，并不代表收到或显示 120 FPS。正式 App UDP 尚未发布，手机蜂窝、外网/V50、光学显示及触控、声学 A/V 同步也尚未在本轮验收。
