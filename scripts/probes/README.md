# macOS / 安卓模拟器隔离探针

这些工具分层验证本机画面绘制、采集和编码。当前 M1 硬件后端已部署，实测结果及边界见 [60 / 120 FPS 验收记录](../../docs/hardware-60-120fps-20260929.md)。本机通过不等于公网手机验收。

## 2026-10-02 最新结果与离线索引

[本轮证据索引](../../docs/evidence/overnight-20261002/README.md)、[PDF 报告](../../output/pdf/huoguo-android-overnight-report-20261002.pdf) 和 [HTML 报告](../../output/pdf/huoguo-android-overnight-report-20261002.html) 汇总 **33 次独立真实 LAN UDP 会话与 16 次固定文件解码对照**。真实视频累计请求 **1325 秒**：31 次 35 秒、两次 120 秒，各轮独立建立会话，不是整夜连续稳定性测试。手机独立 SF 固定窗中，1080×1920 两轮约 59.32/59.12 FPS；720×1280 两个长轮的 [5,110) 秒窗口约 59.31/59.67 FPS。推荐 80 ms，100 ms 为已允许的流畅优先选项；源供给和顺序漂移仍限制收益归因。

- [overnight_suite.py](overnight_suite.py)：有界实验编排入口；真实视频、固定文件、码率和 pacing 组合分组记录，不能把不同组拼为同一单变量对照。
- [summarize_overnight_suite.py](summarize_overnight_suite.py)：只读已有证据，短轮取手机首包后 [5,30)，120 秒长轮取 [5,110)，另保留完整 SF 采样、触边 gap、时长与源验证，以及记录淘汰/统计缺失。输出 [real-video-summary.json](../../docs/evidence/overnight-20261002/real-video-summary.json)，可离线重复运行。
- [同文件 SPS/VUI 结果](../../docs/codec-vui-results-20261002.md) 与 [codec-analysis.json](../../docs/evidence/overnight-20261002/codec-analysis.json)：16 轮媒体内容相同的 CONFIG-only 对照；现行 UDP 实验原已开启 SPS patch，这不是新发布修复。
- [独立提交预算结果](../../docs/raw-budget-results-20261002.md)、[长停顿复核](../../docs/long-stall-results-20261002.md) 与 [pacing 组合结果](../../docs/pacing-results-20261002.md)：分开解释 host 等待、源供给和手机呈现。关闭 socket pacing 的条件同时停用 guard，不能宣称纯 pacer 单因素收益。
- [final-health.json](../../docs/evidence/overnight-20261002/final-health.json)：收尾未见活动实验采集进程，未写手机 CPU 限制、未改 M5、未发布正式 UDP。native encoder 最终统计与 Swift 最终 trace summary 缺失；长轮 native 事件有淘汰，不以缺失记录证明零丢失。

正式默认值和已有用户参数未由此改动；新公网 UDP、蜂窝/V50、光学触控延迟和声学音画同步均未验收。Mac 与手机时钟仍不能直接相减；callback 不代替独立 SF 显示计数，缓冲/target 时间也不等于物理延迟。

## 2026-10-01 历史调试

以下保留 10 月 1 日样本和工具使用记录；其中历史 FPS 不代替上面的最新结果，设备读回、限频保护和运行守卫仍需遵守。

当日源端连续调试、Perfetto/SF、物理60/120Hz与默认/GPU合成路径对照见 [真实视频源端记录](../../docs/source-causal-results-20261001.md)。`trial_client_composition.py` 只临时操作已 root 的 M1 测试 guest，并检查与恢复合成开关；它不安装或发布新版 App。`run_surface_hint_matrix.py --restart-source --source-warmup-seconds 8` 可每轮重开既定公开视频，保留播放器数据，避免沿用自动播放后的其他帧率视频。该日1080P压力测试使用 `--max-size 1920 --video-bitrate 8000000`，实际尺寸仍必须读取VT/phone结果。物理1080×1920及当日SF readiness、25ms宿主vCPU测量见 [1080P实测](../../docs/pre-latch-results-20261001.md)。

