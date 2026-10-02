# UDP 供帧、关键帧突发与恢复：真实 M1 / 一加 15 对照

日期：2026-10-01。当前本地实测已定位并缓解一处具体停顿链：大 IDR 超出整帧发送期限 → 阻断依赖 P 帧 → 多次 500ms 恢复等待。尚未达到稳定 60/120 FPS 或公网/V50 产品验收。

## 环境与比较边界

- M1 Max；已有 Android 17 `RemoteAndroid17Compare` / `emulator-5556`，6 核、16 GiB、物理 720×1280 / dpi 320、永不息屏。M5 未操作。
- 真实 Morphe YouTube `app.morphe.android.youtube` 播放公开 BBB `aqz-KE-bpKQ`，每轮通过固定 URL 请求从 60s 播放。保留账号、用户的可用 60FPS 画质偏好。顺序实时测试不是完全相同编码字节的 A/B，也不是随机化实验。
- USB 连接一加 15用于控制/读取证据；媒体实际走家中 Wi-Fi。M1 `en7 / 192.168.9.128` 到手机 `192.168.9.6`，socket 设置并读回物理 `IP_BOUND_IF`。没有通过 USB 传媒体，没有测试腾讯公网/NAT、蜂窝或东北 V50。
- 540×960 串流、请求 60FPS，编码初始目标 4Mbps VBR；播放缓冲 60ms，手机重组与主机整帧准入各自 80ms。这些配置不代表总延时 <=80ms。
- 视频 Apple VideoToolbox硬件 H.264、native FEC10+2、AES-256-GCM UDP、手机 Qualcomm MediaCodec；AAC和触控反馈同样走加密 UDP。本轮矩阵没有主动触摸动作，原有单指验证仍是单独证据。
- 每轮请求 35s，源端和手机并行 SurfaceFlinger 自动采样约 32s；另报手机首包后 5–30s 新呈现数 / 25s。所有 14 轮及失败/低表现样本保留在[总比较 JSON](evidence/native-iteration-20261001/udp-ingress-burst-recovery-comparison.json)。
- 主机原生计数采用会话内最后一期累计 summary（`final=false`），可能不包括最后不足一秒；不是精确的全部会话结束计数。Surface 采样排除最初 ring 历史，ring容量、层变动、ADB迟延仍可能漏采；不等于光学面板或人手延时。

## 先区分接收阻塞与播放时钟

同一 Probe APK 五轮比较 sync/legacy、async/legacy、async/arrival-only，并重复后两种。异步输入使用四帧 / 2MiB 有界队列；网络入口不调用等待 codec input 的操作。队列溢出、过期或 codec input 超时时清空坏参考链，等新配置及 IDR，不能跳过 P 帧后直接继续用坏参考。

接收处理最大时间由 sync 的 42.994ms 降为 async 的约 1.8–5.3ms，但五轮仍出现约 632–2107ms 的最大呈现间隔。关闭 decoder-driven reanchor 也未稳定消除停顿。线程隔离达到了缩短入口处理的目的，不能据此说解决了全部掉帧。

这些长间隔附近，手机完整 AU 与 codec 输入已经断供约 0.6–2.2s；主机同时记录大 IDR 预算拒绝及依赖丢弃。例如 172098B 完整 wire 在 16Mbps 下需 86.05ms，超过 80ms，随后重试失败继续等待。它不是平均 4Mbps 不够，而是关键帧短时体积及恢复策略的问题。

`scheduled_ns - pts_us * 1000` 不是独立 `PlaybackClock.offsetNs`：renderer 的临时 `target=now` clamp 也会使推导量上跳。旧的“公共时钟增加约414ms”判断证据不足，已修正为候选。比较脚本现在使用 `requested_schedule_mapping_jumps_over_10ms` 并保留限制，不以厂商回显 target 的 callback 证明实际延迟。[完整时钟因果笔记](udp-clock-causal-notes-20261001.md)

## 显示刷新率混杂与受控复测

