# 腾讯云 NPS 程序来源核对

2026-10-02只读核对：目前正式 `nps.service` 与隔离 `nps-android-transport-test.service` 的实际运行程序均为 `/etc/nps/nps`，与 [djylb 官方 v0.34.7 发布包](https://github.com/djylb/nps/releases/tag/v0.34.7) 中的 Linux amd64 服务端逐字节一致。官方压缩包先核对 GitHub asset digest，再在内存中提取程序比较；不是只读取版本字符串。

| 核对项 | 结果 |
|---|---|
| 实际运行程序 SHA-256 | `9b9a36a2cae50c5e66f35c3450c9c4ddccc2ff04c839cb0df03592069c674b12` |
| 程序大小 | 25,006,242 字节 |
| 官方 tag commit | `5edda053f1579784cd6b91917bac1d59fe5824cf` |
| 正式、隔离实验程序与官方程序 | 三者摘要一致 |
| 官方静态网页、模板与云端对应文件 | 55 个全部一致，0 缺失、0 差异 |
| 本轮操作 | 只读，无服务、配置、过滤规则、程序替换 |

当前证据支持“正在运行官方原版，没有当前自改核心功能或页面资源”，不支持凭一个当前摘要证明所有历史时刻从未替换过程序。现有部署记录也未找到我们改动 NPS 核心源码的证据。

## 做过的外围调整

| 范围 | 记录中的变化 | 性质 |
|---|---|---|
| 系统调度 | 正式进程 `Nice=-5`、`CPUWeight=1000`，实时读回一致 | systemd 配置，不改 NPS 源码 |
| 性能采样 | 短时启用回环 pprof，采样后恢复 | 诊断配置与重启 |
| 双主机 | M1 公网15556与M5公网15558使用各自独立 NPC 身份 | 转发配置 |
| QUIC/KCP | 独立测试服务、测试 client 的 `ConfigConnAllow` 权限修正 | 使用上游内置能力与测试配置 |
| 入口恢复 | 撤销实验15556到15557的重定向 | 外围规则恢复 |
| Mac 出口 | 物理网卡绑定 relay、NPC 启动 wrapper | 项目外围脚本 |

当前正式15556/15558的保存映射仍为 TCP，各自目标是对应 NPC 主机的 `127.0.0.1:15556`。主配置的 `bridge_quic_port=8025`、`bridge_kcp_port=8024` 是上游能力的配置入口，不能据此称正式 App 媒体已经走 QUIC/KCP。新的认证 UDP 媒体候选是本项目独立网关，没有放进或替换 NPS 主程序。

本轮经既有物理网卡绑定连接进行 SSH，保留严格 host-key 验证；服务端读回连接来源属于家庭国内出口。没有改国内来源过滤或使用境外代理连接云端。受限脱敏核对详情在忽略目录 `docs/evidence/nps-provenance-20261002/`，不提交运行配置、凭据、原始日志。

## QUIC 界面与传输语义

随后只读核对上游v0.34.7与master：新增隧道的select是tcp/udp/mixProxy/secret/p2p任务类型；QUIC在客户端列表展开行的NPC连接命令里，由后端`quic_p`端口非空时显示。参见[v0.34.7隧道模板](https://github.com/djylb/nps/blob/v0.34.7/web/views/index/add.html#L21-L28)和[客户端模板](https://github.com/djylb/nps/blob/v0.34.7/web/views/client/list.html#L141-L157)。不能将某个任务菜单缺QUIC解释为NPS核心删除QUIC。

v0.34.7的UDP转发任务通过Bridge与`WrapFramed(net.Conn)`发送。QUIC连接包装的是可靠`quic.Stream`；P2P的QUIC分支也打开stream。master核对commit`259b7efafb7b0c20802261b7c9abbf5ffe29c148`保留相同关键语义；文本源文件审查未找到`SendDatagram`/`ReceiveDatagram`/`EnableDatagrams`或WireGuard集成。原版QUIC/P2P能力与“端到端按帧期限传递的不可靠媒体datagram”需要区分。

主要实现位置：[UDP任务](https://github.com/djylb/nps/blob/v0.34.7/server/proxy/udp.go#L131-L144)、[QUIC stream包装](https://github.com/djylb/nps/blob/v0.34.7/lib/conn/quic.go#L17-L44)、[P2P](https://github.com/djylb/nps/blob/v0.34.7/client/local.go#L178-L184)。本次只讨论NPS二开与WireGuard集成可行性，没有创建新NPS仓库、fork、改动这些源文件或部署新协议。

评估路线：先比较本项目实际UDP媒体经Tailscale direct以及国内UDP Peer Relay的表现，保留NPS现有公网入口。Tailscale Peer Relay需要参与客户端至少1.86及策略支持；刚读回一加12仍为1.82.4，尚不能将该功能作为已部署线路。今日Headscale部署记录为0.29.4；仍须对具体策略、Mac/手机实现做兼容和实际路径验收。只有为了朋友免额外VPN客户端等明确产品需要，再考虑在独立候选中实现媒体datagram中继或有范围NPS二开。
