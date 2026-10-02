# Headscale 统一使用 yilufa 的部署核对

核对日期：2026-10-02。当前唯一启用的 Headscale 控制地址为 **https://hs.yilufa.site**。

| 主机 | 当前有效客户端 | 尾网 IPv4 | Android 服务入口 | 实际核对 |
| --- | --- | --- | --- | --- |
| M1 | Homebrew `tailscaled`，显式 socket `/var/run/tailscaled.socket` | `100.65.0.2` | `100.65.0.2:15556` | 运行正常、无健康告警；删除旧 GUI 配置后受信 TLS `/ping` 为 200，返回 M1、`emulator-5556`、VideoToolbox |
| M5 | Tailscale GUI 系统扩展 | `100.65.0.11` | `100.65.0.11:15556` | 运行正常、无健康告警；通过新尾网受信 TLS `/ping` 为 200，返回 `ok=true` |
| OnePlus 12 | Android Tailscale 1.82.4，现有 Headscale 用户 `wyw` | `100.65.0.3` | 测试客户端 `oneplus12-test` | 原生 UI 为 Connected，云端节点在线；M1 同家 Wi-Fi 的 UDP 直连握手为 8 ms |
| 腾讯云 2 号 | Docker Headscale 0.29.4 | 公网 `146.56.249.175` | `https://hs.yilufa.site` | 受信 HTTPS `/health` 为 200；当前反向代理和 Headscale 配置只有新控制域名 |

M1 原先同时运行两个独立客户端。旧 GUI 客户端的控制面已无法正常同步，但还保留旧节点和旧地址。已通过 Tailscale LocalAPI 精确删除该本机旧 profile；最终 GUI profile 列表为空、`WantRunning=false`、`BackendState=NeedsLogin`，没有尾网 IP。M1 新 Homebrew 节点的身份和地址保留。

M5 原先已运行新尾网，同时还保留一个未选中的旧 profile。已只删除这个旧 profile；新 profile 保持运行，尾网 IP 和转发设置不变。最终 M5 的可选 profile 列表只包含新控制地址。

M1 新尾网的 Serve 原先还指向过时的局域网地址。已将 15556 端口准确调整为 `127.0.0.1:15556`，调整前配置留在本次受限实验记录中以便核对。M5 的 Serve 本来就指向这个本机地址。这里是现有正式 TLS/TCP 服务的尾网入口，不能作为新媒体 UDP 协议的性能证明。

App 的 M1 尾网快捷连接应使用 `100.65.0.2:15556`。旧尾网地址只能参与一次设置迁移和已有密码兼容匹配，不能继续作为快捷连接线路或推荐入口。原有公网 M1 `146.56.249.175:15556` 与公网 M5 `146.56.249.175:15558` 是独立的 NPS 入口，继续保留供用户手选。

OnePlus 12 的后续核对已完成。通过 Tailscale 的 Accounts → Use an alternate server 实际设置 `https://hs.yilufa.site`，使用云端已有用户 `wyw` 注册节点 48，尾网地址为 `100.65.0.3`，仅将这个新节点的名称改为 `oneplus12-test`。普通 Tailscale 账户没有执行退出或删除操作。最终 Accounts 界面显示新域名，主页面明确为 Connected，手机 `tun0` 读回新地址，云端节点为在线。

手机最初的未加密首选项没有可读控制域名，不能据此推断加密状态；最终判断采用上述真实 UI、接口 IP、云端节点和实际握手证据。M1 到手机的 Tailscale ping 通过 `192.168.9.149:35621` 直接 UDP 返回 8 ms；这是同家 Wi-Fi 的穿透和连通性核对，还没有验证此路线的视频帧率、移动网络或异地 V50 效果。

云端当前配置中没有旧控制域名，不需要重启或重建 Headscale。对 NPM 数据库包括停用、已删除的反代行精确检查，旧控制 hostname 匹配数也是 0。南京国内 DERP 配置继续使用同一腾讯云 IPv4。此次没有删除云端已有设备节点、修改 NPS/国内过滤规则或修改 Clash 配置。手机注册的一次性 Auth ID 只放在权限 600 的临时文件中，经既有 SSH 发送到同一 Headscale 服务，完成后删除；没有收集账号口令、设备节点私钥或持久授权 key。

现有中文输入法没有正确接受 ADB 的 URL 字符输入，因此使用一个独立的临时 Android Instrumentation 小助手，限定只对 Tailscale 当前公开服务器 URL 输入框执行 `ACTION_SET_TEXT`。完成后助手包、手机临时 APK/JAR/XML 和注册 token 文件均已清理；正式客户端、诊断客户端和手机的 CPU 限频没有变更，输入法也没有切换。

首次 SSH 读取 M5 曾超时，不能据此判断机器关机。最终 `.99` 和 `.126` 的 TCP 22 都可达；使用明确的已有 SSH 私钥、主机别名校验和关闭 agent 自动选择后，经 `.99` 成功完成核对与精确清理。

证据目录为 `docs/evidence/yilufa-migration-20261002/`，只保存脱敏后的域名、状态、地址、Serve 目标与健康结果。源代码初始扫描没有旧控制域名引用；本次部署说明同样不将旧域名写成可用地址。

删除操作核对了 [Tailscale 官方 LocalAPI 的 DeleteProfile 实现](https://raw.githubusercontent.com/tailscale/tailscale/v1.96.4/client/local/local.go)：精确删除本机 profile，删除当前 profile 时切换为空 profile，不删除云端用户。`switch remove` 曾返回退出码 0 但没有删除 profile，因此最终结论以 LocalAPI 删除后的列表和实际服务读取结果为准。
