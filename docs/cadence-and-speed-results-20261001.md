# 1080P：源端恢复、硬件编码速度与手机显示节奏

日期：2026-10-01。延续 M1 实例的物理 1080×1920 / 480 dpi、6 核、16 GiB、host GPU。本轮使用真实 Morphe YouTube 公共 BBB 视频，每轮重新打开同一 URL、请求 seek 60 秒、预热 8 秒。媒体播放状态有数值确认，但没有逐轮独立验证媒体文件帧率或每帧内容。

## 测量范围

路径为 M1 有线 en7 / 192.168.9.128 → 家庭局域网 → 一加 15 Wi-Fi / 192.168.9.6。实验媒体使用 AES-GCM UDP、FEC10+2，Apple VideoToolbox 硬件 H.264 和手机硬件解码。保持 1080×1920、目标 8 Mbps、60 FPS cap、60 ms 缓冲、FIFO 原始帧队列。音频与原生触控协议开启，但本轮不测光学触控延时或声学音画偏差。

手机 SF 固定比较窗为首个 server packet 后 `[5,30)` 秒；host 编码阶段窗为首个 gRPC return 后 `[5,30)` 秒，二者是独立窗口。相同 host screenshot PTS 可以连接本机阶段；不把跨机器时间相减，也不按最近时间连接 SF buffer 与 codec。SF timestamp 不是光学面板测量，Java codec callback 更不代表实际显示。

本轮不是公网、蜂窝或远程 V50 验收。正式客户端的媒体方案未因实验自动升级，M5、NPS、Clash 未调整。

## 源端暂停与恢复

45 秒轻量 video / graphics trace 与 40 秒 fence 采样，两者退出 0。数字 graphics trace 覆盖 45.205 秒，没有观察到目标 frame generation reset。原始 trace 仅保留在 owner-only 私有临时目录。

[最终源端分析](evidence/cadence-resume-20261001/resume-source-01/resume-analysis-final.json)与[简洁摘要](evidence/cadence-resume-20261001/resume-source-01/resume-numeric-summary.json)：

| 数值 | 第 1 轮 | 第 2 轮 | 第 3 轮 |
| --- | ---: | ---: | ---: |
| 确认暂停后的新 Queue marker 静默下界 | 2.071 s | 2.085 s | 2.103 s |
| 恢复命令边界内 Queue 数 | 3 | 2 | 3 |
| 稳态完整同 identity 数 | 201 | 178 | 210 |
| 稳态 Queue 间隔 p50 | 16.253 ms | 16.048 ms | 16.123 ms |
| 同 identity Queue→Latch p50 | 40.697 ms | 40.157 ms | 40.957 ms |
| 同 identity Queue→Present marker p50 | 43.039 ms | 42.562 ms | 42.939 ms |

三轮真正恢复首帧均拒绝：`resume_boundary_queue_ambiguous`、`accepted_cycle_count=0`。host 命令窗口约 67–83 ms，条件时钟映射区间宽 2.258584 ms。没有把命令返回后的第一个候选替换成真正首帧。

稳态 185/201、150/178、196/210 个完整 identity 在自身 Queue 与 Latch 之间已经出现后续 Queue；后续 Queue 数的中位数都是 2。约 40 ms 是重叠管线中的等待区间，不是串行每帧 40 ms 的 GPU 计算耗时。目标呈现时刻、readiness、backpressure 和线程调度尚未分离，精确 producer buffer depth 仍未知。

## 苹果硬件编码速度优先

独立实验编码器加入可选 `--prioritize-speed true|false`。省略不调用 setter；显式 false 与 true 均须独立 native encoder。报告严格区分 supported、setter、readback 和 CFBoolean 类型。

[四帧合成能力读回](evidence/cadence-resume-20261001/encoder-capability-pipe.json)确认本机 AVE H.264 encoder 支持该属性；默认读回 false，true 设置与读回均成功，false 也可成功。合成帧只验证属性接受，不算流畅度证据。前一次使用 `/dev/null` 作 native 输出目的地的失败也保留，不替代成功管道测试。

真实手机 A/B/B/A 使用同一新 encoder binary，默认组不设置属性，B 仅设置 true：

