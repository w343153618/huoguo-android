# 认证 UDP 候选：发送背压与有界重试

本文件记录独立机主 UDP 候选的主机源码和离线验收。正式 v1.30 媒体协议未改，generic `AuthenticatedSender` 仍默认 `legacy_socket_timeout`。真实手机两轮复测由主任务另行记录；本文件当前不声称已解决公网掉帧或已完成 UDP 产品验收。

## 触发问题与可确认范围

此前一轮主机出现 `TimeoutError`，video 与 network_feedback lane 有 send error，Python `socket.send` 调用最大经过时间约 101 ms。候选 worker 的接收 socket 设有 100 ms timeout，同一 Python socket 对象也供发送线程使用。sender 在认证／发送 mutex 内调用它，真实 socket timeout 原样传播；video guard 只把 `PacingDeadline` 作为有界帧丢弃处理，故 socket timeout 可以触发整个 worker 的 revoke。

`max_send_syscall_ns` 是 Python `socket.send` 调用的经过时间，包括其 timeout/readiness 等待和可能的调度间隔，不能仅凭这个值断言一次内核 send syscall 阻塞了 101 ms。原报告也没有精确、不可覆盖的 failure role／operation，不能确定两个 lane 的异常先后根因。

上游停止后，手机仍可能保持已有 UDP 界面及 receive loop。旧 helper 的睡眠和 sampler 完成标记只证明采样过程结束；静止 Surface 的历史 ring 不能证明整段持续视频。4.5 秒 active 后停止的视频，不应被写成正常的 30 秒 40 FPS 稳态窗口。主任务已另补 helper 的持续增长观测；这里不改 alpha3 Probe。

## 两轮真机依赖的冻结源码

| 文件 | SHA256 |
|---|---|
| `udp_lan_worker.py` | `ac3abd08e3e9a5dba53bd6b9f3d0658e11a315f4d4f4ac5587453076cc6278f9` |
| `experiments/moonlight-v2/transport/android-udp/udp_session_sender.py` | `7f90a12ef8df09975d1ffdd2a3f2dbcf7d99b36e16f27427ce21b06696929535` |

上表是两轮真机实际依赖，不是随后诊断分类补齐后的 sender SHA。测试原 APK、共享播放时钟和源码 alpha3 不变；应将这些主机 SHA 和手机实际 APK／helper SHA 一起写入每轮依赖记录，不能把新的主机策略追认到旧轮。

## 只在候选入口启用的发送策略

认证 READY 且核对现有准确 peer 后，worker 在 lifecycle lock 内创建 `socket.dup()`。接收 timeout 模式原本已在共享内核 socket 上开启 O_NONBLOCK；将 duplicate Python wrapper 的 timeout 设为 0 不改变 reader Python 对象的 100 ms timeout。读回必须确认不同 owned FD、同一本地／peer endpoint，以及 reader timeout 保持原样。监听端口、绑定 interface、socket buffer 和路由不改变，不通过放大 SO_SNDBUF 隐藏排队。

候选显式传入 `owned_nonblocking_deadline`。正常 generic sender 和其他既有调用仍使用默认策略。发送语义为：

- Video 的第一次与后续尝试始终使用原始 frame capture＋lifetime deadline，不改写成“重试开始＋80 ms”。缺少原 deadline 的候选 video 调用会拒绝。
- `EAGAIN`／`EWOULDBLOCK` 只造成一次未成功的尝试。认证／发送 mutex 释放后，最多等待 5 ms 的 writable 片段，再检查取消、原 deadline 和完整 shard 的 serialization headroom。pacer 仍使用既有预算、credit、priority debt 和 guard。
- 每次实际重试重新 seal 并消费新 nonce。已经用于失败尝试的 nonce 不复用；等待期间 priority lane 可以先发送。首次 pacer reservation 不会因重试重复预留一遍数据。
- Video 预算耗尽或最多 64 次 would-block 尝试耗尽时，抛出 `PacingDeadline`，进入已有参考链 guard／IDR 恢复。64 次上限还约束 spurious-ready 或测试时钟不前进的情况，它可以在 frame deadline 前保守拒绝，不是假称已到 deadline。
- Audio、touch ACK 和 network_feedback 的 would-block 预算为 10 ms；这是每次重查的 deadline，不是对 mutex／OS 调度延迟的硬抢占保证。耗尽明确计为 late drop，返回 0，不增加 sent datagrams 或 encrypted bytes。它们继续使用已有 AAC/config 重复、touch ACK 重试和 ping 过期行为；这不证明该次音频、触控或 ping 已到达手机。
- 除明确的 would-block 外，sender 将原 hard socket error／TimeoutError 原样抛给调用者，不吞 ENETUNREACH、EPERM、部分 UDP send 或其他硬错误。Video、audio 和 ping 的 background 调用会按原行为记录 worker failure 并收尾；touch ACK 的既有 callback 则捕获异常并增加 `ack_errors`，保持原非致命 ACK 策略，不会自动产生 worker `failure_events`。`select` 等待错误亦原样抛出。取消在新的 seal/send 前检查。
- stop 先禁止新 seal/send 并 exactly-once 关闭 owned writer duplicate，随后按原 worker 流程关闭 reader／guest／native pipe。正常 generic sender 的 close 不关闭非 owned socket。

非阻塞消除了该 Python wrapper 自身的 100 ms socket timeout 等待，不能保证任意调度环境下 wall-clock send 调用都低于某个毫秒数。已经成功 posted、随后记录为 late 的 UDP 不可能被事后撤销，原 late completion 计数保留。

