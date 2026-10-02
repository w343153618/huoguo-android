# NPS 只读检查与可证伪的瓶颈假设

2026-10-01。本记录不更改任何云端服务、NPC 参数、Clash 规则或手机状态。

## 当前路径与配置

M1 的 NPC 路径为 `~/.local/share/huoguo-android/npc/v0.34.7/npc`，通过本机 `127.0.0.1:18024` 的 physical relay 接腾讯云 `146.56.249.175:8024` TCP。本机 relay 代码对外 socket 验证 `IP_BOUND_IF`，只允许实际 `en*` 接口；两侧已设置 `TCP_NODELAY`，不会在物理接口不可用时回落到代理路径。

云端当前 `15556` 监听者是主 NPS PID 2242499。保存的 task 1409 关联 client 1466，目标为 **NPC 所在设备的** `127.0.0.1:15556`，不是云端本机的安卓，也不是直接保存 M5 的旧 LAN 地址。客户端保存的 `RateLimit=0`、Rate.Limit=0、Compress=false、Crypt=false、MaxConn=24，没有发现明确限速或额外 NPS 压缩/加密。原 App 的 TLS 是另一层，不能把 NPS Crypt=false 解读成 App 没有鉴权。

保存 JSON 的 RunStatus/IsConnect/NowConn 字段可能滞后；真实 listener 与已建立 socket 才是实时状态。本次所有读取都使用字段白名单，未输出 vkey、口令、账号、cookie 或配置全文。

