# 原生 UDP 分帧、FEC、期限及恢复实验

这是独立组件原型，所有 socket 只能连接/绑定 `127.0.0.1`，没有公网部署模式，不修改网关、NPS、手机、账号或生产配置。`SessionTag` 仅区分合成数据命名空间，**不是鉴权、密钥或加密**。不能把这个原型直接暴露公网；后续接入需要已认证的 Tailscale/WireGuard，或成熟加密会话与反重放处理，并保持国内直出检查。

依赖使用上级 `upstreams.json` 固定的 Moonlight nanors，构建脚本检查 Git 提交及工作区干净状态，再编译上游源文件。没有另造 Reed-Solomon 实现，也没有复制第三方源码进入工程。使用时需遵守上游许可证。

`HGUD` 是为这次隔离测量编写的最小访问单元封装，**不是 Moonlight/GameStream 或标准 RTP 的兼容实现**。借用 nanors 并不等于已复用 Moonlight 完整视频协议；选择生产协议还需要完整握手、加密、拥塞反馈和手机端互操作验证。

## 组件行为

- 完整视频访问单元分成块，每块最多十个数据片，加两个 Reed-Solomon 校验片。每片包含版本、帧号、块/片号、大小、源时间、参考帧关系和播放期限。
- 单个媒体 UDP payload 最大 `56 + 1024 = 1080` 字节；尾块动态缩小片大小，避免把很短的编码帧填充成完整 1024 字节片。
- 每帧最多 1 MiB，接收端同时最多八个未完成帧；已处理帧号缓存最多 256 个。拒绝不一致元数据、非法长度、过长期限及错误命名空间。
- 从输入到期的画面不会继续排队恢复。接收方可以越过缺失旧画面处理新的 IDR，但不能把缺少参考的 P 帧送解码。当前依赖模型保守，不能代替完整 H.264 DPB 语义解析。
- 参考损失后发送 IDR 请求。早期合成 `udp_media_probe` 对照每事件最多三个相同序号的控制包，间隔 20 ms，第一次模拟 5 ms 控制延迟；允许注入反馈丢包。后续真实 H264 桥增加最多三轮请求和退避，已确认 IDR 超预算时发有界编码适配通知并停止重复要求同样的大 IDR。反馈重复序号被发送端去重；没有媒体重传或无限重试。
- 按实际 IP 数据报字节进行节奏控制，调度停顿后重锚到当前时刻，不补发一串“追赶突发”。此原型没有完整带宽探测、拥塞控制或 NAT 打洞实现。

`20%` 只指十个完整数据片配两个校验片时，校验数据占原数据的比例。短帧、尾块、包头和小包数量可能使实际开销显著更高。统计中的 `packetized_overhead_percent` 包括本协议头和内层 IPv4/UDP 头；**不包括以太网、WireGuard、QUIC 或中转的额外封装**。所以 4 Mbps 视频编码率和 4 Mbps 线路预算不是同一个设置。

## 复现

在工程根目录运行：

```sh
python3 experiments/moonlight-v2/transport/build.py
```

源码依赖默认读取 `~/.cache/huoguo-v2-sources/moonlight-common-c`，缺失时本脚本直接退出，不自行联网。CMake 可由既有 `~/.cache/huoguo-v2-tools/bin/cmake` 提供；构建输出默认在系统临时目录 `huoguo-udp-native-build`，可用 `--source`/`--build` 显式指定。主项目不需要安装这些实验二进制。

组件测试包括 66 种双丢片、跨帧乱序、重复、三个丢片超过预算、过期后拒绝复活、整个参考帧消失、非参考损失、后续 IDR 修复、长度/期限/内存边界和节奏控制。另一项测试验证现有 `h264` framing、真实 NAL 的 IDR/reference/SPS/PPS 分类及截断拒绝。

实际 localhost UDP 对照：

```sh
python3 experiments/moonlight-v2/transport/run_matrix.py --seconds 5
python3 experiments/moonlight-v2/transport/run_feedback_matrix.py --seconds 3 --rounds 2
python3 experiments/moonlight-v2/transport/verify_h264_fixture.py
python3 experiments/moonlight-v2/transport/verify_feedback_limit.py
```

这些测试读取/写入系统临时二进制和本目录的无敏感性能报告。在限制 socket 的环境中，localhost bind 会被拒绝；不要更改生产服务来绕过这个限制。

## 2026-10-01 的实验迭代及结果