| 顺序 | 实际手机 Hz | 设置 | 显示 FPS | 显示间隔 p99 / max | VT submit→callback p50 / p99 |
| --- | ---: | --- | ---: | --- | --- |
| A1 | 90 | 默认 | 57.48 | 51.48 / 99.58 ms | 17.906 / 25.135 ms |
| B1 | 90 | speed=true，读回确认 | 57.44 | 44.26 / 121.71 ms | 17.857 / 24.278 ms |
| B2 | 90 | speed=true，读回确认 | 58.68 | 33.27 / 55.46 ms | 17.846 / 23.537 ms |
| A2 | 90 | 默认 | 58.84 | 33.20 / 88.52 ms | 17.883 / 24.874 ms |

未观察到稳定 FPS 或常态编码等待收益。VT submit→callback 是异步处理与调度间隔，不能称纯硬件引擎耗时，也不能把 17.9 ms 当成串行吞吐极限。保持生产默认设置。

## 实际 60 Hz 与 90 Hz

程序选择同分辨率 mode 7 / 60 Hz 后，原客户端播放期间实际仍为 mode 8 / 90 Hz。独立 SF 周期元数据为 11.111111 ms，画面间隔常呈约 11/22 ms。只提出 60 FPS 或 60 Hz 偏好不代表手机已实际切换。

[显示策略数值快照](evidence/cadence-resume-20261001/phone-display-policy.json)显示应用基准投票为 90 Hz、render range 0–90 Hz，physical range 0–165 Hz；60/90/120 等同分辨率模式在同 group。不能据此声称物理面板硬锁 90 Hz，也不能单用 `mIgnorePreferredRefreshRate` 解释显式 mode 请求被转换的原因。

临时设置 system min/peak 为 60，实际 mode 7 和独立 SF 16.666666 ms 周期均确认；finally 精确恢复 min=null、peak=165.0，两轮分别验证恢复：

| 实际模式 / 旧客户端 | 解码输入 FPS* | 解码 ready 记录率* | SF 显示 FPS* | 显示 p99 / max |
| --- | ---: | ---: | ---: | --- |
| 90 Hz / A1 | 58.24 | 58.24 | 57.48 | 51.48 / 99.58 ms |
| 90 Hz / A2 | 59.32 | 59.32 | 58.84 | 33.20 / 88.52 ms |
| 60 Hz / 第 1 次 | 58.52 | 58.52 | 53.44 | 49.79 / 99.56 ms |
| 60 Hz / 第 2 次 | 58.56 | 58.40 | 54.12 | 49.79 / 82.97 ms |

*同一 phone 窗口 `[5,30)`，各阶段按自身 timestamp 独立计数。没有 codec→SF buffer 的 exact identity 桥，不用计数差计算精确掉帧。两次 60 Hz 均无 native FEC frame expiry / reference loss、decoder input timeout 或 worker expired frame。它们支持调查显示阶段，但不能证明全部停顿都来自同一个代码点。

[手机 cadence 离线分析](evidence/cadence-resume-20261001/phone-cadence-analysis.json)：90 Hz 默认两轮只有 3.642% 的相邻目标显示间隔短于名义周期；60 Hz 两轮为 52.152%。目标间隔与截图 PTS 间隔完全相同的 pair 占 96.7%–97.9%。在 60 Hz 的严格“周期减 1 ms”阈值下，仍有约 36%–39% 的短间隔。不规则 capture PTS 被映射进目标时钟是有效线索；固定周期元数据不包含实际刷新相位，本轮不作 SF 槽分箱或帧身份推断。

## 版本一致性与实验包

最初 `phone-global-60hz-submit8` 只在 host 请求了 8 ms，旧手机 client/probe APK 均不含 submission 字段，报告也无执行读回。因此该轮明确标为 `invalid_treatment_not_deployed`，不用于提交策略收益比较。

新客户端使用固定独立 applicationId `local.remoteandroid.direct.experiment`，独立 instrumentation 为 `local.remoteandroid.phoneprobe.experiment`。原正式 App 和原 probe 保留。两者已安装 APK 指纹与固定字段检查见 [匹配安装](evidence/cadence-resume-20261001/matched-client-install.json)。第一批首次运行在配置文件建立前失败，四行全部保留，无可用 FPS；后续补充独立 files 目录初始化和失败非零退出。第二批两轮因 Activity 仍指向旧产品 package 而未建立串流，也全部保留并排除。三个 instrumentation 入口改为从 target context 获取启动 package，重新构建、安装独立 probe 后，才取得以下有效结果。

Runner 现在先核验安装 instrumentation target，再写配置；正数提交提前量只允许独立实验客户端，报告必须回读请求值与固定执行状态。Matrix 不再将失败或缺失报告当作成功，也不导出失败条件的性能摘要。私有 session、APK 和签名文件不进入 Git。

独立构建与测试入口，必须成对使用：

