# 1080P／12 Mbps 真实视频独立 ABBA，2026-10-02

M1 真实 Morphe YouTube 播放、用户限频的一加 12、AES-GCM 物理 LAN UDP，720P 后续采用 **1080×1920 编码／VBR 12 Mbps／80 ms 缓冲**做独立验证。4 轮 120 秒接收、每轮首个认证包后的 `[5,110)` 手机窗口和 source 自身第一呈现后的 `[5,110)` 窗口全部有效，合计 420 秒 steady。原 native／raw／phone cap120、面板120Hz、source实际60Hz、32Mbps wire、2048字节catchup、FIFO和guard均保留。

A 保留第二层 Python socket wait，B 仅跳过定时 wait，仍有 reservation／serialization／deadline／reference guard 与 native pacing。四轮共同使用时钟契约修正后的新 packetizer SHA `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`；source/Python/native runtime 指纹不变。不能与此前720P旧 binary 的数值混作同一实现，不能将这个时钟修正称为 FPS 改善原因。

| 轮次 | 手机 steady FPS | Source steady FPS | 手机最大间隔 ms | 手机 >100ms 间隔数 |
| --- | ---: | ---: | ---: | ---: |
| 1 A | 59.667 | 59.676 | 82.86 | 0 |
| 2 B | 59.733 | 59.762 | 58.01 | 0 |
| 3 B | 59.619 | 59.676 | 66.30 | 0 |
| 4 A | 59.762 | 59.819 | 66.30 | 0 |

A 手机均值 **59.714286 FPS**，B **59.676190 FPS**，B−A **−0.038095 FPS**；source B−A **−0.028571 FPS**。本条件均接近60，**没有观察到 B 明确改善**。继续保留默认第二层等待与保护机制，不根据720P的小样本关联统一更改所有场景。手机各轮 p95/p99 间隔约24.86ms，仍有短节奏变化；平均接近60和没有>100ms空档，不能自动称为每帧等间隔、肉眼零撕裂或接近零触控延时。

四轮分别核对 Apple VideoToolbox hardware readback=true、1080×1920，手机 source_geometry=1080×1920，AVC decoder `c2.qti.avc.decoder.low_latency`、hardware=true。source实际60Hz，菜单1080p60和requested cap120没有生成120个独立视频帧。原生summary clock_domain全为`host_clock_gettime_CLOCK_UPTIME_RAW_us`，capture trace另为CLOCK_MONOTONIC，不直接相减；见[时钟契约](native-host-clock-contract-20261002.md)。没有主动让Mac休眠，实际休眠／恢复另需验证。

116次5秒只读CPU上限采样全部成功，每轮29次。**观察范围包括源准备、接收与收尾阶段**，不是只限于手机105秒steady窗口；四轮都出现过max_khz变化，范围policy0 672000–1689600、policy2/5 960000–1824000、policy7 902400–1939200。测试未写这些值，非原子的离散读回也不等于CPU实际频率或持续锁定。不能把这一台限频一加12的成绩直接当成V50成绩。

最终socket guard与接收FEC expiry/reference/recovered计数全部为0，但全会话inbox overflow A4/B3、queue-cleared A16/B12，以及A1一次decoder input timeout仍应保留。不能由steady窗口良好推断整条管线全程没有恢复或丢弃。native详细ring裁掉前约51秒；periodic packetizer最后summary全non-final，完整最终快照仍有边界。

音频worker late A46/11287（0.40755%）、B16/11285（0.14178%），queue-drop A0/B1、PCM-late各2，assembly-expired0。旧冻结实验APK尚无新增分原因／直方图字段，也没有声学／口型验证。触控通道开启，但motion_events=0，没有做多指或触控到显示的延时测试。

手机此时已经加入yilufa Headscale，但**本批媒体明确走物理LAN，不是Tailnet媒体**，也没有公网NPS/移动流量/异地V50数据。

复现（在无正式会话时）：

```sh
python3 scripts/probes/run_socket_wait_abba.py --blocks 1 --max-size 1920 --video-bitrate 12000000 --packetizer /private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer --output-dir docs/evidence/socket-wait-1080p-NEW
```

本机详细记录：`docs/evidence/socket-wait-1080p-20261002/campaign.json`、`aggregate-review.json`、各轮`cpu-limit-observer.json`，原始详细数据不提交Git。测试收尾确认无正式连接、无活动source采集，保留AVD、原签名、账号和手机设置；没有修改永不熄屏配置。