旧五轮手机 `display_vsync_ns=16666666`（60Hz），wire20两轮为 `11111111`（90Hz）。因此不能把旧约48–50到新约56FPS的全部提升归给16→20Mbps发送预算。

Probe新增 `display_hz`，选择当前物理分辨率下支持的60/90/120Hz mode，并保存/恢复窗口原modeId与refresh。系统可以不兑现窗口提示，报告始终保留开始/结束实际mode和是否匹配。`ingress-fixed60-*` 的名字表示请求60Hz，但这三轮的实际读回与SF仍为90Hz；未修改系统全局刷新率。后续wire12显式请求90Hz并读回匹配。以下六种实验组不能被描述为“所有手机都固定60Hz”。

## 编码突发约束与快速恢复的真实结果

| 条件 | 轮数 | 手机实际显示周期 | 手机全采集 cadence FPS | 全采集最长呈现间隔 | 最后一期预算拒绝 / 依赖丢弃 | 最后请求/确认编码目标 |
| --- | ---: | --- | --- | --- | --- | --- |
| wire16，原窗口，原时钟/线程矩阵 | 5 | 60Hz | 48.06–50.61 | 632–2107ms | 每轮2–3 / 31–122 | 2.2–2.6Mbps |
| wire20，仅提高短时发送预算 | 2 | 90Hz | 55.99 / 56.77 | 122 / 133ms | 0 / 0；0 / 5 | 仍请求4Mbps，无新降目标指令；未单独VT读回 |
| wire16，异步/arrival，对照原编码窗口 | 1 | 90Hz | 57.07 | 588ms | 2 / 30 | VT确认2.6Mbps |
| wire16，新增100000B/80ms编码窗口 | 2 | 90Hz | 57.89 / 57.09 | 66 / 144ms | 两轮0 / 0 | 两轮VT读回4Mbps |
| wire12，新增64000B/80ms窗口，500ms恢复 | 2 | 90Hz | 52.65 / 55.43 | 1087 / 111ms | 4 / 69；3 / 14 | VT确认1.4 / 1.7Mbps |
| 同wire12/64000B，仅恢复改100ms | 2 | 90Hz | 54.81 / 54.89 | 122 / 232ms | 3 / 18；3 / 21 | VT确认2.0 / 1.9Mbps |

这是小样本中观测到的结果，不是更快恢复必然优于所有500ms样本的保证。500ms第二轮自身就只有111ms间隔。100ms两轮没有重现1.09s停顿，支持恢复等待会放大断供的解释，仍需多内容和真实网络验证。

100000B/wire16两轮的首包后5–30s完整窗口FPS分别57.52、56.76，最长间隔55.46、110.92ms。源端全采集 cadence分别59.24、58.77。全窗口手机FPS55.86、55.13也保留，不拿活跃cadence替代整个采样时间。不能称稳定60，更不能称120。

wire20是包含FEC、加密及估计IPv4/UDP开销的短时预算，不是20Mbps固定平均编码或新增画质档位。只有实测路径有对应短时容量且排队/晚到未恶化时才可使用。在公网容量不足时提高预算可能只把本地拒绝变成网络排队。

## 硬件属性读回并不代替实际输出测量

Swift实验编码器在独立 `/private/tmp/huoguo-udp-burst-encoder`，没有覆盖生产 `hardware/macos-h264`。默认不启用第二窗口，正式行为保留。启动及码率更新记录 `AverageBitRate`、`DataRateLimits` 设置状态和属性读回。

4Mbps/100000B请求 `[750000,1.0,100000,0.08]`，苹果硬件接受/读回相同；两轮最大AU为110309/109697B，完整wire148950/148190B，16Mbps理论序列化约74.5/74.1ms。两轮未出现host整帧预算拒绝。

4Mbps/64000B请求 `[750000,1.0,64000,0.08]` 同样成功读回，但第一轮仍产生103614B AU / 140784B wire，在12Mbps需要93.856ms。连续失败还产生121058/120678B wire，分别80.706/80.452ms，刚好超期限；frame359至422才恢复，手机输入断供约1133.5ms。

