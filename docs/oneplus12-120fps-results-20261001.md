# 一加 12：1080P / 120FPS 压力测试

日期：2026-10-01。本轮将 root 一加 12 PJD110 / Android 16 作为长期测试手机，替代一加 15；用户明确要求保留测试期间设置。不是跨手机硬件优劣的单变量比较。

## 配置与准备

- M1：既有 RemoteAndroid17Compare / emulator-5556，物理 1080×1920、480 dpi、6 核、16 GiB、host GPU，AVD hw.lcd.vsync=120。
- guest min/peak 原为 60.0，本轮设为 120.0 并保留；公共 Morphe YouTube 视频播放时 SF 默认周期读回 8,333,333 ns。
- 一加 12：USB serial f7fc9469，Wi-Fi 192.168.9.149，物理 1440×3168，既有 density override 560 保留；原 min=null / peak=120.00001。
- 独立 client/probe 包已安装并核对 APK SHA-256，未覆盖正式 App，未改变账号与个人数据。安装上传的临时 APK 已删除。
- 媒体实验要求 AES-GCM UDP、Apple VideoToolbox H.264 硬件编码和手机硬件解码；目标 120FPS、1080P、60ms 缓冲、FIFO 原始帧队列。

[安装与原设置](evidence/oneplus12-120fps-20261001/preflight-install.json)、[源端 120 准备](evidence/oneplus12-120fps-20261001/source-120-preparation.json)。

## 测量约束

真实视频的媒体文件 FPS 未逐轮独立核验。120Hz 显示、120FPS 截图/编码上限、媒体内容 FPS、收到帧率、SF present 帧率分别记录；不通过重复画面或 vendor codec callback 认定独立 120FPS 内容。帧缓冲推荐不超过 80ms。局域网数据不能代替 NPS 公网、蜂窝或远程 V50 验收。

USB 用于控制与数字报告，媒体通过 M1 有线 en7 到手机家庭 Wi-Fi。安装后手机 USB 曾离线，后续实验只在确认新手机重新连接且正确 package/target 后启动。

## 入口改进

旧 run_surface_hint_matrix.py 写死 host cap60，旧的 phone120 仅请求手机刷新率120。本轮新增明确 --fps 60/120、--serial、--phone-label、--bind-ip、--peer-ip、--interface 及 hint120，保存请求值，源控制仍固定 emulator-5556。

```sh
python3 scripts/probes/run_surface_hint_matrix.py \
  --experimental-client --serial f7fc9469 --phone-label "OnePlus12 PJD110" \
  --bind-ip 192.168.9.128 --peer-ip 192.168.9.149 --interface en7 \
  --fps 120 --display-hz 120 --hints 120 --buffers 60 \
  --raw-policies fifo --surface-submit-leads 0 \
  --packetizer /private/tmp/实际实验目录/h264_udp_packetizer \
  --encoder /private/tmp/实际实验目录/独立编码器 \
  --max-size 1920 --video-bitrate 8000000 --wire-bitrate 32000000 \
  --rounds 1 --seconds 35 --restart-source --source-warmup-seconds 8 \
  --output-dir docs/evidence/oneplus12-120fps-20261001/新的实验目录
```

外部运行目录为 /Users/wyw/Documents/ChatGPT/others/android-remote/m1-compare；示例私有二进制路径必须替换为已编译、记录 SHA-256 的实际产物。脚本不会自动发现或替换媒体地址。

## 实验结果

手机实际 mode 3 / 120.000008 Hz 与独立 SF 8,333,333 ns 周期在有效串流中均读回确认；MediaCodec 为 `c2.qti.avc.decoder.low_latency`，hardware=true。M1 VideoToolbox 实际使用 AVE H.264 硬件编码，尺寸为 1080×1920。串流仍明确请求 cap120，并非仅把面板偏好改成120。

