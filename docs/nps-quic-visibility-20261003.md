# NPS 客户端列表为什么不显示 QUIC

最新状态（2026-10-03中午）：用户随后明确授权重启正式服务及迁移M1/M5为QUIC，已执行。正式 `bridge_type=both`、KCP/QUIC网页命令与UDP监听已恢复，两台正式NPC已用独立原身份走QUIC，38个原在线NPC全部重新上线。见[正式部署记录](nps-formal-quic-deployment-20261003.md)。本文下方的“尚未执行”均为当时历史状态，不能据此再次重启或撤销新部署。

后续状态：独立实验已迁到TCP48024、KCP/UDP48025、QUIC/UDP48026，原UDP8024/8025占用已释放；三协议高端口均完成真实实验身份认证。下面原PID/端口和连接数保留为迁移前检查。用户进一步要求保护所有正式NPC的日常运维连接，因此不能因Android会话空闲就重启正式NPS。正式配置仍tcp、菜单仍未恢复，见[高端口迁移记录](nps-high-port-migration-20261003.md)。

2026-10-03 只读检查正式服务、实验服务、当前配置、上游 v0.34.7 源码和已认证的正式客户端网页。没有修改配置、停止服务、重启 NPS、创建客户端或调整国内来源过滤。

## 当前原因

正式 `/etc/nps/conf/nps.conf` 的 `bridge_type=tcp`。`quic_enable` 和 `bridge_quic_show` 均未显式设置。