1. 最初固定 1024 字节片。4 Mbps/60 目标的短帧填充，加上校验及包头，实际所需超过预算，第一次三秒测试只生成约 160 帧。改为动态短片后，无损五秒 localhost 测试达到 300/300；12 Mbps/120 达到 600/600。见 `evidence/loopback-20261001.json`。这两项都是合成内容，没有手机显示或真实视频捕获。
2. 随机丢包下，仅 FEC 不足以保证参考链。遇到单块超过恢复预算，等待下一周期 IDR 会放大不可解码间隙。加入即时有界 IDR 反馈后，再做 4/8/12/24/40 Mbps × 60/120 FPS × 开/关反馈 × 两轮，共 40 个三秒样本；媒体丢包 2%，反馈丢包 20%，每两帧注入一次片序交换。
3. 上述测试又发现低码率/120 时“编码 payload 固定占线速 62%”仍不够：短块的两个校验片比例更高。因此按实际分片与 IP 头预算选合成 payload，大约留 15% 调度余量，保留旧版本结果在 `evidence/feedback-loopback-before-wire-budget-20261001.json`，重跑结果在 `evidence/feedback-loopback-20261001.json`。**新旧数据的 payload 大小改变，不能当成同一编码画质的公平 A/B。**

同版、同 seed、同 payload 的 40 Mbps/120 对照：

| 轮次 | IDR反馈 | 接收可交付帧/生成帧 | 最大连续不可交付帧 | 已完成恢复时长 |
|---|---|---:|---:|---:|
| 1 | 关闭 | 333/360 | 27 | 232 ms |
| 1 | 开启 | 347/360 | 13 | 115 ms |
| 2 | 关闭 | 315/360 | 32 | 273 ms |
| 2 | 开启 | 336/360 | 13 | 98 ms |

这说明及时反馈在该合成参考链中有价值，也说明 **80 ms 到期才开始恢复，仍会出现超过 80 ms 的播放中断**。不能据此说已达到真实 120 FPS 或已经消除掉帧。`recovery_max_ms=0` 也不一定代表没发生故障：样本结束前未恢复的事件是截尾数据，需要结合 `idr_requests` 和末尾不可交付帧判断。最后补充了 `recovery_unresolved_at_end` 与 `unresolved_recovery_elapsed_ms` 字段，以及起始 IDR 全丢后的恢复请求组件测试，旧矩阵尚无这两个新字段。三秒、两 seed 只用于定位机制，不能替代长时公网统计。

另有反馈全丢失验证：三秒 40 Mbps/120、2% 媒体丢包、100% 反馈丢包。检查反馈每事件最多三次，没有强制 IDR 或无限重传；周期 IDR 仍作为兼容恢复方式。见 `evidence/feedback-all-lost-20261001.json`。

部分样本源生成数受系统调度影响，反馈开/关不一定完全相同；不能只比较输出 FPS 就归因于反馈。`recovered_data_shards` 包括因乱序提前重建的片，不能解读成真实网络丢包数。合成 IDR 与 P 帧相同大小，未模拟真实 IDR 突发/编码开销，后续真实视频实验必须补测。

实际 H264 小样验证：libx264 生成 320×240、60 帧的合成画面，经原有视频 framing 输入、localhost UDP 收发及重组，再交给 ffmpeg 解码。无损与注入 2% 丢包＋重排均 60/60，输出字节 SHA256 和逐帧解码 MD5 与原始 Annex B 一致。见 `evidence/h264-fixture-roundtrip-20261001.json`。这个测试以尽快送完整文件的方式运行，**不是手机实时 60 FPS 验收，也不是 YouTube 视频**。

## 给现有硬件 H264 的接入口

`h264_udp_bridge` 的命令参数为：

```text
h264_udp_bridge WIRE_BITS_PER_SECOND LOSS_PERCENT REORDER_EVERY_N_FRAMES FEEDBACK_0_OR_1
```

- stdin：直接接本工程 `HostHardwareSession.channel('video')` 的现有字节协议：`h264`，尺寸消息，或 64 位 flagged PTS + 32 位长度 + Annex B 访问单元。
- stdout：已经通过 FEC、期限及参考链检查的 Annex B，可写临时文件并用 `ffmpeg -f h264 -i returned.h264 -f null -` 解码检查，也可接下一阶段原生解码器。
- stderr：JSON 事件与最终统计；收到 `{"event":"request_idr", ...}` 时，驱动应向同一 HostSession 的 control channel 发送 `b'\x11'`。桥本身不能伪造 IDR；只有编码器真正发出的 NAL type 5 才能解除参考链等待。
- 构建中已有硬件管线支持上述 reset/IDR 控制。驱动必须另行 drain 音频/控制通道，避免本地阻塞，结束时按 HostSession 生命周期关闭。桥不会读取账号或私钥。
- 从 NAL 识别 IDR 和 reference 属性，缓存 codec-config 的 SPS/PPS，并随 IDR 再发送。原型只支持 Annex B，拒绝误当成 AVCC 的输入；每访问单元的 1 MiB 上限比现有 App 的 8 MiB 更严格。
- 桥的 `captureUs` 实际是桥收到编码访问单元的单调时间，统计范围是 **桥接接收→UDP→重组输出**，排除模拟器捕获、硬件编码和此前排队。原始 PTS 只用于识别配置标志，输出裸 Annex B 没有音画时钟，不能用于宣称完整音画同步。

