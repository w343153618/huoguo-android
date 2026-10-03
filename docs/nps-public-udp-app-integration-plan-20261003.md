# NPS 公网认证 UDP App 适配计划（2026-10-03）

状态：**只读源码评估，未实现、未构建、未运行手机媒体验收。** 本文不改变冻结源码、Mac/NPS 服务、现有账号或原有正式 TCP 入口。公网 UDP 任务与有界 HMAC 往返由主任务独立验证；即便往返成功，也不能当成 App 音视频、多指触控、帧率或朋友隔离验收。

当前正式 NPC 桥接已迁为 QUIC，见 [正式部署记录](nps-formal-quic-deployment-20261003.md)。本文评估的是再增加一层 **App 认证 UDP → NPS UDP 任务 → NPC → Mac 认证 UDP worker**。它与旧 App 的 TLS/TCP 媒体是不同产品路径。原有 TCP15556/15558 应保留，不能直接把 TCP 任务改成 UDP 任务。

## 目标地址与真正的套接字边界

| 层 | M1 | M5 | 身份 / 语义 |
| --- | --- | --- | --- |
| App 公网 HTTPS 控制（拟复用） | `146.56.249.175:15556/TCP` | `146.56.249.175:15558/TCP` | 原账号认证、短期描述、状态、取消；旧正式网关目前没有 UDP 控制路由 |
| App 公网 UDP 媒体（独立新增任务） | `146.56.249.175:15556/UDP` | `146.56.249.175:15558/UDP` | HGUE 认证数据报；不能由 TCP 测试成功推断其可用 |
| 各 Mac 本地 UDP 目标（拟定） | `127.0.0.1:45965/UDP` | `127.0.0.1:45965/UDP` | 独立高端口；先查监听冲突；仅自身 NPC 代理交付 |
| NPC → 云桥接（已有正式配置） | Mac loopbackUDP48126 → 云UDP8025 | Mac loopbackUDP48126 → 云UDP8025 | 各自独立 NPC 身份、既有国内物理出口；可靠 QUIC stream |

TCP 与 UDP 可以使用相同数字端口，但属于不同监听器和不同 NPS task。独立候选 HTTPS 控制仍应先使用冲突检查后的专用高端口任务，保留 LAN45560/UDP45963 与所有历史实验合同；只有兼容性验证后，才将 `/udp/session` 路由接入原有公开 TCP15556/15558。这里不能把新候选的 HTTPS 监听器直接替换旧正式网关，因为旧 App 还依赖 `/session` 与 `/stream/...`。

必须分清三个地址：**用户选中的公共控制入口**、**描述中可见的公共媒体入口**、**仅 worker 可见的 Mac 本地 bind 地址**。本地 bind 地址和配置不能由 App 提交，也不应发送给手机。M1/M5 profile、证书与虚拟机必须逐一绑定，不能只以同一个公网 IP 区分设备。

## 从固定上游源代码可以确认的 NPS 行为

评估使用仓库内已固定的官方 v0.34.7 源码，位于 `docs/evidence/nps-provenance-20261002/source-review/v0.34.7/`；此目录是既有审计证据，不是新修改的 NPS 实现。

1. `server/proxy/udp.go:72` 以公网来源 `IP:port` 为 key。每个来源建独立 entry，`clientWorker` 建对应 bridge link；队列容量是 1024 数据报（`:78`）。返回方向由云监听器向原公网来源发包。
2. `client/client.go:441` 到 `:461` 为 UDP target 调用 `net.DialTimeout("udp", target, ...)`，再按 UDP framing 复制。目标是 loopback 时，Mac worker **看到的是 NPC 的 `127.0.0.1:ephemeral-port`**，并非原手机公网地址；HTTPS 经 NPC 代理的 peer 也不能视为手机身份。UDP 与 HTTPS 的 NPC 本地源端口不必相同，不能跨两套套接字比较源端口。
3. `server/proxy/udp.go:131` 到 `:144` 通过 bridge 后用 `WrapFramed(net.Conn)`。`lib/conn/framed.go:24` 到 `:43` 用 `io.ReadFull` 顺序读完整 framing。`lib/conn/quic.go:13` 到 `:31` 明确持有 `*quic.Stream`，`Read/Write` 均转调 stream，**没有使用 QUIC Datagram 媒体 API**。
4. `server/proxy/udp.go:182` 的 `ProxyProtocol` 可在首次数据报前插入代理头。HGUE worker 要求从第一个字节开始认证整包，所以相关 task 必须核对并保持 `ProxyProtocol=0`。不能为了获得来源 IP 打开代理头而不增加独立、严格的解析合同。

