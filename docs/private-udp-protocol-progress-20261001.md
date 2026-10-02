# 私有 UDP 媒体原型：真实 M1 / 一加 15 验证

日期：2026-10-01。目标是验证自研实时 UDP 线路是否值得继续，不把组件验证称为已经发布的产品。当前正式 App 的媒体线路仍是原有 TLS/TCP；本轮使用独立 instrumentation APK 驱动其解码 Surface，新 UDP 原型不自动替换线上连接。

后续同日完成14轮线程/时钟、关键帧突发和快速恢复的真实视频对照，见[最新结果](udp-burst-and-ingress-results-20261001.md)。其中wire16/100KB实验手机全采集最长间隔66/144ms，仍未稳定60FPS；旧的下文两轮数据作为历史证据保留。

## 本轮已实现

- 视频：Apple VideoToolbox 硬件 H.264 → C++ 分帧、整帧预算检查、10+2 Reed-Solomon FEC、限时发送 → AES-256-GCM UDP → 手机上的原生 FEC 重组 → Qualcomm MediaCodec。每个加密包不超过 1120 字节；音频和控制共用方向唯一的 nonce 序列。定时丢包或发送失败仍消耗 nonce，不能重用。
- 音频：AAC 配置和媒体帧使用 HGUA 分片，进入同一加密 UDP socket。手机使用独立音频解码/输出线程和公共音画时间映射，读取 AudioTrack 播放时钟。没有音频 TCP 网络回退；目前没有音频 FEC，也没有声学同步验收。
- 触控：HGUT 传输完整活跃触点快照，归一化坐标、每次 DOWN 的独立 tracking token、最多十指、边沿 ACK/有限重试、MOVE 合并和断线释放。主机在本地控制通道注入 Android SOURCE_TOUCHSCREEN，不移动 Mac 鼠标。本轮真实手机完成一次 OS 注入的单指滑动；完整十指、人手触控延时及 ACTION_CANCEL 仍待验证。
- 恢复：关键帧超出固定 80 ms 整帧发送预算时，先降低编码器目标再申请 IDR；每轮最多六次、间隔至少 500 ms。实际 VideoToolbox 接受值单独记录。该算法只解决本机编码/发送预算，不是公网可用带宽估计或完整拥塞控制。
- 自动采样：首次媒体包触发源端和手机端并行 SurfaceFlinger 采样，覆盖大部分会话。记录实际 Surface 呈现间隔，不使用厂商回显的 MediaCodec target 时间冒充真实屏幕呈现时间。

Mac 与 guest 之间仍复用本地 scrcpy 音频/控制服务。这里的“媒体走 UDP”指 M1 到真实手机的网络媒体通道；本机内部 IPC 没有被全部重写。协议不兼容 Moonlight 的完整握手，也没有复制网易私有实现。

## 环境和实验路径

- M1 Max，保留的 `RemoteAndroid17Compare` / `emulator-5556`，6 核、16 GiB、物理 720×1280、dpi 320、Skia Vulkan、宿主 VTB 解码环境已启用。
- 一加 15，Android 16，物理 1080×2354，`c2.qti.avc.decoder.low_latency`。它不是目标低性能真我 V50。
- 同一家庭 Wi-Fi：M1 有线 `en7` / 192.168.9.128 → 手机 192.168.9.6。UDP 15961 ↔ 15960，主机设置且读回 Darwin `IP_BOUND_IF`。本轮不经过 NPS、公网、蜂窝、ICE 或 Tailscale。
- 内容是蓝色 M 的 Morphe YouTube 中公开 Big Buck Bunny `aqz-KE-bpKQ`，用户已启用可用 60 FPS 优先规则。仍以实际源 Surface 呈现记录为准，不能从请求 60 FPS 推断输出必为 60。
- 下表两轮各 35 秒，编码请求 60 FPS / 540×960，guest 物理分辨率保持 720×1280；配置缓冲 60 ms，整帧组装预算 80 ms，短时 wire pacing 预算 16 Mbps。
- 16 Mbps 是包含 FEC、包头和加密开销的短时发送预算，和 4 Mbps 编码目标不同；它不是新增用户码率档位，也不是网络测出的保证容量。

## 真实结果