```sh
./gradlew --offline -PprobeApplicationId=local.remoteandroid.direct.experiment :app:assembleRelease
python3 scripts/probes/build_phone_transport_probe.py --experimental-client \
  --udp-native-library /private/tmp/实际实验目录/libhuoguo_udp_fec.so
```

两个 APK 分别位于 `app/build/outputs/apk/release/app-release.apk` 与 `experiments/nps-transport/phone/build/experimental/phoneprobe.apk`，都是忽略的构建产物。默认产品构建须省略 `probeApplicationId` 并重新构建；不要把独立实验 APK 当作用户升级包。UDP matrix 增加 `--experimental-client --surface-submit-leads 0 8`，维持每轮公开视频重开、相同实际刷新率和缓冲，核对手机 `surface_submit_status` 才接受比较。

## 匹配版本的显示提交对照

[有效对照与阶段摘要](evidence/cadence-resume-20261001/matched-submission-comparison.json)对应 [matrix](evidence/cadence-resume-20261001/matched-global60-targetfixed/matrix.json)。两轮 exit 0，source fingerprints 不变，actual mode 7 / 60 Hz，instrumentation target 和提交参数回读均通过。同一独立 App APK / probe APK；与上文旧客户端六轮版本不同，不把跨版本差异当作单变量结果。

| 同版本条件 | 手机提交读回 | SF 显示 FPS | SF gap p99 / max | 输入→取出解码输出 p50 | 就绪→release p50 |
| --- | --- | ---: | --- | ---: | ---: |
| 原 release 路径 / lead 0 | `disabled_existing_release_path` | 52.40 | 49.91 / 82.99 ms | 21.18 ms | 0.00 ms |
| 接近 target 前 8 ms 再 release | `applied_bounded_wait` | 52.20 | 49.92 / 83.19 ms | 50.75 ms | 15.44 ms |

SF 沿用首包后 `[5,30)` 秒的独立完整窗口。codec 阶段筛选每帧自身 `received_ns` 在同一窗口内的 1,420 / 1,404 条记录；不混用整轮包含启动阶段的分位数。8 ms 组确实等待 1,960 个输出、累计 31.066 秒，最大 hold 80.092 ms，预算 fallback 2 次；“lead 8”不是每帧固定等 8 ms。输入至 release 中位间隔为 21.18 / 66.43 ms，target−release 中位间隔为 52.80 / 7.86 ms。

本轮没有观察到显示 FPS 或尾部帧间隔收益。8 ms 组的应用输入队列 overflow、decoder input timeout、FEC expiry / reference loss 都为 0，worker queue wait p99 为 1.89 ms，原路径为 1.83 ms，因此不声称持续应用输入队列拥堵。`decoder_ready_ns` 记录在 `dequeueOutputBuffer` 返回之后，包含之前输出等待、codec 内部排队和线程调度，不能把驻留增加全部解释成硬件解码变慢。各条件仅一次有效成功运行、使用不同实时编码片段，不能建立稳定因果结论。保留 lead 0 默认，8 ms 仅为可显式启用的独立探针选项。

## 当前结论边界

暂不建议用户永久锁定 60 Hz，也不把 speed=true 设为默认。进一步的假设是按实际刷新节奏管理目标时间与提交时机，验证在不超过 80 ms 推荐缓冲的条件下减少不规则帧间隔。它是待验证的方向，未证明严格稳定 60/120 FPS、原生光学触控延时或公网抗丢包体验。

全部数值摘要与阶段数据见 [受控对照](evidence/cadence-resume-20261001/controlled-comparison.json)和[同 PTS 本机阶段](evidence/cadence-resume-20261001/capture-steady-analysis.json)。[最后状态核验](evidence/cadence-resume-20261001/final-health.json)确认：手机 min=null / peak=165.0 已恢复；正式 App 与原 probe 已安装 APK SHA-256 未改变；独立实验包单独保留；M1 仍是 6 核 / 16 GiB / 1080×1920 / host GPU，guest tracing 与 atrace flags 为 0，已无测试 capture worker；本地 gateway ping 与下载页均 HTTP 200。没有发布用户升级包，也没有改动 M5、NPS 或 Clash。

本轮相关 Python 检查共 64 项通过，覆盖编码属性报告、独立实验 target / 初始化 / 失败退出、硬件适配器、resume identity 分析、graphics trace 和 fence-gap 分析。它们是局部源码回归检查，不代替真机视频证据。Swift 独立 encoder、独立 Android App 和匹配 instrumentation 均已实际构建。