## 新的读回与失败字段

`owned_send_wrapper` 包括 dup 建立、reader timeout 保持、同 endpoint／peer 和读写 timeout。`udp_send_policy` 包括真实选用策略、拥有 writer FD、5 ms 片段、10 ms priority 预算、64 次上限、fresh nonce、等待不持有认证 mutex，以及 close attempted／confirmed。

各 lane 新增 would-block 调用／重试、累计与最大 writable 等待、操作总时长、would-block expiry drops、priority budget drops 和 retry-bound drops。旧 `send_errors` 仍计数失败的 OSError 尝试，其中 now 包含可恢复的 would-block；它本身不能被当成 fatal worker 数量。`max_total_send_call_ns` 的边界是 candidate nonblocking operation，首次外部 pacer wait 另由既有 pacer 指标记录。

worker 的 `failure_role`、`failure_operation` 和首次观察 host monotonic ns 使用固定枚举／数字，第一条不被后续竞态错误覆盖；最多八条后续事件并记录淘汰数。操作包括 socket send、writable wait、cancel、sender state、seal 和 formal busy guard。未插入具体操作 hook 的异常明确为 `not_instrumented`，不编造精确阶段。只记录 class、受限 role／operation／lane、errno 和观察时间，不写 exception text、key、账号或 endpoint。首次观察仍不等于已证明根因先发生。只有实际传播到 worker failure 记录入口的异常才进入这个 ring；touch ACK callback 的捕获错误须看 `native_touch.ack_errors` 与 lane 计数，不能把空 worker failure ring 解释成所有 lane 无硬错误。

## 已完成验收

最终共 91 项针对性检查通过：新 writer 十项、旧 sender 四项、既有 deadline 四项、socket pacing／参考链二十九项、worker 四十四项。范围包括：

- would-block 重试消费 fresh nonce，等待时 authentication mutex 可用，priority 可以在该等待期间成功发送；
- Video expiry 进入现参考链 guard，未成功的数据不被计为 `media_data_complete`，后续新 IDR 仍可开始；
- 三个 priority lane 的 10 ms expiry 计 drop、不计 sent；无时钟进展仍有 64 次上限；
- hard error 原对象与 class 保留，固定 operation／lane 可读回；cancel 有界，owned close exactly-once；
- **实际、仅本地 owned UDP duplex fixture**：原 reader 100 ms、writer 0 ms、FD 分离、同 endpoint、原 SO_SNDBUF；并发四 lane 的加密数据与 reader 方向同时工作，nonce 唯一，关闭 writer 后 reader 仍能收包；
- 原认证/replay、peer pin、formal busy、feed 完整记录、参考链、native EOF 和 exactly-once worker cleanup 回归保持。

这些检查没有连接手机、虚拟安卓、Tailscale、NPS 或公网。localhost duplex 验证 FD／锁／close 功能，不能代表视频帧率、家庭 Wi-Fi、Tailnet 或异地性能。

## 两轮结束后的最终诊断分类

独立只读 review 未发现阻止两轮候选复测的问题。两轮完成后才补一个诊断细节：`select` 与 owned writer close 竞争时，负 FD 可能抛 `ValueError`。等待阶段现对 `OSError`／`ValueError` 附加同一固定 `udp_send_wait` operation，原 error class 原样抛出，重试／丢弃／取消策略不改变。新 fixture 注入首次 send would-block，然后 exactly-once 关闭真实 owned duplicate，并调用真实 `select` 产生负 FD `ValueError`；验证 operation 分类、reader 100 ms 和 FD 保持、无成功 datagram 与 close 一次。

最终 sender SHA256 为 `36fbebc4cf2cef4a58ea28e5d9302dad51ae5eea218e7e557161dcdb03854b0b`；worker 仍为上表 `ac3abd08...`。最终分类源码经上述离线检查通过，**它没有被追认为两轮真机使用的 `7f90a12...` 源码**。

## 后续重放窗口与网络证据

每次 would-block 重试消费 fresh nonce 保证不复用，但大量未成功尝试会推进 sequence。它们可能消耗手机 4096 项 anti-replay window 的网络重排余量；不能仅由“nonce 唯一”推导极端背压和乱序下仍零拒绝。当前不扩大 replay window，也不改重试策略。后续需联看各 lane `would_block_calls`／`would_block_retries`、真实 drop、手机 `replay_errors`、FEC／参考链与路径乱序证据。两轮中未观察到 would-block 时，也不能声称真机已验收了背压重试分支。

## 已完成的有限真机复测

两轮保持1080P／4M VBR／60 cap／80 ms、lead0／PCMoff，现有限频一加12和同一个alpha3 APK，使用新增持续进度检查的helper。实际公开BBB源逐轮读取itag299/avc1/1920×1080@60，主机策略与dup契约读回通过，正常播放、退出、新认证与完整native收尾通过。见[两轮详细报告](udp-send-backpressure-real-video-20261003.md)。

手机SF端点cadence为59.430/59.437FPS，但仍有232.056/174.035ms长空档邻近Inbox overflow。两轮及重连共四个session没有send error或would-block；因此真实数据未覆盖重试分支，也不能把平均节拍接近60解释为稳定60或证明所有卡顿修复。native四次自然退出0、无TERM/KILL；新helper及候选端口已清理。最终全仓939项unittest通过。真实依赖的sender为`7f90a12...`，结束后补分类的`36fbebc...`只通过离线fixture，未追认为真机受测。正式v1.30、NPS、国内出口和两个Mac的Tailnet身份保留。
