# UDP 会话撤销、认证入站心跳与结束提示设计，2026-10-03

本轮仅做源码审查并提出下一轮候选，没有修改 App、gateway、worker、正式 NPS 或手机，也没有执行设备测试。目标是让已经建立的认证 UDP 会话在 host 撤销或入站 UDP 路径失效后及时停止输入、遮掉最后一帧并明确提示；静态画面本身仍然合法，不能用“多久没有视频帧”代替会话存活判断。

## 已有证据的边界

[上一轮 startup 真视频记录](codec-startup-ready-real-video-20261003.md)包含一次 Darwin ENOBUFS 导致 host 撤销，手机 worker/callback 随后约 13.019 秒没有新进展。这是计数与生命周期观察，不是用相机测得的屏幕冻结时间。[本轮 ENOBUFS 正常路径](udp-enobufs-real-video-20261003.md)没有执行自然 ENOBUFS 恢复分支，也没有重复这个长尾；不能据此称撤销提示已经修复。

该候选属于机主原 UID501 的 M1 非隔离测试环境。正式 v1.30 仍是 TLS/TCP App 媒体；正式 NPC 的 QUIC stream 不等于本候选的原生 UDP 媒体。本设计不改变正式15556/15558、M5日常会话、国内出口、NPS程序或 Headscale 身份。

## 当前源码能证明什么

以下行号是本轮读取时的源码定位；实现后应重新定位，而不是把旧行号当成版本标识。

| 通路 | 当前行为 | 实际含义与缺口 |
|---|---|---|
| 手机 `receive()` | [UdpVideoProbe.java:579](../experiments/nps-transport/phone/UdpVideoProbe.java)每750ms发送认证 `ALIVE`，20ms socket timeout 后继续循环；截止时间由整体窗口/首个视频包决定 | 出站发送成功不证明 host 还存活。没有“最后一个有效 host 心跳距今多久”的判定。20ms是 socket 等待设置，不是20ms断线通知承诺 |
| host 接收 `ALIVE` | [udp_lan_worker.py:201](../udp_lan_worker.py)调用 `authenticated_alive()`，没有回复确认 | 单向续期。host 已撤销、socket已关闭后，手机仍可能继续发送并保留旧 Surface |
| host 租约 | [udp_lan_sessions.py:111](../udp_lan_sessions.py) READY10秒、ALIVE3秒；`authenticated_alive()`只允许当前 starting/active 会话，不能延长 hard deadline | host 已有独立的权限关闭机制，不能为了手机心跳放宽或重建已撤销会话 |
| host `HGPQ` / 手机 `HGPR` | [feedback_controller.py:121](../experiments/moonlight-v2/transport/android-udp/feedback_controller.py)最多每1秒发一个16字节 `HGPQ`；手机认证后回32字节 `HGPR`；host校验已签发 token 和时间边界 | 当前用途是 host 时钟上的 RTT 反馈，不是手机失效看门狗。可以复用为已建立会话的第一轮认证入站候选 |
| host ping 的启动 | [udp_lan_worker.py:259](../udp_lan_worker.py)先构造硬件会话，再创建包括 `_ping` 的媒体线程 | 现有 `HGPQ` 不是冷启动时立即返回的“登录成功/租约仍有效”确认。不能从 UDP socket 创建时就用3秒阈值，否则可能误杀正常硬件启动 |
| host 撤销 | [udp_lan_sessions.py:191](../udp_lan_sessions.py)先关闭权限并建 tombstone，再在 registry 锁外清理；[udp_lan_worker.py:651](../udp_lan_worker.py)停止 sender/touch/硬件和 owned socket | 没有认证的终止通知数据报。权限关闭与资源退出不同，不能把自然 native exit0 当成全程健康 |
| App 结束回调 | [AuthenticatedLanUdpUi.java:186](../app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java)先写数值报告、同步 DELETE，再显示登录页 | DELETE使用5秒connect/7秒read设置，可能独立推迟结束提示。这不是一次全请求固定12秒上限，也不能凭源码断言它就是历史13秒的原因 |
| App 主动取消 | [AuthenticatedLanUdpUi.java:210](../app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java)标记当前 attempt/retiring，取消所属 receiver，异步 DELETE，UI显示离开提示 | 有可复用的退出形态，但新看门狗必须捕获 attempt/generation；不能从旧会话调用全局 `failed()`/`cancel()`，否则可能误伤后来的会话 |
| HTTPS status | [udp_lan_gateway.py:125](../udp_lan_gateway.py)已有账号认证的 `GET /udp/session/<sid>` 返回当前状态或404；`/ping`不查账号会话 | 可做撤销原因的辅助确认。`/ping`成功不能给媒体会话续命；HTTPS仍通也不代表 UDP 媒体可用 |

