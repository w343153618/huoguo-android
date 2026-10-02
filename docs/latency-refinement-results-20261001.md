# 2026-10-01 晚至 10-02：真实视频缓冲、像素池与宿主调度

本轮建议保留 **80 ms 折中推荐，100 ms 流畅优先可选**。用户已明确允许超过原来的 80 ms 限制，依据实测取舍。100 ms 已进入播放时钟、实验探针和 App 选择界面；在独立实验包中完成编译、安装、选择、退出重开与读回验证。当前手机保留 80 ms，V50 默认仍为 80 ms，未发布正式升级包，也未重置已有用户的有效参数。

本轮没有达到持续 60 FPS。源端在没有抓屏、串流编码或手机接收时也出现长呈现空档；调整模拟器宿主调度后，源端供帧有改善，但最后两轮真实手机显示分别约 55.1 / 49.6 FPS，收益尚未稳定传到整条链路。不能把这些结果称为公网、移动流量或火锅 V50 验收。

## 环境与测量边界

- M1：6 核 / 16 GiB 的测试 AVD，物理 1080×1920、480 dpi、min/peak 120 Hz、`skiavk`、host GPU；当前读回见 [保留状态](evidence/latency-refinement-20261001/final-retained-state.json)。冷启动后会重新应用并验证 `skiavk`，尚未实现自动持久化。
- 手机：用户降频的 rooted 一加 12 PJD110，实际显示模式 120 Hz。CPU 上限会动态变化；本轮只读观察，没有升频、解除 CPU 限制或关闭温控。它不是已验证等价的 V50。
- 视频：实际 Morphe YouTube 播放公开视频 BBB；启动请求 seek 60 秒，此前界面观察到 1080p60 选项。未验证实际媒体文件 FPS 或精确播放位置，不能称为连续、内容完全一致的 60 FPS 源。自动触控演练未启用。
- 传输：独立实验 App / probe 配对，原生加密 UDP，经物理局域网 M1 `en7` 到手机 Wi-Fi；不是 NPS / Tailscale 公网测试。正式 App 的既有媒体链路未在本轮切换。
- 每个手机测试运行 35 秒，手机按自身首包后 `[5,30)` 统计；host 按自身首个 gRPC return 后 `[5,30)` 统计；source 使用独立约 32 秒 SF 窗口。不能跨设备相减这些计数或时间戳求丢帧率、单向延迟。
- SF 指唯一有效 actual-present 时间戳统计，不是光学测量，也不是独特视频内容帧数。VT submit→callback 含调度；手机 input→ready 含 codec 排队、调度和输出被观察时间，均不能称为纯硬件执行时间。

## 60 / 80 / 100 ms：六轮正反顺序

720×1280 编码、8 Mbps VBR 目标、32 Mbps wire pacing cap、120 FPS 编码上限 / Surface hint / 手机 panel。顺序为 60/80/100/100/80/60，每个条件两轮。所有源验证通过，未混入静止心跳样本。

| 缓冲 | 两轮手机 SF FPS | 合计 50 秒内 >100 ms SF 空档 | 两轮最大 SF 空档 | 取舍 |
|---|---|---:|---|---|
| 60 ms | 54.36 / 55.60 | 2 | 116.02 / 116.02 ms | 调度目标较早，但长停顿较多 |
| 80 ms | 54.08 / 55.00 | 1 | 82.86 / 124.31 ms | 当前折中推荐 |
| 100 ms | 53.88 / 53.92 | 0 | 91.14 / 66.29 ms | 流畅优先可选，目标多等待约 20 ms |

100 ms 两轮都没有 >100 ms 的 SF 空档，但供给节奏也发生变化，每档只有两个样本，不能把全部差异归因于缓冲。它没有提高实际 FPS。60/80/100 正向测试的 target−receive 中位数约 75.52/95.63/115.33 ms，验证设置确实进入时钟，没有被截断到 80 ms；这是调度目标，不能当物理播放或触控延迟。

稳态未观察到 inbox 断链、codec input timeout 或 FEC 失败；全程启动阶段仍有少量超时/overflow。音频全程 drop 计数保留，缺少稳态事件时刻和声学音画测量，不能用它们宣称音画同步已验收。完整逐轮数据见 [缓冲复核](evidence/latency-refinement-20261001/buffer-60-80-100-manual/buffer-offline-review.md)。

## 1080P 像素池对照：未证明收益

新增 `--pixel-pool manual|session`，默认仍为 manual。session 从 VT 会话取得像素池，严格验证 BGRA / 实际尺寸 / stride；没有把格式转换为 NV12，也没有取消必要的内存复制。

四轮原计划 ABBA 中，只有前两轮有有效真实视频源。manual / session 的手机 SF 分别 38.40 / 40.48 FPS，VT p50 17.955 / 17.823 ms，slot wait p99 均约 0.001 ms；未显示持续像素池等待。后两轮源验证失败、约 1 FPS 静止心跳，已从结论中排除，保留原始报告。一个有效 A/B 不足以建立 session pool 的稳定收益，因此不改变默认。见 [源验证复核](evidence/latency-refinement-20261001/pool-real-source-review.json) 与各目录 `steady-pipeline-analysis.json`。

## Source-only 宿主调度：有候选收益，仍有混杂

