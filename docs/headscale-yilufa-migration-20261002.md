# Headscale 统一使用 yilufa 的部署核对

核对日期：2026-10-02。当前唯一启用的 Headscale 控制地址为 **https://hs.yilufa.site**。

| 主机 | 当前有效客户端 | 尾网 IPv4 | Android 服务入口 | 实际核对 |
| --- | --- | --- | --- | --- |
| M1 | Homebrew `tailscaled`，显式 socket `/var/run/tailscaled.socket` | `100.65.0.2` | `100.65.0.2:15556` | 运行正常、无健康告警；删除旧 GUI 配置后受信 TLS `/ping` 为 200，返回 M1、`emulator-5556`、VideoToolbox |
| M5 | Tailscale GUI 系统扩展 | `100.65.0.11` | `100.65.0.11:15556` | 运行正常、无健康告警；通过新尾网受信 TLS `/ping` 为 200，返回 `ok=true` |
| 腾讯云 2 号 | Docker Headscale 0.29.4 | 公网 `146.56.249.175` | `https://hs.yilufa.site` | 受信 HTTPS `/health` 为 200；当前反向代理和 Headscale 配置只有新控制域名 |

M1 原先同时运行两个独立客户端。旧 GUI 客户端的控制面已无法正常同步，但还保留旧节点和旧地址。已通过 Tailscale LocalAPI 精确删除该本机旧 profile；最终 GUI profile 列表为空、`WantRunning=false`、`BackendState=NeedsLogin`，没有尾网 IP。M1 新 Homebrew 节点的身份和地址保留。

M5 原先已运行新尾网，同时还保留一个未选中的旧 profile。已只删除这个旧 profile；新 profile 保持运行，尾网 IP 和转发设置不变。最终 M5 的可选 profile 列表只包含新控制地址。

M1 新尾网的 Serve 原先还指向过时的局域网地址。已将 15556 端口准确调整为 `127.0.0.1:15556`，调整前配置留在本次受限实验记录中以便核对。M5 的 Serve 本来就指向这个本机地址。这里是现有正式 TLS/TCP 服务的尾网入口，不能作为新媒体 UDP 协议的性能证明。

App 的 M1 尾网快捷连接应使用 `100.65.0.2:15556`。旧尾网地址只能参与一次设置迁移和已有密码兼容匹配，不能继续作为快捷连接线路或推荐入口。原有公网 M1 `146.56.249.175:15556` 与公网 M5 `146.56.249.175:15558` 是独立的 NPS 入口，继续保留供用户手选。

当前手机 OnePlus 12 已安装 Tailscale，最初没有运行中的隧道。实际打开 App 后看到普通 Tailscale 账户的“Not connected”，随后 App 自行转入登录浏览器；没有完成登录或注册新尾网。未加密首选项里没有可读的控制域名，但这不能证明加密首选项中不存在旧自定义服务器。手机的旧服务器清理仍需后续专门核对，不能把 Mac 清理结果扩大为手机也已完成。

云端当前配置中没有旧控制域名，不需要重启或重建 Headscale。南京国内 DERP 配置继续使用同一腾讯云 IPv4。此次没有删除云端设备节点、修改 NPS/国内过滤规则、修改 Clash 配置，也没有写入或收集账号口令、设备节点私钥或授权 key。

首次 SSH 读取 M5 曾超时，不能据此判断机器关机。最终 `.99` 和 `.126` 的 TCP 22 都可达；使用明确的已有 SSH 私钥、主机别名校验和关闭 agent 自动选择后，经 `.99` 成功完成核对与精确清理。

证据目录为 `docs/evidence/yilufa-migration-20261002/`，只保存脱敏后的域名、状态、地址、Serve 目标与健康结果。源代码初始扫描没有旧控制域名引用；本次部署说明同样不将旧域名写成可用地址。

删除操作核对了 [Tailscale 官方 LocalAPI 的 DeleteProfile 实现](https://raw.githubusercontent.com/tailscale/tailscale/v1.96.4/client/local/local.go)：精确删除本机 profile，删除当前 profile 时切换为空 profile，不删除云端用户。`switch remove` 曾返回退出码 0 但没有删除 profile，因此最终结论以 LocalAPI 删除后的列表和实际服务读取结果为准。