| 条件 | 无主动丢包，带单指滑动 | 周期性 2% 视频数据报丢包 |
| --- | ---: | ---: |
| 初始编码目标 → 最后编码器接受目标 | 4 → 2.8 Mbps | 4 → 2.7 Mbps |
| 源 Surface 采样秒数 / 新呈现数量 | 32.179 / 1740 | 32.173 / 1829 |
| 源 FPS：完整窗口 / 呈现间隔 cadence | 54.073 / 55.140 | 56.849 / 57.850 |
| 手机 Surface 采样秒数 / 新呈现数量 | 32.318 / 1686 | 32.108 / 1681 |
| 手机 FPS：完整窗口 / 呈现间隔 cadence | 52.168 / 53.204 | 52.355 / 53.256 |
| 手机呈现间隔 p95 / 最大 | 33.275 / 121.689 ms | 33.190 / 499.143 ms |
| 手机大于 100 ms 的呈现间隔 | 1 | 11 |
| 手机视频完整帧收到 / 排入解码 / codec callback | 1940 / 1940 / 1931 | 1935 / 1912 / 1908 |
| 手机原生 FEC 恢复 data shards | 0 | 201 |
| 手机整帧过期 / reference lost | 0 / 0 | 3 / 3 |
| 音频 worker late / PCM late / assembly expired | 0 / 0 / 0 | 87 / 2 / 2 |
| 音频 PCM 写入字节 | 6705152 | 6340608 |

无损轮触控：手机 42 个 MotionEvent，主机实际注入 1 DOWN、27 MOVE、1 UP，两个边沿 ACK、零 writer error、结束活跃触点为零；guest 页面前后截图可见滚动，截图只用于视觉确认，不用来统计 FPS。截图保留在临时目录，不进入 Git。

丢包轮在加密封包后、socket 发送前，主动丢弃每第 50 个视频包：302 / 15130，约 1.996%。音频与触控没有注入丢包。这个周期模式比连续突发丢包容易恢复，也没有注入乱序、网络抖动、带宽竞争。201 个恢复的 data shards 不是“所有 302 个丢包完全恢复”的同义词；丢弃中包括 parity，且存在时间/帧边界。

完整窗口 FPS 是新呈现数 / 采样墙钟时间；cadence 以实际呈现时间戳区间计算。两种统计均保留，不挑更高的一项冒充整个会话性能。源/手机窗口并非逐帧对应，不能用两者相除得到严格网络丢帧率。采样器旧通用 `scope` 字段写作 source layer，手机报告实际选的是客户端 Surface，不是远端 guest 的视频 layer。SurfaceFlinger 记录也不是摄像机实测物理面板或触控到出光。

MediaCodec 的厂商 callback 时间仍回显请求 target，不能用于端到端延时或音画同步。丢包轮 input→decoder ready 为均值 43.468 ms、p95 约 166.7 ms、最大 505.193 ms；该区间含解码器队列和线程调度，不是纯硬件解码执行时间。配置缓冲 60 ms 不代表实际总延时小于 60 或 80 ms。

## 找到的具体故障与边界

早期 720p / 8 Mbps 编码、12 Mbps wire budget 的无自适应轮只排出九帧到手机。源仍约 57.8 FPS；一个 IDR 的完整 wire 大小约 233064 字节，在 12 Mbps 下需约 155.4 ms，超过 80 ms 整帧预算，随后依赖 P 帧被保护性丢弃。平均编码目标低于通道容量仍不能保证大关键帧及时完成。

启用反馈后 720p 的 8 Mbps 目标逐步降到 1 Mbps，手机 callback 大部分秒窗恢复到 58–61 FPS。这证明恢复控制实际驱动了硬件编码器；不能据此称持续跑满 8 Mbps 或实际屏幕稳定 60。该轮额外 Surface 采样启动较晚，仅前约 12.45 秒与播放重叠，不能拿其全窗口 22 FPS 或活跃 cadence 57 FPS 当整个会话的显示结果。后续两轮改为首次媒体自动触发采样。

最新周期性丢包轮平均呈现仍约 53 FPS，但最大停顿近 500 ms、音频迟到丢弃和视频 IDR 恢复仍存在。native packetizer 最后一期累计记录为 5 个 output deadline drop 和 100 个依赖源帧丢弃。接收循环还会同步等待视频 codec input，可能延迟同 socket 的音频分流；丢失参考后的解码重建与共享时钟需要单独做因果实验，不能把所有停顿归因于线路。

