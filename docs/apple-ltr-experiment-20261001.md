# Apple LTR 独立可行性实验（2026-10-01）

本轮证明了一个较窄但实际可用的事实：当前 M1 的 Apple 专用低延迟 H.264 会话接受 LTR 属性，应用反馈一个已经离线接收并解码的参考帧后，可以产生真正引用该长期参考的 P slice。刻意删除中间 16 个编码帧后，FFmpeg 解码得到的后续像素与完整码流逐帧一致。尚未验证手机、V50、真实 YouTube、网络丢包或 UDP 整体体验。

所有编码进程已经退出。没有替换生产 binary，没有操作手机、服务、AVD 或 `run_phone_udp.py`，没有把 LTR 接入当前传输协议。源 RGBA 自动删除；明确保留的压缩 fixture 在 `/private/tmp/huoguo-ltr-phone-fixtures-20261001`，权限为目录 700、文件 600，未放入 Git。

## API 和硬件证据

本机 SDK `VTCompressionProperties.h` 提供 macOS 12 起的 `EnableLTR`、`RequireLTRAcknowledgementToken`、`AcknowledgedLTRTokens` 和 `ForceLTRRefresh`。应用应把接收端确认过的 token 返回编码器，不能把编码回调发出 token 当成接收端确认。[Apple 专用低延迟编码及 LTR 说明](https://developer.apple.com/videos/play/wwdc2021/10158/)、[ForceLTRRefresh API](https://developer.apple.com/documentation/videotoolbox/kvtencodeframeoptionkey_forceltrrefresh)。

| 会话 | 实测 LTR 属性 | 实际编码/解码结果 |
| --- | --- | --- |
| 原默认硬件 Baseline，LTR 关闭 | 未设置 EnableLTR | 72 个 AU 正常离线解码；SPS profile 66、参考帧上限 1 |
| 原默认硬件 Baseline，显式 LTR 开启 | set/read 均 -12900（不支持） | 严格失败，无回退成功声明 |
| 专用低延迟 + ConstrainedHigh + 显式 LTR | set 0、read 0、readback true | 72 个 AU 正常输出；SPS profile 100、constraints 0x0c、参考帧上限 10 |

专用会话的 `UsingHardwareAcceleratedVideoEncoder` 读回仍为 -12900，所选 `com.apple.videotoolbox.videoencoder.h264.rtvc` 注册行也缺少硬件标志。因此本实验没有宣称硬件属性读回 true。只有显式 `--enable-ltr true --low-latency-mode true --ltr-allow-hardware-wrapper true` 才允许额外的实验 gate：`RequireHardware=true` 创建成功、准确匹配该 RTVC ID、缺少注册标志且 Apple 文档声明该模式使用硬件。`.rtvc.sw` 不匹配。证据中同时保留 unsupported、null 和这条文档合同推断。默认 gate 与默认 LTR=false 保持原样。

沙箱内最后一轮在创建会话时失败（普通 -12908、专用 -12902）；按同一授权范围独立运行后的真实成功结果另行保留。创建失败也没有被覆盖成成功。

## 合成码流结果

输入是 FFmpeg `testsrc2`，540×960，60 FPS，4 Mbps VBR；一轮 72 帧。先送入并实际离线解码前 24 帧，然后确认 token 8，对应第 8 帧（PTS 1133333 µs）。第 24 帧提交 ACK，第 40 帧提出刷新。丢帧恢复解码器只收到第 0–23、40–71 帧，共 56 帧。

| 第 40 帧刷新方式 | AU 字节数 | 完整 trace 的 slice 类型和引用 | 独立丢帧解码 |
| --- | ---: | --- | --- |
| 不提交 ACK，ForceLTRRefresh | 14334 | NAL 1，`slice_type=2`：非 IDR I slice | 56 帧全部与完整解码对应像素一致 |
| 提交 ACK，ForceLTRRefresh | 11045 | NAL 1，`slice_type=0`：P slice；L0 修改 idc 2，`long_term_pic_num=8` | 56 帧全部与完整解码对应像素一致 |
| 提交 ACK，ForceKeyFrame | 14808 | NAL 5，`slice_type=2`：IDR I slice | 56 帧全部与完整解码对应像素一致 |

这一个场景的 LTR-P 比同位置 IDR 小 25.4%。不是对真实视频的压缩收益承诺。每份 trace 解析整个 72-packet 码流并定位第 40 packet；没有把所有 NAL 1 当作 P slice。无 ACK 的实际输出为非 IDR I，与本机 SDK 描述的无 ACK 时 IDR 不完全一致，保留这个差异。只证明本次删除区间后的像素恢复，不推断任意缺失或重配置都可以恢复。

LTR-P 完整码流 SHA256：`0bccc7f78c90458f82c40c0bff6a080735fc34d69e34cde6d0f624eaefb8c983`。每个 RGB24 解码帧分别计算 SHA256；报告 `complete_rgb_sha256` / `recovered_rgb_sha256` 是这些十六进制帧 hash 串联后的 SHA256，两个集合数量不同所以汇总 hash 应不同。实际验收用恢复集合与完整集合对应帧的 hash 全量比较。

最新成功证据：[apple-ltr-fixture-retained-external.json](evidence/native-iteration-20261001/apple-ltr-fixture-retained-external.json)。最后沙箱失败证据：[apple-ltr-fixture-retained.json](evidence/native-iteration-20261001/apple-ltr-fixture-retained.json)。此前严格硬件 gate 的拒绝和尝试记录也保留在同目录。

## 手机 FileProbe 所需 fixture

压缩 fixture 的头为 `h264` + 大端 `u32 0x80000000, width, height`；后续每个记录为大端 `u64 flags/PTS, u32 payloadLength` + Annex B H.264。bit 62 表示 config，bit 61 表示 keyframe 标记（本轮标记为 true 的 AU 均包含 NAL 5），其余低 61 位是 PTS µs。与既有 `FramedH264Fixture.parse` 相同。

建议先用这些文件验证 High+LTR 解码，再考虑传输协议改造：

| 私有文件（共同目录 `/private/tmp/huoguo-ltr-phone-fixtures-20261001`） | 内容 |
| --- | --- |
| `low-latency-ack-ltr-complete-1cycle.h264framed` | 72 AU、约 1.2 秒、完整参考链 |
| `low-latency-ack-ltr-recovery-1cycle.h264framed` | 56 AU、保留原 PTS 缺口、刻意丢掉 16 AU |
| `low-latency-ack-ltr-complete-20cycle.h264framed` | 同一完整序列重复 20 遍，1440 AU、约 24 秒 |
| `low-latency-ack-ltr-recovery-20cycle.h264framed` | 同一丢帧序列重复 20 遍，1120 AU、约 24 秒 |

20cycle 文件只扩展解码排队样本，并不是 24 秒独立捕获。每次循环从原 IDR 开始，config 只送一次，PTS 单调递增。恢复文件仍保留 16 帧缺口，平均输入约 46.7 FPS；不能按每秒 60 个输出判它失败。其余 noACK、ACK-IDR 和默认 Baseline-IDR 控制组也各有 complete/recovery、1/20cycle 文件。

完整 20cycle 文件 SHA256：`07917d39499cfa2a35b611371fd8c96d75f3c2f2624a1cc13e491b8aed6f84ee`；恢复 20cycle：`943df882ff27abd9cae3b9ed06c95fe7c01923c8c6aaaf9790bb79bbe02fcfa8`。实际 Java parser 验证全部 16 文件通过；这不等于 Android MediaCodec 已通过。手机 FileProbe 属于真机 synthetic fixture 测试，也不属于 YouTube 或 UDP 验收。

## 重现与局部检查

仅独立编译到临时目录，保持生产 binary：

```sh
xcrun swiftc -O -module-cache-path /private/tmp/huoguo-swift-cache \
  scripts/probes/emulator_hardware_encoder.swift -o /private/tmp/huoguo-ltr-encoder
python3 scripts/probes/apple_ltr_fixture.py \
  --encoder /private/tmp/huoguo-ltr-encoder \
  --output docs/evidence/native-iteration-20261001/apple-ltr-fixture-retained-external.json \
  --retain-fixtures /private/tmp/huoguo-ltr-phone-fixtures-20261001 --fixture-cycles 20
python3 -m unittest discover -s tests -p test_apple_ltr_fixture.py
javac -d /private/tmp/huoguo-ltr-framed-parser \
  experiments/nps-transport/phone/FramedH264Fixture.java \
  tests/java/local/remoteandroid/direct/AppleLtrFramedFixtureProbe.java
java -cp /private/tmp/huoguo-ltr-framed-parser \
  local.remoteandroid.direct.AppleLtrFramedFixtureProbe \
  /private/tmp/huoguo-ltr-phone-fixtures-20261001/*.h264framed
```

这些程序仅使用合成媒体，没有网络、账号或捕获。若沙箱拒绝硬件会话，需在既有授权的本机实验范围内运行，不能改成软件编码并称硬件实验成功。运行时要与真机 VT 轮次错开，避免两路编码的资源混杂。

本地实验输入控制头与 RGBA 帧相同：20 字节 `>IIQI`，width/height 置 0，payload 长度 12，随后 `>IQ`。type 3 携带已发出且由应用确认过的 token；type 4 携带 0 请求刷新。只有 service + 显式 LTR 开启才接受，token 上界 Int64、ACK 待提交队列最多 64、历史最多 4096。此接口仅为本地 pipe fixture，不是新增公网 ACK 协议。

当前 UDP BaselineGate 和上一参考帧链不能正确表示 profile 100、10 个 DPB reference、长期参考 token 与恢复依赖图。生产接入前必须升级参数集能力、依赖图和接收端确认语义，并验证手机丢失参考帧时的真实恢复；本轮没有放松该 gate。

## 一加 15 合成文件解码组件验证

随后在用户授权的 USB 一加 15 上，使用已安装的 `CodecFileProbe` 连续执行四轮文件回放：60 FPS 上限、60 ms 缓冲、scheduled release。每轮文件通过 stdin 写入固定 app-private `codec-test.h264framed`，app UID 10498、mode 600、restorecon；手机实际读取的整文件 hash 均与离线材料一致。四轮结束后固定测试文件删除并 readback absent；现有 runner 自动删除测试 report。未改正式 App 或用户账号，未使用公开 ADB 临时目录。

每轮都使用 `c2.qti.avc.decoder.low_latency`，报告 hardware=true、无 failure，完成 bounded drain。渲染回调指标如下：

| 合成文件控制组 | 输入 AU / 渲染回调 / 过期丢帧 | 文件输入率 / 回调率 | 稳态 input→ready p50 / p99 |
| --- | --- | --- | --- |
| 原始 Baseline-IDR，未补 VUI | 1440 / 1388 / 42 | 60.000 / 58.232 次每秒 | 169.97 / 172.36 ms |
| 专用低延迟 High + IDR | 1440 / 1439 / 0 | 60.000 / 59.998 次每秒 | 20.14 / 22.41 ms |
| 专用低延迟 High + LTR 完整链 | 1440 / 1439 / 0 | 60.000 / 59.996 次每秒 | 20.01 / 22.27 ms |
| 专用低延迟 High + LTR 丢 16 AU | 1120 / 1119 / 0 | 46.657 / 46.644 次每秒 | 20.16 / 287.63 ms |

这是 **真机 synthetic file codec 组件测试**，没有网络、音频、模拟器源或实际屏幕显示 FPS 测量。所有 `vendor_render_ns` 都出现请求目标回显，不能据此估算真实上屏时间；回调率也不能当作 panel FPS。没有在手机上采像素或 hash，不能把离线 FFmpeg 的逐帧像素等价说成手机像素等价。完整链两组的末尾均差一个回调，保持实际数值，不以补帧声明 1440/1440。

恢复组 input→ready 超过 100 ms 的记录恰好 20 个，全部是每个 72 帧循环的第 23 帧——其后测试刻意不给第 24–39 帧，直到第 40 帧才继续投喂。该长尾与人为 PTS/输入缺口一致，不能把 p99 287.63 ms 解读成 LTR-P 第 40 帧解码很慢。

原始 Baseline SPS 的 `bitstream_restriction_flag=0`；High 的 restriction=1、`max_num_reorder_frames=0`。**当前真实 UDP 编码链已有另一层 Baseline SPS VUI 低延迟补丁，本节未补 VUI 的 Baseline 文件不等同于该链路**。不能据这一次顺序执行的四组结果推断正式串流会 holding 170 ms，或证明 High profile 本身降低了延时。冷启动、执行顺序及 SPS/profile/码流差异仍混杂。

报告索引：[四轮 manifest](evidence/udp-feedback-20261001/ltr-phone/matrix-20261001T032556Z.json)、[组件比较](evidence/udp-feedback-20261001/ltr-phone/comparison-20261001T032556Z.json)、[实际 SPS 初始 trace 字段](evidence/udp-feedback-20261001/ltr-phone/sps-initial-trace-metadata.json)。

另已离线准备严格单变量的 Baseline VUI 控制：只改 SPS config，1440 个媒体 AU 字节、PTS、keyflag 都与原始 20cycle 文件相同。原始 72 帧与补丁后 72 帧的软件 framemd5 逐帧相同，实际 Java parser 通过；补丁文件 SHA256 `211539788ba0fab859b17cbd18d470b1834cd919bdf312c7389426bb6c77884b`。这项准备的证据为 [1cycle 补丁与软件解码](evidence/udp-feedback-20261001/ltr-phone/baseline-vui-patch-single-cycle.json)、[20cycle 字节等价验证](evidence/udp-feedback-20261001/ltr-phone/baseline-vui-patch-20cycle.json)。手机对照的结果另行记录，不能将仅准备阶段算成实测。

随后的独立空档中，这个 **Baseline + VUI-only 控制文件实际又跑了一轮手机 CodecFileProbe**，同样 60 FPS / buffer 60 / scheduled，仍由相同高通硬件解码器处理。1440 AU 输入、1439 回调、过期丢帧 0；文件输入率 60.000、回调率 59.994 次每秒。稳态 input→ready p50 19.97 ms、p99 22.27 ms，超过 100 ms 的记录为 0。只改 SPS 的 Baseline 达到与两个 High 控制接近的组件结果，支持 VUI 缺失与前一未补 Baseline 的 holding 有关；这一次追加对照不能消除执行顺序、热身等时间混杂，也不能宣称真实 UDP、像素等价或实际 panel FPS。

第五轮仍验证全文件 hash、UID/mode/restorecon，无异常；结束后固定 fixture 清理并 readback absent。证据：[第五轮 manifest](evidence/udp-feedback-20261001/ltr-phone/matrix-20261001T033241Z.json)、[第五轮摘要](evidence/udp-feedback-20261001/ltr-phone/1-baseline-default-idr-vui-patched-complete-20261001T033241Z-summary.json)。这项结果说明当前已有的 VUI 补丁值得保留，并没有形成必须更换 High profile 的依据。