NPC 配置没有 `tcp_mux` 用户字段，不能据此说没有 mux。[v0.34.7 官方 client/client.go](https://github.com/djylb/nps/blob/v0.34.7/client/client.go#L358) 在数据隧道建立时直接创建 mux；[lib/mux/conn.go](https://github.com/djylb/nps/blob/v0.34.7/lib/mux/conn.go) 包含每逻辑连接的发送/接收窗口和窗口更新等待。因此要分别观察内核 TCP 窗口和应用层 mux credit，二者不是同一个指标。

## 采样边界

子任务自行采样的两个窗口为北京时间 03:30:24.800–03:30:33.486 和 03:33:17.706–03:33:26.416，分别保存到 `evidence/nps-live-phone4M-20261001.json` 与 `evidence/nps-live-phone8M-20261001.json`。当时没有活动 `15556` socket，已标记 `claimed_phone_run_overlap_is_not_confirmed=true`，不能用于声称覆盖了真实手机测试。当前脚本显式记录活动 15556 样本数，防止再把空闲心跳当视频吞吐。

根节点另采集了 **确实有活动 15556** 的八秒窗口：`../../../docs/evidence/native-iteration-20261001/nps-readonly-12M-live.json`。按结果生成时间推算，约北京时间 03:34:56.38–03:35:04.42；旧报告没有逐样本绝对时钟，所以这个区间是近似值。新脚本补了 server unix_ms，后续才能更严格对齐 Host 阶段时间。

这次 12 Mbps 实验的视频源中途变成 30 FPS，根节点已排除它作为“稳定 60 FPS 码率对照”的资格。本记录仍可用其活动 socket 验证线路拥塞、窗口与资源假设；不把手机 FPS 作为该档成功/失败的证据。

## 活动窗口实际看到了什么

安全归纳数据为 `evidence/nps-active12M-analysis-20261001.json`。以下角色按传输字节量推断，没有读取 TLS 媒体内容：

| 观察点 | 七秒计数差与窗口 | 含义 |
|---|---|---|
| 15556 主视频候选 peer61205 | 发送增加 6,764,507 B，重传增加 1,332 B/一个段；TCP RTT 13.94–17.35 ms，snd_wnd 5,297,664 B，cwnd459，Send-Q 最大22,559 B，后半多数为0 | 没有持续手机 TCP 零窗口或大排队证据；Send-Q 还含未 ACK 在途字节，不能等同于尚未发送的应用积压 |
| 15556 另一小流量候选 peer61206 | 发送增加122,160 B，无新增重传；RTT49.72–58.43 ms，Send-Q最大1,479 B | 各逻辑连接的 ACK/小包节奏不同；不能用该 RTT 直接替代视频传播时延 |
| 8024 主数据隧道候选 peer59444 | 接收增加6,905,734 B；接收窗1,072,768 B，Recv-Q0，rcv_ooopack不增 | 不支持“云端入站内核接收窗口耗尽”的解释 |
| 同一8024的云→M1反向 | 发送增加59,025 B，重传增加7,553 B/85段，cwnd3–44，在第7秒看到3；RTT10.92–13.95 ms，RTO212–216 ms | 反向小包/credit/control 的丢失或重复重传值得定位；这是反向流，不能当成 M1→云视频丢包率 |
| 云端资源 | 2 vCPU；系统idle75.35%、steal0；NPS22.64%单核，另一个python3约9.95%单核；eth0采样峰值17.38 Mbps、无NIC新增drop | 当前窗口没有云CPU、NIC或用户所述200M容量饱和证据；不需要据此随便停止其他云服务 |

七秒反向 `bytes_retrans/bytes_sent` 约12.8%，但这不是准确网络随机丢包率：小包大小、重复或伪重传、累计计数含义都会影响它。当前采样频率一秒，不能证明从未出现不足一秒的 HOL/零窗口事件。

## 具体下一步假设

1. **编码输入一侧已经丢失60FPS能力。** 根节点给出的当前 LAN 同样只接收约42.7 FPS，且 encoded_egress p95低于1ms、raw_pipe p95约30–38ms。因此“只有公网 TCP 发送阻塞”不能解释全部问题。先在同一个真正60FPS源、同一个会话中，定位 raw_pipe 是像素转换、原生编码器读取/提交，还是它等待输出 drain；改 QUIC 不能恢复在这一步已经被替换掉的帧。
2. **M1 的其他捕获/编码负载可能与该输入一侧竞争。** 子任务两次 CPU 窗口中，qemu-system-aarch64-headless PID760约276–280%单核；Jump Desktop Connect 的 JumpConnect PID39037约67–93%单核；scrcpy PID11170约29–30%单核。可执行映射已核实，但窗口没有确认覆盖手机流，不能直接认定因果。根节点在确认 JumpConnect 不是当前必要入口后，可以做同源、同会话条件的暂停对照，观察 raw_pipe/提交FPS；本子任务没有停止任何进程。
3. **应用层 RTT 含设备/队列等待。** 根节点有效8M样本 App RTT p95约1,880ms，而该活动内核 TCP 窗口典型RTT只有十余ms；它们不是同一测量，因此只能排除“把1,880ms全当网络传播时延”的说法。需要分开记录 ping入队、手机socket写入、Host读取/响应与手机读取，而非立即增加80ms播放缓冲。
4. **反向 mux feedback/HOL 是仍待证伪的网络候选。** v0.34.7有应用发送窗；反向TCP重传与cwnd短时坍缩可能延迟 credit/control。只有在同一绝对时间窗口比较 mux窗口等待、M1sender re-tx、App control RTT 与帧到达中断，才能建立相关性。若所有内核窗口/队列都健康但App RTT仍很大，应优先查逻辑控制队列和设备调度。当前八秒数据不足以单独判定 mux 是主因。

`host-morphe-nps60-vui-4M-matrix-timings.json` 属于更早的4M窗口，与这个12M TCP窗口不重叠；其说明也警告同时会话无法分离。因此没有把两份不同时间的 p95 拼成逐帧因果链。

## 可重复的只读入口

```sh
python3 experiments/moonlight-v2/transport/audit_nps_readonly.py \
  --duration 8 --m1-relay \
  --output experiments/moonlight-v2/transport/evidence/nps-next-readonly.json
python3 experiments/moonlight-v2/transport/analyse_nps_metadata.py \
  docs/evidence/native-iteration-20261001/nps-readonly-12M-live.json \
  --output experiments/moonlight-v2/transport/evidence/nps-active12M-analysis-20261001.json
```

第一项仅 SSH 读取云CPU/NIC/筛选socket，并通过 nettop 读取本机18024 relay、通过ps读取CPU时间差；不会发起串流或改配置。local nettop 第一条是累计基线，后续为差值，不能把基线重传总量当本次新增。第二项完全离线。