下一次应接真实 YouTube/抖音输出，分别检查无损哈希、故障时可解码帧/编码器 IDR 响应、捕获到输出的原始时间戳以及最大冻结。随后才进入手机 UDP 解码/显示、受认证隧道保护的公网线路及 V50 测试。要提前恢复而非等待全部 80 ms，候选是依据到达抖动的较短片等待、有限且仍赶得上期限的选择重传、动态 FEC 和参考恢复；不能用无限 ARQ 保证实时流畅。

## 真实 M1 HostSession 驱动（已写好，需单独运行）

```sh
python3 experiments/moonlight-v2/transport/run_host_h264.py \
  --duration 20 --fps 60 --video-bitrate 4000000 --wire-bitrate 8000000 \
  --loss 0 --reorder 0 --feedback 1 \
  --source-label 'caller verified real YouTube video' \
  --output docs/evidence/real-host-udp-result.json
```

驱动不打开或更换视频，也不停止现有预览；调用者先确认 M1 安卓当前在播放什么并负责避免并行 VT 性能测试。它创建本次 HostSession 的私有 socket pairs，drain 视频/音频/控制，把视频送桥；IDR 反馈经 control `0x11` 返回真实编码器。

原始 Annex B 与重组 Annex B 只写 `/private/tmp` 下的私有临时目录，使用 ffmpeg 解码后删除。安全 JSON 包含源帧/解码帧数、逐帧 MD5 序列摘要、字节摘要、解码错误分类、源码时间戳间隔/到达间隔/源 age，以及 SPS 的 profile_idc、level_idc、max_num_ref_frames、max_num_reorder_frames、max_dec_frame_buffering 等。trace 未找到的字段保留空列表，不能解释成零；JSON 不保存完整日志、媒体或账号。

驱动写完时仅通过 Python 编译和 CLI 参数检查；是否已完成真实视频运行，以其对应证据文件和调用者报告为准。它测量的是 M1 本机真实编码源与本机 UDP/ffmpeg，仍不测手机渲染或公网。

## 真实接入后的 pacing 修正

真实 Host 接入第一次出现零丢包仍大量过期，已定位并修复原型的未来排队债务 bug：过期而不发送的包不再推进 pacer。新增整帧预算预检与完整FEC/仅数据片成本、IDR大小、排队/调度延迟和拒绝原因统计。参见 [80ms与IDR恢复记录](DEADLINE-RECOVERY.md)。这些修正不是生产UDP方案已验收的声明。

独立离线复杂 H264 对照，不采集安卓画面、不使用 VT：

```sh
python3 experiments/moonlight-v2/transport/verify_complex_h264.py
```

它通过 CPU libx264 产生 540×1200/60、4 Mbps VBR 的普通运动及固定 seed 轻噪声，再按实时帧节奏喂本机桥，对照8/12/24 Mbps线上预算。SPS共用profile66/level32/refs1，但与真实VT的VUI信令不完全相同；源码和证据都记录这个区别。

## 有界恢复与超预算 IDR 反馈

后续真实 12 Mbps 桥样本里，源编码 59.04 FPS，而九张 IDR 的全帧成本超过 80 ms，后续参考链不能恢复。旧 `idr_requests=1` 只是一个未结束的接收器参考链事件，不能解释为已经修复。新的 `recovery_feedback.hpp` 对普通丢包提供最多三轮请求，每轮三个去重控制包，轮间退避 250/500 ms；理论发送成本已超 80 ms 的 IDR 则转为 `encoder_budget_feedback` 元数据，不不断重复更大的 IDR。详见 [预算、恢复状态与测试边界](DEADLINE-RECOVERY.md)。

可在另一个临时构建目录验证，不覆盖正在运行的桥：

```sh
python3 experiments/moonlight-v2/transport/build.py --build /private/tmp/huoguo-udp-recovery-build
python3 experiments/moonlight-v2/transport/verify_recovery_feedback.py \
  --binary /private/tmp/huoguo-udp-recovery-build/h264_udp_bridge
python3 experiments/moonlight-v2/transport/verify_h264_fixture.py \
  --binary /private/tmp/huoguo-udp-recovery-build/h264_udp_bridge \
  --output experiments/moonlight-v2/transport/evidence/h264-recovery-regression-20261001.json
```

第一项集成脚本只构造 NAL 头与参考链用于验证控制状态，主体不是可解码 H264；确定性 CTest 还验证所有反馈包都丢失时总请求有界。第二项才使用可解码的 CPU-H264 并比较帧摘要。两者均不是手机/WAN 验收。新桥命令参数与 Host 输入输出 framing 不变，`run_host_h264.py --bridge` 可以选新桥；适配通知不自动改变真实编码参数，安全概要仍进入 summary。