手机现在在 [UdpVideoProbe.java:594](../experiments/nps-transport/phone/UdpVideoProbe.java)先核对 peer IP/port，再在 [UdpVideoSecurity.java:42](../experiments/nps-transport/phone/UdpVideoSecurity.java)验证 session tag、AES-GCM、方向nonce和 replay window。这条校验顺序要保留。`authenticated++`在解析应用负载前增加，不能直接拿这个计数来续入站 liveness。

## 建议分两步实现

### 第一步：小范围验证已有 host 心跳，不改媒体/host协议

下一版实验客户端显式启用 `established_host_ping_watchdog`，默认旧路径保持关闭。只在 descriptor 明确支持 `network_feedback=true`、已收到第一个合法 `HGPQ` 后进入已建立状态；旧 probe、未协商心跳的 descriptor 不自动套用这个规则。当前 gateway 会返回 network_feedback=true，但 App 的 `LanUdpContract`尚未强制该字段，必须在候选入口中明确校验。

候选只保存固定大小状态：启用/已建立、所属 generation、最后合法心跳的 phone `System.nanoTime()`、最后接受的 HGPQ identifier/opaque host token、一次性终止标记与固定计数。合法心跳必须同时满足：

1. 来自当前精确 peer，经过当前会话 HGUE 身份/完整性校验和 replay window。
2. `HGPQ`应用负载恰好16字节、identifier非0，满足当前协议的字段约束。重排的旧 identifier/token 不能刷新时间；120秒会话内按明确的无符号/回绕规则比较，不能直接用 Java signed int 的大小关系。
3. 仍属于本 runner、当前 App generation，尚未进入停止状态。校验和续期要作为一个小的状态操作，不能与新会话共享计时器。

建议实验阈值为距最后合法心跳 **3秒**，每200ms检查一次。该值与当前host约1秒ping间隔/3秒ALIVE租约相符；这是下一轮待测策略，不是已证明最佳值。长于3秒的入站丢失即判定当前UDP通路不可用，不宣称知道host的具体撤销原因。

检查必须在收到任何包之后和socket timeout之后都执行，避免无认证包连续到达让代码永远进不了timeout分支。若RX线程可以被native/解析操作阻塞，单纯loop检查不能承诺有界响应；可使用本runner拥有的轻量watchdog线程，读取小的原子/受锁状态并发布停止请求。线程必须可取消、退出时有界join，UI和Inbox monitor下均不join。3秒加检查周期只是逻辑期限；设备调度、UI阻塞和资源收尾的实际长尾还要独立测量。

以下均不续期：收到任意UDP字节、`authenticated_packets`增长、未知应用负载、格式错误HGPQ、foreign peer、重放包、认证失败包、成功发出ALIVE/READY/KEYFRAME、视频帧或音频帧有无变化、HTTPS `/ping`成功。合法静态画面有正常HGPQ就保持连接。

提示应为“未收到服务器认证心跳，UDP连接已中断，请重新连接。”而非“服务器一定已撤销”。现有单向HGPQ虽然有认证和防重放，但不能证明包刚刚生成；攻击者可以延迟此前合法且尚未接受的包。按递增identifier/token拒绝旧包可减少乱序续期，不能把这一候选当成强新鲜性协议。

该首步只覆盖已经收到 host 心跳的会话，**不解决硬件冷启动前从未收到心跳的失效提示**。保留已有启动/整体时限并如实报告未覆盖；不凭空增加首包3秒限制。