由上述实现可推断：同一手机 UDP socket 在公网来源 tuple 稳定且该 entry 存活时，应经同一 NPC target socket 到达 worker；手机切 Wi-Fi/流量、NAT 重绑定或 NPC 重连可能创建新的本地 source tuple。该推断仍要用有界往返和真实 App 实验验证，不能用源码推断替代运行验收。

**性能边界：** 手机到云端 UDP 与 NPC 到云端 QUIC 的外层确实都可为 UDP，但 NPS 内部媒体仍经过可靠、有序 QUIC stream。桥接链路丢包时，同一个 stream 的后续 framed 媒体仍可能等待重传；1024 数据报排队也可能累积超过 App 的 80 ms 可用播放预算。QUIC 多 stream 可减少互相牵连，不会把单 stream 改成不可靠实时数据报。这是可测的兼容方案，不是最终无可靠排序中间层的 Datagram 中继或 P2P 产品；不能宣称仅改端口或启用 QUIC 就必然更流畅。

## 必须同时适配的精确合同

| 当前位置 | 当前限制 / 关联 | 最小适配边界 |
| --- | --- | --- |
| `udp_network_scope.py:103`、`:123` | 只有物理同 `/24` LAN 或精确登记的 M1/手机 Tailnet；拒绝 loopback / 普通公网 | 新增独立、明确的 `nps_public` scope，不扩大现有 LAN/Tailnet 范围。公开 endpoint、node profile 与本机 loopback backend 由服务端配置固定，proxy peer 只允许精确 loopback。网络健康校验保留国内物理出口及预期 NPC 身份 / 本地代理；它不能用 loopback 来证明手机的国内来源。国内来源过滤仍由云端真实来源规则负责，不修改过滤。 |
| `udp_lan_gateway.py:142` 到 `:173` | POST 先检查 HTTPS peer，再认证；把 HTTPS peer IP 当 UDP worker peer | 公网模式将 HTTPS socket peer 视为可信本地代理入口，不当作用户身份。继续使用现有账号 auth、登录限流、有界 JSON、单会话保留。HTTP peer 只能作为代理位置约束；媒体授权来自短期会话密钥。不要要求媒体 peer port 等于 HTTPS source port。 |
| `udp_lan_gateway.py:69`、`:201` | `formal_busy` 固定 M1 TCP15556；CLI 固定 LAN/Tailnet、45560/45963 | 使用明确 node/guest 配置；M5 不可照搬 M1 硬编码。候选入口仍以正式会话保护为准；未来接入同一个正式 TCP 端口时不能把本次 HTTPS 认证 socket 自身当作已有媒体会话，需要使用账户绑定的真实会话 reservation / 活跃媒体状态与共享准入锁。仅忽略自己 socket 不足以防竞态。 |
| `gateway.py:232`、`:334` | 正式 POST 只识别 `/session` 等既有路由；CONNECT 是 `/stream/...` TLS/TCP | 保留旧路径，在统一账号验证和共同 guest reservation 之下，显式增加 `/udp/session` POST/GET/DELETE 委托。实验期可先用独立高端口控制 task；新增 UDP NPS task 本身不会给正式网关补路由。未获描述或 UDP READY 失败要明确报错，不能转旧 CONNECT 媒体。 |
| `udp_lan_sessions.py:37`、`:116`、`:262` | 只允许 `lan/tailnet`；同一 `peer_host/peer_port` 同时用来公开描述和 worker bind | 显式拆为服务端固定的 `advertised_endpoint=(146...,15556或15558)` 与 `local_bind_endpoint=(127.0.0.1,45965)`。仅 advertised tuple 进入 HTTPS 描述。scope/profile、账户、guest 和选定地址一起校验；不允许客户端提交任意 target/bind 地址。READY10 s、ALIVE3 s、最长120 s、tombstone、单会话与 exactly-once 收尾先保持原行为。 |
| `udp_lan_worker.py:91` 到 `:100` | socket 绑定所选接口，并在 `host_ip:config['peer_port']` bind | 公网 relay 模式绑定 `127.0.0.1:45965`，接口应精确为 `lo0` 并读回；不能绑 `en7` 后向 loopback 收发，也不能在 Mac bind 云 IP。物理出口绑定属于 NPC 外层配置，不能混作 worker 本地接口。原 LAN/Tailnet 分支保持原 bind 合同。 |
| `udp_lan_worker.py:163` 到 `:207` | 首先核 peer IP，再 GCM+ReplayWindow；首个有效 READY 后 pin 完整 tuple、connect、创建共享端点非阻塞 sender | 公网模式只接受 loopback；first READY 仍必须通过正确 session/tag/key、方向 nonce 与 replay 校验后才能锁定 NPC source tuple。锁定后外来 tuple 全拒绝。最小版本 NAT/代理 tuple 变化要求新 HTTPS 认证与新会话，不能无认证自动迁移或把 ALIVE/触摸误作准入。继续 STOP/DELETE/ALIVE 超时撤销与真实 ACTION_CANCEL。 |
| `app/src/udp/.../LanUdpContract.java:30` 到 `:51` | 登录只允许45560及 LAN/精确 M1 Tailnet；描述必须等于 loginHost，UDP45963 | 新增显式公网 M1/M5 profile 和所选 tuple。校验必须接收真实控制端口，不能继续 `validateLogin(loginHost,HTTPS_PORT,scope)` 隐含固定45560。node/profile、控制 tuple、UDP tuple、scope 必须匹配；不广泛允许任意公网或任意端口。保留旧 LAN/Tailnet 接口兼容与纯 Java fixture。 |
| `app/src/udp/.../AuthenticatedLanUdpUi.java:41` 到 `:147` | 只显示 M1 LAN/Tailnet，地址与选项写入 prefs；JSON scope 只有这两种 | 加明确“公网 NPS UDP · M1 / M5”可选 profile，与正式 TLS/TCP UI 明确区分。保存用户最后选择但不复用另一个 scope 的地址；给出桥接 QUIC stream 的边界。保留 generation/cancel/retiring 禁抢占和按所选控制端点 DELETE。 |
| `AuthenticatedLanUdpUi.java:170` 到 `:184` | HTTPS 经既有 TLS 工厂，额外核证书 SHA256 pin；有界读取描述 | 保留 TLS 和 pin；M1/M5 profile 应绑定可信 node pin（可校验既有明确许可的轮换集合），不能因公网 IP 使用自签证书而关闭校验。TLS 仅信令，测试时记载实际控制/媒体路径。原账号口令与 session key 不进入报告或 Git。 |
| `experiments/nps-transport/phone/UdpVideoProbe.java:997` 到 `:1028` | App parseSession 再一次固定 UDP45963；独立 legacy probe 15961/15960 | 公网扩展必须同步这一层，优先传入已验证的不可变 endpoint/profile 合同再校验 descriptor。不能只改 UI 而留下 Probe port 二次拒绝；不能把历史 standalone 合同改成公网默认或广泛放开所有端口。 |
| `UdpVideoProbe.java:579` 到 `:621` | 手机每次仅接收 descriptor 指定的公网 IP:port，再 GCM/replay；同一 UDP socket 发 READY/ALIVE/反馈 | 继续固定接收云公网 tuple、认证与方向序列；NPS 通过该 public listener 回复，不能让手机接受 Mac loopback tuple 或任意 peer。音视频、native touch、反馈和 STOP 都用本 UDP socket；HTTPS 只鉴权/取消。 |