上游 v0.34.7 只在 `bridge_type` 为 `quic`、`udp` 或 `both`，且 QUIC 端口有效、`quic_enable` 为真时，才开启 `ServerQuicEnable`。`bridge_quic_show` 未设置时，网页显示开关默认跟随这个运行开关。因此，即使配置仍保留 `bridge_quic_port=8025`，正式服务也不会启动 QUIC，客户端列表的展开行也不生成 QUIC 命令。参见[运行开关](https://github.com/djylb/nps/blob/v0.34.7/cmd/nps/nps.go#L314-L317)、[网页显示条件](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/base.go#L120-L131)和[展开行模板](https://github.com/djylb/nps/blob/v0.34.7/web/views/client/list.html#L150-L152)。

已用既有管理员凭据在服务器进程内完成只读认证，正式后端 `/client/list` 返回 HTTP 200，实际渲染包含 TCP/TLS 命令，不包含 QUIC/KCP 命令。没有输出或保存凭据、会话 cookie 或完整响应体。

公网 `nps.yilufa.site` 的 Nginx Proxy Manager 配置指向 `172.17.0.1:18080`，对应正式 NPS 网页，不是实验服务。当前现象不是浏览器缓存、跳到实验页面或新增客户端菜单选项变化。

## 程序和历史边界

正式实际程序仍为 `/etc/nps/nps`，大小 25,006,242 字节，SHA-256 `9b9a36a2cae50c5e66f35c3450c9c4ddccc2ff04c839cb0df03592069c674b12`。2026-10-03 03:27:42 UTC 再次下载 djylb 官方 v0.34.7 发布包，先核对 GitHub asset digest，再与 `/proc/2242499/exe` 的实际运行程序和云端静态文件逐字节摘要比较：程序一致，55 个网页文件一致，缺失、差异和额外文件均为0。客户端模板仍含 QUIC 条件，没有删除 QUIC 功能或模板。此前完整来源核对见 [NPS 来源记录](nps-provenance-audit-20261002.md)，新受限结果为 `evidence/nps-quic-visibility-20261003/official-live-recomparison.json`。

`/etc/nps/conf/nps.conf.bak20260911` 已保存 `bridge_type=tcp`；6 月 14 日备份未显式设置 `bridge_type`。当前上游 v0.34.7 缺省值是 `both`，但不能用当前源码加旧配置证明旧版运行状态。9 月 11 日备份支持“TCP 配置早于本项目 9 月末协议实验”，不支持凭文件修改时间确定具体由谁、在哪次操作中改变配置，也不能证明所有历史版本均未被替换。

## 实验端口与恢复注意事项

当前正式 `nps.service` PID 为 2242499，启动时间 2026-09-29 15:15:38 CST。它监听 TCP 8024 和 TLS/TCP 8025，没有监听这两个 UDP 端口。

独立 `nps-android-transport-test.service` PID 为 497073，启动时间 2026-09-30 16:10:28 CST，配置目录 `/srv/nps-android-transport-test`。其 `bridge_type=both`、TCP 18024、KCP/UDP 8024、QUIC/UDP 8025、网页端口 0；当前 UDP 8024/8025 由该实验进程监听。

实验服务占用 UDP 端口不是正式页面缺项的直接原因：正式 `bridge_type=tcp` 本来就关闭这些 UDP 监听。不过，要恢复正式 QUIC，必须先给实验服务分配独立测试端口或在确认无活跃实验客户端后停止它，再让正式配置开启 QUIC 并重新加载。不能只设 `bridge_quic_show=true`，否则会显示一条正式服务尚未监听的命令。

正式进程读回共有 119 个已建立 TCP socket，M5 公网 15558 有 3 个，恢复需避开正在使用的会话。实验进程当前没有已建立 TCP socket，但 QUIC 共享 UDP socket，不能因此宣称实验 QUIC 一定没有活跃连接。恢复尚未执行；不会为一个页面选项贸然重启全部正式 NPC。

## 检查范围

SSH 从 M1 物理 `en7` 的 `192.168.9.128` 发起，保留严格 host-key 验证；云端读回国内家庭出口。没有通过境外代理连接云端。

受限脱敏证据位于忽略目录 `docs/evidence/nps-quic-visibility-20261003/`。记录仅包括配置白名单、程序摘要、监听所属进程、历史白名单、认证网页布尔结果和 socket 数量，不包含密钥、客户端 vkey、运行配置全文、原始日志或其他用户资料。

## 修改前备份与恢复候选

按用户要求，在任何运行配置修改前，2026-10-03 11:25:43 CST 已在腾讯云主机创建受限备份目录 `/root/nps-backups/before-kcp-quic-20261003-112543/`。备份包含完整 `/etc/nps`（程序、网页、主配置、客户端和隧道数据及原有备份）、独立实验目录、两项 systemd 单元及相关 drop-in、当前 NPM 对应站点配置；防火墙规则和监听状态另外保存为恢复时的参考，不能不加区分地覆盖其他服务的网络规则。秘密始终留在云端受限目录，没有下载或提交配置全文。

归档 `nps-restore-snapshot.tar.gz` 为24,921,598字节，SHA-256 `85e8647675141b13ac45a4b21cd95daf01a50824874f00207a8c25dc55e60137`。复制时核对15个运行配置文件与保存副本的摘要，随后独立读取归档并核对82个普通文件的摘要；目录权限0700、归档0600。正式主配置前后SHA相同，两项服务PID及状态未变。归档完整性已验收，未实际回滚正在运行的服务。

同目录 `staged/nps.conf.bridge-both` 已生成最小恢复候选：仅将一项 `bridge_type=tcp` 改为 `both`，保留现有端口、账号、NPC身份和其他配置；候选权限0600，SHA-256 `259672daefc9e6373f56495aec65f695d8b0eb0223beacc6a8b162d7de3a21ab`。正式文件仍未修改，KCP／QUIC尚未恢复。须先处理独立实验对UDP8024/8025的占用，再在受影响会话空闲后通过systemd重启并验证，不使用有信号编号风险且不能新增监听的 `nps reload`。

后续检查M1/M5的15556/15558已空闲，但其余NPS任务仍有90个前端已建立socket，涉及36个任务端口；这只是连接快照，不能把它们当成90个独立用户，也不能因Android会话结束就认定整台NPS可无影响重启。本轮因其他任务仍在线，没有重启正式或实验服务。恢复后还需分别核对正式UDP监听、认证网页展开命令、旧TCP线路恢复和真实KCP/QUIC客户端连接；菜单可见不等于客户端链路已验收。

官网[UDP隧道说明](https://d-jy.net/docs/nps/?lang=en#/guide/tunnels/udp)描述公网UDP端口映射到内网UDP服务，明确要求目标自身为UDP服务。当前正式App v1.30媒体入口为TLS/TCP，因此现有15556/15558保持TCP映射；不能只改隧道类型而不改客户端和服务端。NPC与NPS采用QUIC连接、UDP隧道任务和端到端媒体Datagram是三个独立层次，参见来源记录中的 `quic.Stream` 实现边界。

## KCP 的独立核对

KCP 与 QUIC 各自有启用条件，并非由同一个端口数字决定：

| 项目 | KCP | QUIC |
|---|---|---|
| 程序启用 | `kcp_enable` 默认真；端口非零；`bridge_type` 是 `kcp`、`udp` 或 `both` | `quic_enable` 默认真；端口非零；`bridge_type` 是 `quic`、`udp` 或 `both` |
| 默认网页显示 | `bridge_kcp_show` 缺省取 `ServerKcpEnable` | `bridge_quic_show` 缺省取 `ServerQuicEnable` |
| 当前正式配置 | `bridge_type=tcp`，因此运行和缺省网页显示都关闭 | 同样因 `bridge_type=tcp` 关闭 |
| 原检查时 UDP 监听者（迁移前） | 独立实验进程，UDP 8024 | 独立实验进程，UDP 8025 |

精确来源：[KCP/QUIC 运行开关，315–316 行](https://github.com/djylb/nps/blob/v0.34.7/cmd/nps/nps.go#L315-L316)、[KCP 显示，112–115 行](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/base.go#L112-L115)、[KCP 展开命令，144–146 行](https://github.com/djylb/nps/blob/v0.34.7/web/views/client/list.html#L144-L146)、[两个 UDP 监听的创建](https://github.com/djylb/nps/blob/v0.34.7/bridge/listener.go#L93-L129)。

## 保留兼容性的恢复方案（尚未执行）

1. 先完成正式和实验程序、配置、客户端/任务数据、网页资源、systemd 单元及相关监听/过滤配置的完整受限备份。备份包含秘密，只留在受限部署位置，不提交仓库。记录校验和与回滚所需的原服务状态。
2. 保护所有正式NPC在线连接，不仅是Android媒体会话。独立实验端口迁移现已完成，采用48024/48025/48026而非此前临时检查的18025/18026；旧UDP占用已解除，没有新启第二套服务。正式仍有在线NPC时保持运行，不推进需要重启的正式恢复。
3. 正式配置最小修改为 `bridge_type=both`，保留 `bridge_tcp_port=8024`、`bridge_tls_port=8025`、`bridge_kcp_port=8024`、`bridge_quic_port=8025` 和其他现有字段。上游先计算 KCP/QUIC 的启用条件，再在 `both` 时以 TCP 条件继续计算 TCP/TLS/WS/WSS，因此能保留旧 TCP/TLS NPC 兼容。不要改成 `quic` 或 `udp`，它们不会同时保留这里的 TCP/TLS 入口。参见[318–326 行](https://github.com/djylb/nps/blob/v0.34.7/cmd/nps/nps.go#L318-L326)。
4. 当前没有显式禁用 `tcp_enable`、`tls_enable`、`kcp_enable`、`quic_enable`；对应缺省值均为真。只改变 `bridge_type` 已足够，不需要增加未经验证的参数。保留既有账号、独立 NPC 身份、证书配置、鉴权规则和国内来源过滤；不把恢复协议入口当成放宽访问控制。
5. QUIC v0.34.7 识别的参数是 `quic_alpn`（缺省 `nps`）、`quic_keep_alive_period`（10 秒）、`quic_max_idle_timeout`（30 秒）、`quic_max_incoming_streams`（100000）。本次恢复不修改这些吞吐/超时参数，也不套用 master 新增字段。参见[连接初始化，76–80 行](https://github.com/djylb/nps/blob/v0.34.7/server/connection/connection.go#L76-L80)。KCP 原版会话参数在[SetUdpSession](https://github.com/djylb/nps/blob/v0.34.7/lib/conn/kcp.go#L12-L22)中固定，不能凭新增配置项改变它们。
6. 只有不存在会被中断的正式NPC且符合用户维护约束时，才可通过现有systemd管理正式恢复；不能为菜单自行重启在线服务。届时读回正式进程拥有TCP8024/8025和UDP8024/8025，检查bind错误、认证网页命令、所有原NPC与M1/M5入口及实际协议客户端。当前不具备这个条件，正式恢复尚未执行。

### 为什么不采用无重启恢复

当前两个 systemd 单元 `CanReload=no`，没有配置 `ExecReload`。本版 `lib/daemon/reload.go` 接收 SIGUSR1 后仅重新读取 Beego 配置；它不会重算 `ServerKcpEnable/ServerQuicEnable`、重建 `connection` 全局端口或执行 `Bridge.StartTunnel`，因此不能用它启动原来关闭的 UDP 监听。参见[配置读取函数](https://github.com/djylb/nps/blob/v0.34.7/lib/daemon/reload.go#L15-L26)和[启动时才计算开关的位置](https://github.com/djylb/nps/blob/v0.34.7/cmd/nps/nps.go#L302-L327)。

另外，官方此版 `nps reload` CLI 的实现写死 `kill -30`，而实际云端 Linux x86_64 的信号 30 是 SIGPWR，SIGUSR1 是 10。该命令不能作为本服务器可靠的热重载路径，本轮没有发送任何信号。参见[reload 命令实现](https://github.com/djylb/nps/blob/v0.34.7/lib/daemon/daemon.go#L48-L64)。不要为了避免一次维护重启，直接改页面显示条件或给正式进程发未经验证的信号。

客户端验收使用有明确用途的既有身份，不能并发启动同一生产 vkey 的影子 NPC 抢占路由。分别记录旧 TCP/TLS 保持工作、QUIC/KCP 登录握手和具体任务可用性；仍不宣称正式 App 实时媒体已经换成 UDP Datagram。