### 第二步：协商独立的租约 challenge/echo，覆盖启动与强新鲜性

在首步验收后，再做单独的 App+worker+descriptor 候选。使用明确的 `liveness_protocol` capability；请求/响应均放进现有HGUE认证信封。消息可命名HGLQ/HGLR，最终字节布局需另立严格的跨语言fixture，不能只凭此文档实现不同的长度。

手机约750ms发送一次带当前会话请求id及随机64位challenge的请求，保存最多4个未完成项。host ingress在READY确定精确peer后即可应答，不等待VideoToolbox/guest/native启动；回复必须回显同一请求id/challenge并携带可枚举的状态。worker只在 `registry.authenticated_alive(sid)`明确返回true且会话仍允许应答时发送有效确认，不能对未知、tombstoned、scope失效或已关闭会话回应ACTIVE。hard deadline与权限检查保留，不能由challenge重置。

手机只接受当前已签发、尚未消费、在本机有界年龄内的challenge响应；重复、迟到、旧generation或未签发token均不续期。计龄全用手机本地时钟，不将host单调时间戳减去phone时间。响应可以证明host处理了最近请求，强于只收host主动ping；3秒失联阈值仍需用真实丢包/调度实验验证。

不新增独立的AES-GCM nonce计数器。所有ALIVE/READY/touch/feedback/challenge仍由既有 `sendPayload`串行分配client方向nonce，host复用当前sender的server方向序列；不同线程不能各自从1开始。每次恢复发送使用新的nonce。心跳仅用原有短的priority发送预算，不能增加无界重试或阻挡取消；registry/lifecycle锁外发包，不持锁等待网络/硬件退出。

首版不必加入“撤销后必达的CLOSED”。目前sender.close/stop_event使新seal/send停止，临时在 `stop()`末尾调用 `sender.send(CLOSED)`既可能被拒绝，也可能破坏“撤销后不发新媒体”的边界。若后续要做best-effort终止通知，必须单独定义控制专用、一次性、短预算的撤销通路并保留立即权限关闭；通知丢失时仍由phone watchdog收尾。

## App 结束提示与收尾顺序

看门狗不应等receiver完整报告、音频join或HTTPS DELETE后才遮盖旧画面。建议增加捕获attempt/generation的 `terminationRequested`通知，与最终 `finished`资源证明分开：

1. controller在自己的短锁内验证“current仍是该attempt、generation匹配”，一次性标记停止和retiring，生成本次终止的UI代际token；释放锁。
2. 立即向UI投递移除/覆盖Surface、禁用输入、显示枚举提示。UI执行时再次检查本次终止token且没有较新活动会话。UI线程只改视图，不持Inbox/registry/attempt锁等待cleanup、报告、HTTP或join。
3. worker请求当前receiver取消，清理touch/codec/audio/owned UDP；DELETE与报告写入在后台。保留retiring屏障，收尾未确认时禁止下一次启动，但提示不能继续显示像正常运行的最后一帧。
4. 最终回调只解除匹配的retiring和补充该轮数值报告。旧callback/DELETE/HTTP status/watchdog不得修改新attempt、停止新generation或覆盖它的界面。清理失败也按所属attempt处理，不能复用未带身份的全局 `failed(Exception)`。

“先显示结束”与“可以重连”是两个不同事件。音频cleanup未确认时继续禁止重连，并保持明确收尾提示。不能因为希望提示快就跳过现有音频资源证明、touch CANCEL或host reservation barrier。

可新增固定数字字段：`liveness_enabled`、`liveness_established`、`liveness_valid_pings`、`liveness_rejected_semantic`、`liveness_last_valid_phone_ns`、`liveness_max_age_ms`、`liveness_timeout_phone_ns`、`termination_requested_phone_ns`、`ui_end_visible_phone_ns`、`termination_reason_code`、`cleanup_finished_phone_ns`。在 [numericAppSummary](../experiments/nps-transport/phone/UdpVideoProbe.java)中明确白名单并保持64KiB上限；当前`failure_class`不会自动出现在App裁剪报告中。不要记录密钥、口令、challenge值、原始报文或含凭据的异常文本。