只改 `peer_port` 或把所有 scope 判断删除，不满足该合同。当前 parseSession 能解析 unicast IPv4 不等于 App 的上层 contract 已许可该路径。新 descriptor 如果有版本/schema 字段，App、helper、纯 Java fixture 与 server fixture 必须同时冻结并记录 SHA；不得追认旧 APK 已支持。

## 认证、安全与上线条件

短期会话仍由可信 HTTPS 和现有账号建立。App 与 Mac 之间保留端到端 AES-GCM、方向 nonce、单调序列和 replay window；NPC vkey 只是桥接身份，不能代替 App 用户认证，也不能交给手机。第一份合法 READY 只授权所选 node 上的该会话，另一 node 的密钥、旧 session、错误方向、重复包和错 tuple 都必须拒绝。

loopback peer pin 保护的是本地代理交付位置，不是来源证明。不能信任手机自报公网 IP、HTTP `X-Forwarded-For` 或未认证的 NPS proxy header。若未来支持 NAT migration，需另行设计有认证的路径挑战、限速及 replay/sequence 连续性；本轮最小候选保持断开后重新认证，避免无界放开 peer。

公开 UDP listener 会在 App 认证之前创建 NPS entry 与 1024 项队列。云端国内来源过滤、连接/流量限制及资源上限须保留；有界 HMAC/GCM 测试不等于抗大量来源的资源验收。媒体失败、认证描述失败、READY 超时、取消及进度失速必须显式报错并收尾，不能悄悄走 TCP 媒体，也不能用超过100 ms缓冲掩盖问题（推荐仍为80 ms）。