先在不抓屏、不串流编码、不发手机媒体时采集 source SF，35.3 秒约 45.5 FPS，仍有 31 次 >100 ms actual-present 空档。这说明停顿至少有一部分已存在于源端，无法全部归因于网络或手机。

仅修改 M1 测试模拟器 launchd `ProcessType`：Standard → Interactive → Standard 返回，各两轮 35 秒 Perfetto + SF。6 核、16 GiB、物理尺寸、120 Hz、renderer 和 AVD config 摘要相同，实际 spawn type 均回读。

| 条件（各两轮） | source 加权 SF rate | 合并 actual gap p99 | >100 ms 空档 |
|---|---:|---:|---:|
| Standard 初始 | 45.52 FPS | 114.44 ms | 63 |
| Interactive | 57.07 FPS | 36.09 ms | 3 |
| Standard 返回 | 51.45 FPS | 69.05 ms | 11 |

合并只连接各窗口内部的 gap 样本，没有平均 p99 或把窗口边界连接成停顿。返回轮经历源重开失败及额外准备时间，结果未回到最初基线；因此不能把全部差值视为调度设置的单因果效果。C2 长 runnable 等待下降，但 queue→done 超过 100 ms 的比例反而较高，不能宣称所有解码延迟改善。C2 配对已加 PID 限定，防止不同进程相同 PTS 被误配。见 [调度复核](evidence/latency-refinement-20261001/qos-offline-review.md)。

当前保留 Interactive 作为 M1 的下一轮实验候选，原 plist 的受限私有备份仍在主机。没有把 token 所在完整 plist 放入仓库。未更改 M5、NPS、Clash 或下载服务配置。

## 候选的最后两轮真实手机复测

720P / 8 Mbps / 80 ms / manual pool / 120 cap、hint、panel，真实 UDP 视频，两轮均通过完整手机运行和真实源验证。

| 指标 | 第 1 轮 | 第 2 轮 |
|---|---:|---:|
| source 独立全窗口 SF FPS | 53.74 | 45.10 |
| host 自身稳态 capture FPS | 55.16 | 49.60 |
| 手机自身稳态接收 FPS | 55.16 | 50.00 |
| 手机自身稳态 SF FPS | 55.08 | 49.64 |
| 手机 SF p99 / max | 49.72 / 132.58 ms | 82.88 / 149.19 ms |
| 手机 >100 ms SF 空档 | 1 | 6 |
| 手机 input→ready p99 | 66.40 ms | 98.46 ms |

第二轮源端、host 和手机接收同时变慢，手机处理长尾也增大。host 未显示持续 raw queue 或 native slot 积压，VT p50 均约 9.8 ms；不能据此排除其他宿主阶段。第一轮有一次首包后 16.668 秒的稳态 FEC expiry/reference loss，不能把它算成启动异常，也不能仅凭 expiry 判定为纯网络丢包。

所有 >100 ms 手机 SF 空档，在相应 PTS 接收窗口内均与完整 AU / codec input 供给空档重叠，支持供给间断参与停顿；尚没有 screenshot→codec→SF buffer 的完整身份链，不能称为最终单因果定位。没有同条件同步的旧 QoS 手机对照，因此当前不能宣称端到端稳定收益。完整阶段表与反例见 [手机复核](evidence/latency-refinement-20261001/qos-phone-offline-review.md)。

## 实现、留存与下一步

实际 UI 验证：选择 100 ms → 退出重开读回 100 ms → 选择 80 ms 并等待 UI 回读 → 退出重开读回 80 ms。先前过快 force-stop 的失败记录保留，没有据此把异步参数保存误判为产品缺陷。证据见 [UI 复测](evidence/latency-refinement-20261001/buffer-ui-persistence-retry.json)。实验包曾被手机使用时长功能暂停，只解除了该 App 的暂停提示，没有修改 CPU 降频或温控。

真实视频矩阵现在要求已知 PLAYING 的 before/after 状态及足够时长、有效的 source SF 进度，再纳入效果统计。完整接收循环成功仍与真实视频源成功分开记录，避免把静止心跳当高性能播放。这只验证两个状态快照与长窗口进度，不证明播放期间每一时刻的内容身份。

本轮校验：18 项实验客户端测试、8 项编码器参数/像素池读回测试、16 项 Perfetto 分析测试、两项 Java 播放时钟检查通过；Swift 编码器和独立 Android 实验包构建通过；真实 UI 保存读回通过；`git diff --check` 通过。原始脱敏阶段证据和测试编排脚本保存在 [本轮目录](evidence/latency-refinement-20261001/)，脚本摘要见 `runner-scripts/manifest.json`，不要对已有结果目录重跑覆盖。

外部运行依赖为用户 SDK（`~/Library/Android/sdk`）、原有 `android-remote/m1-compare` / AVD 运行目录、受限 launchd plist，以及 `/private/tmp` 中本轮编译的探针和 Perfetto 工具；脚本使用 canonical source，但不会把这些外部运行状态迁移或删除。当前健康检查 gateway 与 download 均返回 200，测试采集已结束；配置索引已更新到 [current-testbed.json](current-testbed.json)。

下一轮应优先缩小源端供帧及手机 input→ready 的长尾，再在同一内容进度与更完整 buffer 身份关联下复测 80/100 ms；继续提高缓冲无法补出缺失的源帧。稳定 60 FPS、触控到画面延时、声学音画同步、公网及 V50 仍未验收。