## 可选HTTPS确认：诊断用，不续媒体命

失联后可由独立、可取消、有总期限的后台请求查询现有 `GET /udp/session/<sid>`。404可以作为该认证账号的会话已不存在证据；200 active只能说明控制面尚保留会话，不能恢复已中断UDP或重置入站心跳；401/403/503分别保留认证/网络scope等原因类别；网络失败不等于确认撤销。

该查询不挡UI显示，不回退TCP媒体，不与初始认证共用会被覆盖的 `attempt.https`指针。现有generic HTTP的5秒connect/7秒read不能直接作为快速检测链。第一步可以完全不查询HTTPS，只给准确的失联提示，将“确认host撤销”留给后续独立候选。

## 下一轮验收与停止条件

先做纯状态fixture，再只在空闲、已授权机主M1高端口环境做手机试验。不要操作M5日常服务、正式NPC/NPS或云端国内来源规则。每一轮钉定APK、Java契约、host脚本和native SHA；记录为真实限频一加12、同家Wi-Fi LAN或Tailnet内层UDP，不外推到东北V50或指定公网路径。

| 层级 | 必须覆盖 | 通过条件或明确局限 |
|---|---|---|
| 纯Java看门狗 | 阈值前/等于/超过、有效静态心跳、错误peer/认证/replay/未知应用、错误长度/字段、持续错误包、乱序旧ping、旧generation、取消重复触发 | 固定有界状态；只合法新心跳续期；每轮最多一次终止；无旧会话误伤 |
| 契约兼容 | opt-in OFF、network_feedback缺失/false、旧probe、新协商能力 | 默认旧行为保留；未协商的不默默使用新失联规则。只要求新候选满足新增契约 |
| host/registry fixture（第二步） | READY前、starting中、active、hard expiry、scope失效、revoke并发、tombstone；nonce共享、ACK丢弃/超时 | 不给已关闭会话续期/ACTIVE；不持registry锁发包或清理；不扩大原host权限期限 |
| 手机健康静态画面 | ≥10秒不改变guest画面但仍有认证host心跳，然后正常leave | 不误判断开；正常音频/视频清理和gateway quiescence仍通过 |
| 手机真实视频后撤销 | 正常认证/真实60fps内容持续推进后，使用现有账号对当前候选sid执行一次有界DELETE，随后观察 | 最后合法心跳→逻辑timeout→UI遮盖分别记phone本地时间；仅当前会话结束，音频确认退出，retiring最后解除，hostfinal/quiescence完整 |
| 重新认证与旧结果 | 正常撤销后重连；投递旧generation的watchdog/finished/HTTP结果 | 新会话继续推进，旧结果不会停止/覆盖它；没有默默退TCP媒体 |
| 冷启动/部分单向丢失 | 第一阶段记录未覆盖；第二阶段才检验硬件启动前challenge应答、phone→host失效、host→phone失效 | 第一阶段不虚称覆盖所有启动失败；第二阶段独立验证初始响应与媒体bootstrap不是同一期限 |

真机撤销验收使用独立且有界的campaign，建议总观察不超过25秒，另留已定义的收尾窗口。原账号用于普通认证，不新增临时账号，不输出或提交凭据；sid只在受限运行态用于精确取消，不进入公开数值报告。先确认正式及候选会话空闲，有正式会话则跳过设备试验。

host `time.monotonic()`、phone `System.nanoTime()`与SurfaceFlinger事件时钟没有自动共同原点。可在各端各自测持续时间；跨端“host revoke→phone界面变化”需有已验证clock mapping、ADB前后时间括号或外部相机，不能直接相减后称物理/光学延时。后台清理结束也不是音画同步或真实触控延时验收。

这轮最小可行动项是：**实验客户端既有HGPQ失联候选 + attempt-scoped快速结束提示 + 后台原有清理**，以真实一次撤销及静态画面验收检查。不要同时改码率、buffer、mapping容量、codec启动、host发送策略或网络路径来解释它。冷启动及challenge新鲜性明确留给下一独立协议候选。