**朋友正式 M5 部署不能跳过宿主与 LAN 隔离。** 仅发布带 UDP 的 APK，不能限制已 root 的 guest 经网络访问 Mac/LAN。还需完成：guest 无宿主共享目录/任意 ADB 文件或命令通道；服务进程只接触必要的受限 runtime；账户绑定的文件 API 只落 Android 可见受限媒资；guest 到宿主文件与敏感服务的访问限制；guest 到家庭 LAN 的禁止横向访问与例外白名单；root guest 尝试访问时的负向验收。真实朋友使用 `huoguo`，机主通常使用 `wyw`；当前 M1 运行认证记录只有 `huoguo`，此前用户已明确授权以这个既有账号完成机主实验，不新增账号或修改权限。用户账号边界不等于 OS/网络隔离。机主有界原 M1 实验可标记非隔离继续，不把这份例外推广给朋友使用。

## 有区分力的最小验证顺序

1. **源码 fixture**：endpoint 分离、合法 M1/M5 profile、错节点/错端口/错 scope/public→loopback 混淆全拒绝；旧 LAN/Tailnet/legacy standalone 合同保留。GCM/replay/方向/READY 准入、tuple pin、重复 READY 不续活、取消竞态、旧 generation 不影响新会话、cleanup barrier 按现有 owned fixture 验收。
2. **有界公网 task 往返**：在独立高端口本地 listener 记录实际 NPC loopback source tuple 和回复的 public source tuple；核 ProxyProtocol=0、原正式 TCP 与所有原 NPC 身份仍正常。记录来源网络、路径和超时，不上传凭据或原始敏感日志。这只证明 UDP task 连通，不证明 App 已适配。
3. **M1 机主真实 App**：沿受信 HTTPS 取得 scope/node/endpoint descriptor，通过同一 UDP socket READY；真实 YouTube 60 FPS 内容，固定源尺寸、码率与80 ms缓冲、保留限频手机，采集认证接收、native 重组/FEC、Inbox、codec、独立 SurfaceFlinger 呈现/覆盖/长空档、音频时钟和单/双指四角、取消/重连。callback target 回显不能当真实呈现；报告注明 NPS 内部 stream 和非隔离 owner 环境。
4. **网络对照**：同 APK/源内容/参数比较该公网 UDP+QUIC-stream、登记 Tailnet 实际 direct/relay 的内层 UDP，以及后续国内独立 Datagram relay；控制只变一个因素。先 Wi-Fi 公网，再手机流量/异地；不得用同家 Wi-Fi 往返代替东北 V50、移动网络或 P2P 验收。既有 TCP正式版可留作明确独立对照，不能作后台fallback。
5. **朋友发布门槛**：M5正式在线不打断；先另候选完成宿主/LAN/权限与现有账号负向验收，再在空闲维护窗口验证 M5 实际 guest/runtime 兼容、声音、touch 和真实公网 V50。通过后才给实验渠道可选公网UDP；未经这些分层证据，不替换正式默认，不宣称稳定60/120FPS或UDP产品完成。

当上述公网 UDP 兼容方案仍受 stream 排队/可靠重传长尾限制时，下一项才是 **独立可关闭、认证的国内 UDP/QUIC Datagram 中继**：云端与 Mac/手机配合真正 Datagram path，保留会话鉴权、防重放、截止时间、拥塞/FEC/触控取消，旁路现有 `WrapFramed(quic.Stream)`。它需要新服务端/客户端合同，不能靠启用现有 NPS UDP task 获得；也不能把此候选连通解释为已经完成智能打洞/P2P。

## 后续状态（2026-10-03，保留上文计划时态）

独立 `udp_nps_profile.py` 已完成21项组件检查，M1 owner候选control49556/TCP→local45561、media15556/UDP→local45965/lo0固定；M549558仅规划且friend gate拒绝。尚未集成认证gateway、session registry、worker或App。LAN alpha5已按精确d863302发布及启用有界机主试用，见[记录](experimental-alpha5-release-20261003.md)。试用活跃时可做离线集成，不得抢占候选或并开第二媒体registry。