Apple官方定义仍是decode-time窗口硬上限，且有有效timing和编码器支持条件。当前组合实际输出没有提供想要的整AU大小保证；具体忽略、量化或码控原因未证实，不能擅自解释成“API只是软平均值”。`MaxH264SliceBytes`仅限制slice，`MinAllowedFrameQP`仅限制QP，也不能当整帧字节数保证。[DataRateLimits](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_dataratelimits)、[MinAllowedFrameQP](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_minallowedframeqp)

快速恢复原型把native、主机编码反馈、手机KEYFRAME反馈的冷却一起选为100ms；原默认仍500ms。每个断链epoch最多六次，完整送出的IDR才重置epoch，失败P参考链继续阻断。编码降目标与IDR命令按序写入本地worker，编码器接受值另读回；尚未增加带序号的在途ACK控制、全局每秒恢复上限或真实公网拥塞回升算法。

## 100000B第二轮剩余两次停顿

- 8.646–8.757s的SF间隔110.920ms：之前完整AU接收/codec输入已分别停顿118.891/118.527ms，随后又有约104ms间隔。客户端发送KEYFRAME，但该时段FEC过期累计仍0。全会话inbox溢出2次、清8帧、恢复2个epoch；没有逐epoch时间，不能准确把两次溢出各自分配到该间隔。
- 30.540–30.684s的间隔143.812ms：30.633s首次观察到FEC参考帧过期，30.650s收新IDR、30.6526s入codec、30.6573s ready，30.6843s实际SF恢复。恢复IDR输入到ready仅4.716ms。这次明确包含重组端参考丢失及等待IDR，不能称手机花143ms硬解一帧。
- host预算、输出deadline、依赖丢弃全程采样均0；手机codec input timeout0、接收循环超过80ms次数0。旧的host拒绝断供和同步codec挡住socket不能解释这两次全部现象。仍需区分捕获/编码供给、网络集中到达、inbox压力和重组时限。
- guest与phone Surface各用本机monotonic，缺少跨时钟校准，不能直接相减宣称逐帧端到端时延。第二次发生在5–30s主窗口之外，但仍在全采样内，不能删掉或说成启动抖动。

## 保留的工程改动与下一道验收

实验入口支持独立encoder、可选短窗口、async ingress、arrival-only clock、实际显示mode读回、100/200/500ms有界恢复以及自动原始采样。普通App的clock默认、生产编码器、账号、NPS和Clash没有被本轮实验替换。主App1.29同版本重新编译安装只增加兼容实验构造入口；测试组件独立安装，未发布新升级包。

下一步需要记录每次inbox epoch及首/末片到达时间，进一步区分剩余完整AU断供；独立验证源端关键帧大小约束/长期参考恢复，补接收侧带宽/RTT/排队反馈。再接真实UDP P2P/国内UDP中继、断网重连、V50和声学音画同步。Apple的LTR-P方向与大IDR拥塞有关，但需要token、接收/解码ACK和合法参考图，不能只打开EnableLTR。[Apple低延时编码与LTR](https://developer.apple.com/videos/play/wwdc2021/10158/)

离线38项Python检查、22项实际Java inbox状态检查、独立Swift编译/参数校验，以及native编译/CTest/默认坏参考链恢复回归通过；其结论不替代真机。保留[默认原生回归](evidence/native-iteration-20261001/udp-fast-default-regression.json)和[有界冷却定时检查](evidence/native-iteration-20261001/udp-fast-cooldown-regression.json)。

[最后健康回查](evidence/native-iteration-20261001/m1-after-udp-ingress-burst-readback.json)确认VM仍为6核/16GiB/720×1280、永不息屏，旧网关及下载页HTTP200，手机本轮密钥/报告文件已清除。所有证据在本工程；未提交凭据、二进制、APK、AVD磁盘或原始worker日志。
