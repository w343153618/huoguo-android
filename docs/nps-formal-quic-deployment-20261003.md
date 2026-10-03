# 正式 NPS 恢复 KCP／QUIC 与双 Mac NPC 迁移

2026-10-03 中午，用户明确要求直接重启正式 NPS、恢复 KCP 和 QUIC，并把 M1、M5 的 NPC 都改为基于 UDP 的连接。此次明确指令覆盖此前对这一次维护的重启限制。维护已完成；不构成后续实验可重复重启在线正式服务的授权。

## 运行结果

| 项目 | 当前实际运行 | 验证 |
|---|---|---|
| 正式 NPS | djylb v0.34.7，`bridge_type=both` | systemd active；正式 PID3412973 |
| KCP | UDP8024 | 正式进程监听；既有 M1 身份完成真实 KCP 认证后回到 QUIC |
| QUIC | UDP8025 | 正式进程监听；M1、M5 独立身份上线 |
| 其他 NPC 的旧入口 | TCP8024、TLS/TCP8025 | 保留兼容；重启前38个在线客户端全部重新上线，缺失0 |
| NPS 网页 | KCP、QUIC 展开命令 | 既有管理员认证后的实际渲染均包含对应命令 |
| M1 | QUIC，回环UDP48126→云UDP8025；物理en7，源192.168.9.128 | 原client1466保留；公网15556受信HTTPS `/ping` 200 |
| M5 | QUIC，回环UDP48126→云UDP8025；物理en11，源192.168.9.99 | 原client1468保留；公网15558受信HTTPS `/ping` 200 |

正式程序SHA-256仍为 `9b9a36a2cae50c5e66f35c3450c9c4ddccc2ff04c839cb0df03592069c674b12`，没有修改、编译或替换 NPS 主程序。配置仅改变 `bridge_type` 一项，原客户端身份和任务持久化记录完整读回未变，`geo_in` 与 `geo_fwd` 的配置摘要前后相同。没有放宽国内来源过滤。

独立实验实例继续使用TCP48024、KCP/UDP48025、QUIC/UDP48026及任务48028，保留原 disabled 开机策略，未新建第二个实例。正式恢复使用已释放的UDP8024/8025。历史[缺项审计](nps-quic-visibility-20261003.md)和[高端口迁移](nps-high-port-migration-20261003.md)保留原观察时间及状态。

## 物理出口与分层验证

两台 Mac 的正式 UDP relay 只绑定 `127.0.0.1:48126`，固定目的地 `146.56.249.175:8025`。上游 socket 使用 macOS `IP_BOUND_IF` 绑定允许的 `en` 物理网卡并读回绑定值；M1优先en7、备用en0，M5优先en11、备用en0。无允许的物理路径时明确拒绝，不尝试 unbound/TUN/境外代理回退。保留原有断链重连和有线稳定15秒后恢复优先的机制，本次没有实际拔网线验收切换。

云端实际读到两个客户端来源均为 `180.111.118.161`，属于该主机现有 `cn4` 集合。同步发起六次受信证书的公网健康读取期间，云端12秒有界 UDP8025 header capture 得到107个包，其中49个入站，107个均涉及该家庭源地址。此为正式 QUIC bridge 的实际 UDP 包头证据，未采集媒体内容。

初次8秒抓包在请求发生前结束，捕获0包；随后将 capture 与六个请求放在同一顺序工具脚本中，得到上述实际数据。初次零包记录保留，不能当作路由失败。第一次重复健康读取脚本在读到HTTP后错误地于EOF之后取证书，报 `handshake not done yet`；修正为握手后立即读取证书后，两端各三次全部200。该修正是测试器错误，不是服务故障。

这六次小请求包含建立TCP、TLS及HTTP的时间（约95–147ms），不是画面、触控或音画延时，也没有与原TCP bridge做同参数性能对照。当前证据证明协议、国内来源、身份和公网转发入口可用，不能证明已经解决视频卡顿或得到稳定60/120FPS。

KCP认证使用原M1身份单独完成：先退出M1正式NPC，临时高位回环48125→正式云UDP8024，收到官方 `Successful connection with server`，NPC自然退出0，测试relay以SIGINT结束（-2），然后恢复原M1 QUIC launcher。没有创建、复制或并行运行正式vkey影子客户端，没有更改M5身份。

## 哪一层已经改为 UDP

已修改的是 **Mac NPC ↔ 腾讯 NPS 的 bridge**。djylb此版 QUIC 用可靠的 `quic.Stream` 承载转发，它底层为 UDP，但仍提供可靠有序字节流。

正式 App v1.30 自身依然以 TLS/TCP 连接公网15556/15558，Mac网关本身也仍是TCP服务，因此公网任务继续保留 `mode=tcp`。不能只把任务标签改成 UDP：现有 App 和网关不会因此成为UDP服务，反而会无法连接。独立认证 UDP Datagram 的视频、音频、原生多指入口继续在实验分支研发，不能把此次维护称为该产品已发布。

手机原有两个公网地址和已安装 App 不需要改变。M5仍给火锅日常使用，M1用于机主实验；两个模拟器数据、分辨率、账号、Headscale节点和运行gateway未重启或修改。

## 修改前备份与回滚

新云备份：`/root/nps-backups/formal-quic-20261003-120815/`。归档保存完整 `/etc/nps`、systemd单元及drop-ins和NPM对应站点，69个普通文件逐一从归档读取验证。归档SHA-256：`73094082494b60be3a58ebf9601b2df0d1418bdffa37ae0732aa0a148eb7ba6f`。另外保存防火墙参考，不能将其直接全量覆盖其他服务。

M1配置、LaunchAgents及旧runtime源码备份：`~/.config/huoguo-android/backups/formal-quic-20261003-120257/`。

M5对应备份：`~/Library/Application Support/AndroidRemote/direct/npc-host/backups/formal-quic-20261003-120257/`。新M5运行源码在该npc-host目录的 `formal-quic/` 子目录；旧TCP源码留存。

如需回退，分别停止对应NPC及relay，恢复备份的NPC配置与两个LaunchAgent，再启动旧relay和NPC。新wrapper仍只允许精确旧TCP回环18024或新QUIC回环48126两组配对，不能任意外连。正式云恢复旧配置涉及再次重启全部NPC，须符合届时的明确维护授权；回退单台Mac的NPC为TCP不要求重启已启用both的云服务。

正式部署源码为 [NPC wrapper](../scripts/run_m1_npc.py) 与 [QUIC relay入口](../scripts/run_formal_quic_relay.py)，固定白名单/0600配置/秘密仅环境/失败消息脱敏等21项离线检查通过。实际部署两台wrapperSHA为 `58aa701e1e29712aea871d4b45241683d9bb28a8b9a2a6e7f0c8e6df80fba86f`，relay入口SHA为 `2f042a564d84180d9bc4a094bb29cc73114c5f94cb057afb641a1d76e7a8e7fb`。

备份和原始receipt/log均留在受限部署位置或忽略的 `docs/evidence/nps-formal-quic-20261003/`；没有将凭据、NPC vkey、会话cookie、运行配置全文、APKs或原始包头日志提交仓库。