源端暂停恢复的 identity 检验、VideoToolbox 速度优先属性、手机实际 60/90 Hz 和匹配版本 0/8 ms 显示提交对照见 [1080P 显示节奏与编码结果](../../docs/cadence-and-speed-results-20261001.md)。`analyze_resume_frame_trace.py` 拒绝边界不明确的恢复首帧；`--encoder-prioritize-speed true|false` 要求独立 encoder 并保留严格读回。正数 `--surface-submit-lead-ms` 只允许 `--experimental-client` 的固定独立 App/probe 配对，先校验 instrumentation target，再校验手机执行报告；失败或未匹配的 matrix 返回非零，不生成性能结论。独立 App 构建使用 `-PprobeApplicationId=local.remoteandroid.direct.experiment`，probe 构建也须带 `--experimental-client`；正式发布前须重新构建默认产品，不能发布该独立实验 APK。

当前保留测试机为 **M1＋root一加12**，请先读取 [current-testbed.json](../../docs/current-testbed.json) 并重新核验设备serial/IP，避免把旧的一加15默认参数当成当前设备。矩阵可显式指定 `--serial --phone-label --bind-ip --peer-ip --interface --fps 120 --hints 120`；旧默认保留用于历史复测，120必须明确传入。真实视频和原生120场景的分层数据见 [一加12压力测试](../../docs/oneplus12-120fps-results-20261001.md)。matrix还核验手机完整观测时长与各线程异常，不能仅因instrumentation返回0就认定播放成功；SF采样缺失单独报告。

一加12已由用户人为CPU降频，目标是作为真我V50的性能受限替代机。**保留用户的限频，不通过提高CPU频率、关闭限制模块或温控来追求FPS。** 下一轮须只读保存实际限频条件；配置文件中的6核/16GiB指M1虚拟安卓，手机本身是另一套资源。CPU限频不等价于V50的GPU/视频解码/网络能力，最终V50仍需独立实测。

进一步降频后的5轮真实视频验证见 [本轮结果](../../docs/oneplus12-lower-cpu-results-20261001.md)：较好组约53～54FPS，稳定60尚未达到；CPU上限动态变化，不能称为固定约0.7GHz测试。该次CPU采样的Python `time.monotonic_ns()`与capture trace的`clock_gettime_ns(CLOCK_MONOTONIC)`实测存在offset，禁止直接对齐稳态窗口或用事后offset作精确校准。后续需要关联host CPU和capture阶段时，采样端统一显式时钟API，并在每轮开始、结束保存成对时钟读回；手机与Mac的时钟仍不能直接相减。

当日缓冲正反顺序、VT像素池、source-only宿主调度与真实手机复测见 [细节优化结果](../../docs/latency-refinement-results-20261001.md)。用户已明确允许100 ms作为流畅优先可选值，matrix支持60/80/100 ms对照；折中推荐80 ms。矩阵须同时通过完整手机运行与真实源门槛（已知PLAYING前后快照、足够时长的有效source SF进度），idle heartbeat不得纳入真实视频性能结论。当时已保留Interactive作为M1测试候选，尚未证明持续60 FPS或稳定端到端收益。

## 工具与运行边界

- `measure_local_encoder.py`：现有 scrcpy 软件编码；默认在 M1 主机运行，使用 `emulator-5556` 和已部署的测试源。
- `emulator_grpc_probe.py`：认证回环 gRPC 的完整 RGBA 截图流；报告帧率、帧间隔与时间戳年龄，不保存像素。
- `videotoolbox_probe.swift`：合成 NV12 输入；只验证硬件编码阶段。要求并读回硬件使用状态，报告各阶段耗时。
- `emulator_hardware_encoder.swift`：完整 RGBA 帧经 vImage 转换后，交给 VideoToolbox 硬件 H.264 编码；输出兼容现有视频帧协议，默认探针模式有时长上限；`--service` 供正式适配器使用，仍保留在途帧与写入超时限制。
- `measure_host_hardware.py`：实际 gRPC 画面到上述原型，再用 ffprobe / ffmpeg 在内存中检查码流；与单独编码探针是不同验证层。
- `run_codec_file_probe.py`：重放固定私有合成 H.264 fixture，只运行现有手机解码/输出策略，隔离模拟器、采集、网络和音频；格式、固定报告路径与验证边界见 [独立手机解码探针](codec_file_probe.md)。