| 样本 | 条件 | 实际结果 | 边界 |
| --- | --- | --- | --- |
| 本机真实 YouTube | 1080P / cap120 / 8 Mbps，没有手机或媒体网络负担 | 本地 H.264 57.113 FPS；稳态 raw 55.77–59.39 FPS | 不是手机验收，也没有输入120张原始帧/秒 |
| 真实 YouTube 首轮 | 1080P / cap120 / 8 Mbps | 不到 1 秒退出，worker IllegalStateException | 明确拒绝，不用它算FPS |
| 真实 YouTube 同版本复测 | 相同 8 Mbps，手机 actual120 Hz | SF `[5,30)` 显示 **56.96 FPS**；gap p99 41.44 / max 58.03 ms | 跑满35.001秒，仍有启动阶段一次输入预算 timeout 与依赖链恢复 |
| 真实 YouTube 12 Mbps | 其他条件保持 | SF `[5,30)` 显示 **55.60 FPS**；gap p99 41.44 / max 82.87 ms | 跑满35.000秒；单轮不同实时片段，未观察到加码率收益 |
| 原生120诊断场景串流 | 1080P / cap120 / 8 Mbps，nearest source clock | 源生命周期绘制 **91.006 FPS**；phone SF `[5,30)` **86.28 FPS**；gap p99 33.15 / max 58.01 ms | 合成容量验证；源与phone窗口不同，不能相减算精确丢帧 |

[统一摘要](evidence/oneplus12-120fps-20261001/comparison.json)、[本地真实管线](evidence/oneplus12-120fps-20261001/host-only-120-8m.json)、[原生源生命周期](evidence/oneplus12-120fps-20261001/synthetic-120-source-lifecycle.json)。全部有效手机试验均为加密物理 LAN UDP；音频解码有PCM写入，但没有声学音画同步或光学触控测量。有效报告的 native FEC expiry / reference loss 均为0。

8/12 Mbps 是编码目标，不是强制实际发满的带宽。两段真实视频的packetizer全窗口估计IPv4加密媒体wire平均值约 **4.435 / 4.465 Mbps**，是本地估计、不含所有无线/VPN开销；因此本轮没有显著提高实际网络负载，不能把目标12 Mbps当成已完成12 Mbps持续满载压力。结论仅为提高该场景编码目标未观察到显示收益。

本机真实视频的六个完整稳态 worker 窗口中，raw 与 submitted 完全一致、replaced=0、raw queue p95 0.080–0.118ms。这支持“本轮约57FPS供给已在VT之前出现”，不支持“VT把持续120FPS输入压成57FPS”，也不证明VT可以持续吞吐1080P120。真实源SF约55–57，手机约56–57，是不同采样窗的观测，不是逐帧身份证明。

## 原生源与绘制后端检查

原生诊断场景实际安装版本的启动日志确认 `target_fps=120 / source_clock=nearest`；退出时读取自身 unique draw / skipped tick / callback 数，而不是只相信 Intent 参数。它通过硬件 Canvas 绘制，但源仍没有持续产出120张不同场景帧。

不启动capture/VT/UDP时，15秒主体观测的完整生命周期（含启动约17.7秒）为 **84.361 FPS**，Vulkan recent120 frame rows 的 total p50约16.219ms、draw command p50约7.568ms。尝试OpenGL的单轮完整生命周期绘制 **87.918 FPS**，recent total p50约16.855ms，仍没有达到120；尾部分位也未改善。两次非同时、短窗口、无重复与精确负载匹配，不能据此建立后端优劣排名。OpenGL仅改变等待分布，不能把各阶段百分位相加当总耗时。继续保留 prior Vulkan 基线，120Hz偏好保留。

[Vulkan源单独运行](evidence/oneplus12-120fps-20261001/synthetic-120-source-alone.json)、[OpenGL源单独运行](evidence/oneplus12-120fps-20261001/synthetic-120-source-alone-skiagl.json)、[保留的绘制后端](evidence/oneplus12-120fps-20261001/renderer-selection.json)。原生源串流的 source SF companion 固定选择了 Morphe package，不适用于当前 benchmark场景，已在摘要中排除；生命周期draw计数不冒充SF present计数。

## 原生触控与失败判定

