# M1 Tailscale 节点保留与测试环境分工

核对日期：2026-10-03。用户要求M1与M5都保留，方便后续测试；唯一控制服务仍为`https://hs.yilufa.site`。

| 主机 | 现有节点ID | 云端名称 | Tailnet IPv4 | 本轮实际状态 |
| --- | --- | --- | --- | --- |
| M1 | 46 | `macbook-m1-64` | `100.65.0.2` | 云端与本机均在线，后台Running，内核utun0持有此地址，Health为空 |
| M5 | 23 | `macbook-m5-128` | `100.65.0.11` | 云端在线，独立节点保留，实际discovery ping通过 |
| 一加12 | 48 | `oneplus12-test` | `100.65.0.3` | 云端在线，M1实际discovery ping为直接端点；本轮未测媒体 |

## M1 没有被删除

M1有效客户端是Homebrew后台`tailscaled`，显式管理socket为`/var/run/tailscaled.socket`。原有效节点名为`m1-max-test`；此次仅用`tailscale set --hostname=Macbook-m1-64`修改该客户端名称，再将云端现有节点46的given name改为`macbook-m1-64`。没有删除、重新注册节点或更换尾网地址。M5现有节点23保留。

之前删除的是M1旧线路的GUI profile。当前图形App仍安装，但其独立旧profile为空、显示NeedsLogin；它不代表Homebrew后台未连接。不要为了让这个GUI显示Connected而再注册第二个M1、抢占路由或替换正在工作的100.65.0.2身份。当前以显式socket的状态和云端节点为准。

现有系统LaunchDaemon `homebrew.mxcl.tailscale`配置为RunAtLoad与KeepAlive；本轮实际root后台正在运行。没有为验证开机行为而重启Mac，因此此处只确认配置和当前进程，不宣称本轮完成重启验收。

```sh
/opt/homebrew/bin/tailscale --socket=/var/run/tailscaled.socket status
/opt/homebrew/bin/tailscale --socket=/var/run/tailscaled.socket ip -4
```

`https://100.65.0.2:15556/ping`实际返回HTTP200、`node=M1`、`serial=emulator-5556`、`video_backend=videotoolbox`。使用App内固定服务端证书建立受信TLS；此证书固定验证关闭了名称匹配，没有关闭证书链验证。它只证明该现有TLS/TCP入口可达，不证明真实视频FPS、物理触控延时或新UDP产品验收。

M1到一加12的三次Tailscale discovery ping经`192.168.9.149:35621`返回64、78、45ms；到M5两次经`192.168.9.1:41644`返回2ms。这是少量同家网络连通性采样，不能代替媒体长尾、异地、手机流量或V50测试。去一加12的内层内核路由为utun0；去腾讯云146.56.249.175的当前路由为en7／192.168.9.1，仅为路由读回，不新增出口国家验收。

脱敏证据为`evidence/tailscale-m1-retained-20261003/retained-node-readback.json`，不含节点密钥、登录凭据或原始敏感日志。

改名后进一步实际执行现有`TailnetScope`的只读准入核验：控制URL、M1节点46和一加12节点48的身份、WhoIs、utun0地址及去手机的内核路由均通过。源码没有按旧显示名称固定身份，所以这次改名无需削弱或修改准入检查。证据为`evidence/tailscale-m1-retained-20261003/tailnet-scope-readback.json`；未创建UDP会话，不能作为Tailnet媒体验收。

## 日常测试与正式部署分别验收

用户允许日常机主性能实验暂用原有UID501环境。现有认证UDP worker本来就使用`emulator-5556`／`RemoteAndroid17Compare`，没有以隔离候选的通过状态作为运行条件，因此这次无需削弱媒体代码或撤销候选策略。候选的ADB offline留给独立兼容性排查，不阻挡机主有界LAN／已登记Tailnet媒体实验。

此类实验记录必须写明非隔离环境；朋友／公众新版部署仍要求完成宿主文件、局域网、代理绕过和权限生命周期验收。两类路径均保留认证、完整性、防重放、取消与会话收尾、正式在线会话保护，以及国内云端物理出口规则。隔离候选保留，正式v1.30及原有NPS身份不因这个测试许可而迁移或重建。

既有“安卓串流下一版优化”自动实验安排也已加入上述最新分工和节点保留规则；原周期、当前对话目标及仅在有意义变化时通知的设置保留。