因此可以确认视频、AAC、单指控制三个 UDP 组件已真实跑通，且 FEC 有真实恢复；尚不能确认稳定 60/120 FPS、零卡顿、公网顺滑或与网易产品等价。

## 后续产品化验收

1. 将 socket 接收/解密/音频分流与 codec 输入等待分离，使用有界媒体队列；损失参考或产生长解码积压时验证清队列/等待新 IDR 的恢复策略。不能随意扔 P 帧后仍继续解码坏参考链。
2. 为真实公网测量实现带宽/排队/RTT/损失反馈和码率回升；对连续丢包、乱序和抖动做独立测试，补音频 FEC/PLC。
3. 完成正式会话鉴权、ICE 手机接入与真实 NAT 测试；先 P2P UDP，失败走国内云 UDP 中继。当前 ICE 只有固定提交的构建与本机候选验证。不能把 QUIC reliable stream 或 Tailscale DERP 自动当作原生 UDP 媒体路径。
4. 每个实际公网 socket 验证国内物理出口绕过 Clash；保留腾讯云外国来源过滤规则，不能从 LAN 的物理绑定结果推断公网路径已验证。
5. 手机触控取消需最小 guest 修补：scrcpy 4.1 接受 action 3，但内部 PointersState 不会因 CANCEL 自动清空。应一次取消全体活跃触点并清状态；当前兼容释放用 UP，尚不等于真正 Android ACTION_CANCEL。
6. 把路线接入正式 App 的登录、连接选择、重连与更新，测真实触控到画面、声学音画同步、持续播放与 V50。正式发布前不称 prototype APK 是用户已升级的公网 UDP App。

保留标准 scrcpy + Tailscale 作为独立对照/备用实验；当前继续私有 UDP，不把 TCP 自动设成新实时媒体线路的回退。

## 复现与证据

入口：[run_phone_udp.py](../experiments/moonlight-v2/transport/android-udp/run_phone_udp.py)。固定外部运行依赖为 `~/Documents/ChatGPT/others/android-remote/m1-compare`、已有 Android SDK/NDK 和按固定提交构建的 Moonlight/nanors；二进制、APK、AVD 磁盘和会话密钥均不提交。本轮 host packetizer 是 `/private/tmp/huoguo-udp-recovery-build/host/h264_udp_packetizer`。

```sh
python3 experiments/moonlight-v2/transport/android-udp/run_phone_udp.py \
  --bind-ip 192.168.9.128 --peer-ip 192.168.9.6 --interface en7 \
  --packetizer /private/tmp/huoguo-udp-recovery-build/host/h264_udp_packetizer \
  --seconds 35 --fps 60 --bitrate 4000000 --wire-bitrate 16000000 \
  --max-size 960 --buffer 60 --audio --touch --adapt-budget --sample-surfaces \
  --drop-video-every 50 --source 'real public YouTube video; periodic loss experiment' \
  --output docs/evidence/native-iteration-20261001/phone-private-udp-av-540p4M-loss2.json
```

主要报告：[无主动丢包 AV + touch](evidence/native-iteration-20261001/phone-private-udp-av-touch-540p4M.json)、[周期性丢包 AV](evidence/native-iteration-20261001/phone-private-udp-av-540p4M-loss2.json)，同名 `-source-surface.json` 和 `-phone-surface.json` 是独立原始呈现采样。其他诊断与失败报告继续保留，不能删除失败后只展示成功。

本轮相关检查：12 个恢复控制单元测试、4 个公共 nonce/发送测试、4 个音频分包测试、13 个触控状态测试、4 个加密协议测试；Java 组装检查、API 37 编译及真机 instrumentation APK 安装通过。离线合成编码/FEC/恢复测试另存 transport/evidence，不冒充真视频手机结果。

实验后两个固定 phone session/report 文件已删除；[健康回查](evidence/native-iteration-20261001/m1-after-private-udp-readback.json) 确认 guest 仍是 6 核 / 16 GiB / 720×1280、永不熄屏配置，原 HTTPS gateway 和下载页均 HTTP 200。没有更换线上账号、NPS、M5 或 Mac 显示器配置。