先确认没有现有串流，使用项目 `diagnostic-source` 的合成 Activity，不采集私人界面。若正在连接，相关探针会拒绝运行。不要为测试擦除 AVD、改动原账号或开放公网 ADB / gRPC。

对已有模拟器启用 `-grpc 8554 -grpc-use-token` 时，检查实际只监听 `127.0.0.1`；生产实例的启动参数改变需要在无人在用时生效。使用 SDK 生成的 `pid_<PID>.ini` 发现文件，令牌只在进程内读取，不要复制到命令行、报告或 Git。当前 SDK 的实际目录是 `~/Library/Caches/TemporaryItems/avd/running/`。

依赖使用独立虚拟环境及安装 SDK 自己的协议文件。以下命令只准备编译和运行环境，**不会启动、重启模拟器或切换合成界面**：

```sh
probe_dir=$(mktemp -d /private/tmp/huoguo-vt-probe.XXXXXX)
python3 -m venv "$probe_dir/venv"
"$probe_dir/venv/bin/pip" install grpcio==1.84.0 grpcio-tools==1.84.0 grpcio-reflection==1.84.0
"$probe_dir/venv/bin/python" -m grpc_tools.protoc \
  -I "$HOME/Library/Android/sdk/emulator/lib" \
  --python_out="$probe_dir" --grpc_python_out="$probe_dir" \
  "$HOME/Library/Android/sdk/emulator/lib/emulator_controller.proto"
swiftc -O scripts/probes/emulator_hardware_encoder.swift -o "$probe_dir/emulator-hw-encoder"
```

在合成 Activity 已处于前台、首次全屏提示已关闭、画面物理尺寸为 540×1200 时，提供实际发现文件即可运行。按目标实例修改 serial / avd；不要把示例 PID 当成实际 PID：

```sh
PYTHONPATH="$probe_dir:scripts/probes" "$probe_dir/venv/bin/python" \
  scripts/probes/measure_host_hardware.py \
  --discovery "$HOME/Library/Caches/TemporaryItems/avd/running/pid_<实际PID>.ini" \
  --encoder "$probe_dir/emulator-hw-encoder" \
  --serial emulator-5556 --avd RemoteAndroid17Compare --duration 10 --fps 30
```

当前测试还需要 Homebrew 的 `/opt/homebrew/bin/ffmpeg` 和 `ffprobe`。输出是性能 JSON，不会使用登录口令；压缩视频只在内存中进行解码校验。测试结束正常 BACK / HOME 退出合成 Activity，取得对应 `DiagnosticSource` 生命周期日志，再确认没有测试串流残留。只读临时模拟器应关闭。

实验中的 `source_scene_fps=30` 是合成源目标，不等于实测绘制帧率。`timestamp_age_ms` 是宿主时钟和模拟器时间戳的估算差，不能解释为手机端延迟。VideoToolbox 属性若设置不受支持，应保留错误码；不能仅因为读取值为 0 就声称已启用该优化。

`measure_source_rendering.py` 支持显式 30 / 60 / 120 FPS 合成源，对比 Android 帧阶段、实际绘制和硬件输出；正常手机自动检测仍用原固定 30 FPS 场景。`measure_hardware_service.py` 验证正式适配器的 H.264、AAC、真实触摸和动态码率；`--gateway` 只使用已有账号和本机 TLS，口令从测试进程环境读取。`--bitrate` 支持 0.5–40 Mbps，报告平均 FPS、5 秒窗口、帧间隔及音频时钟估计，不保存画面。24 / 40 Mbps 需要 Mac 硬件编码后端。