诊断串流中注入了一次手机OS单指滑动：phone记录54个MotionEvent，host bridge实际注入down1 / move30 / up1，edge ack2，无writer/ack错误。guest的专用场景日志另按run UUID核验action/count，见最后状态记录。这验证本轮单指输入走原生Android触摸协议，不是Mac鼠标；未测多指、光学延时或真实App所有手势。

首轮异常保留：首包到收流结束0.960193秒，worker IllegalStateException，外层IOException，host UDP peer关闭。首IDR在接收175.481ms后计一次timeout，而queue预算包含此前codec初始化；初始化耗尽预算是未证假设，报告缺少具体调用stage，不能定性为硬件decoder吞吐或网络故障。同版本复测成功，不把一次启动异常推广为恒常120FPS故障。

本轮补强matrix接纳门槛：无host/phone/worker失败、`running_at_end=true`、请求时长一致且phone窗口完整（固定容差1秒），才接纳成功。SF缺失独立记为测量缺失，不用codec callback替代显示。失败row保留、matrix返回非零、不生成成功摘要。该门槛与新设备/FPS参数共17项mock回归通过；[既有样本重新审计](evidence/oneplus12-120fps-20261001/playback-acceptance-audit.json)明确拒绝首轮，原始文件没有改写。

## CPU 降频条件补充

用户随后说明：一加12已人为强制CPU降频，用来作为真我V50的性能受限替代测试机。此前手机结果应按这一用户说明解读，不能称为满性能一加12成绩。具体旧轮次没有同步采集频率，因此以下读回只证明当前设置，不倒填成旧轮每个时刻的测量。

[只读限频快照](evidence/oneplus12-120fps-20261001/phone-cpu-limits-user-declared.json)确认0–7全部在线，四组governor均为uag：

| 手机CPU编号 | 内核报告最高频率 | 当前配置最高频率 | 上限比值 |
| --- | ---: | ---: | ---: |
| 0–1 | 2.2656 GHz | 1.6896 GHz | 74.58% |
| 2–4 | 3.1488 GHz | 1.4976 GHz | 47.56% |
| 5–6 | 2.9568 GHz | 1.2864 GHz | 43.51% |
| 7 | 3.3024 GHz | 1.3632 GHz | 41.28% |

这些是频率上限比值，不是性能或FPS同比缩减比例。本次只读，没有提升CPU频率、移除限制模块或关闭温控。current-testbed明确保存保留限频的约束。后续手机试验要同时保存该轮限频快照与同窗负载/排队观测，而不是用idle当前频率代表串流负载。

CPU受限替代机能提前暴露用户态UDP/FEC/调度/输入排队问题，但其GPU、硬件视频解码器、内存、屏幕与无线网络仍是原手机，不等价于V50；86.28FPS不能直接推广为V50吞吐、尾部间隔或启动稳定性。现阶段也不能把首轮IllegalStateException或全部掉帧定性为降频造成，缺少对应调用stage与同窗频率证据。最终仍需V50真实硬件及其网络验收。

独立的M1 host-only57.113FPS与源单独运行84.361/87.918FPS不受这台手机CPU限频直接影响：源端供帧不足的线索仍保留，应与手机端限制分开排查。并发的源/phone观测窗口不同，不能相减计算精确丢帧。

## 保留状态与结论

本轮没有达到稳定120FPS。已确认一加12真实120Hz、硬件解码与超过60FPS的原生容量；当前原生场景源端本身不达120，真实视频供帧约60，因此不能靠提高码率补出缺少的帧。后续需要继续区分源绘制、视频层交付及capture供给，再以同一手机验证30/60/120不同节奏；高帧率压力通过也不自动保证所有低帧率场景的尾部抖动。

[当前长期测试配置](current-testbed.json)固定M1＋一加12，保留guest与手机min/peak=120，M1仍为6核/16GiB/1080×1920/host GPU，Vulkan基线保留。IP在下一次测试前仍须重新读取。独立App/probe保留供继续测试，本轮没有发布用户升级包、改动M5/NPS/Clash或关闭UDP直连。最后本地gateway/下载服务健康和调试退出状态见[保留状态核验](evidence/oneplus12-120fps-20261001/final-retained-state.json)。
