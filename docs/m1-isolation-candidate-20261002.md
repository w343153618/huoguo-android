# M1 隔离候选：出口、文件策略与管理员 staging

日期：2026-10-02。本轮在正式网关会话 owner 加固之后，准备独立宿主身份与受限出口。**当前是源码与自有合成 fixture 验证；没有安装或切换隔离后的真实模拟器。正式 v1.30 仍是原 TLS/TCP 媒体路径。**

App 账号 `huoguo` 供火锅使用，`wyw` 只供机主测试。拟建的三个 macOS 服务身份是运行权限边界，没有新增 App 登录凭据：

- `_huoguo_vm`：只持有自己的 AVD、缓存和日志，读取只读 SDK。
- `_huoguo_media`：固定实例的画面、声音与原生触控管理，后续需单独验证文件与接口权限。
- `_huoguo_egress`：独立 Web／DNS 出口，没有个人 Home、管理员组或交互登录权限。

管理员持有的候选根目录拟为 `/private/var/lib/huoguo-android-isolation`。不迁移个人 Home、SSH、浏览器或 Keychain；不覆盖旧 runtime、AVD、NPC 身份或正式公开入口。

## 本轮代码与可重跑验证

| 组件 | 源码 | 范围检查 |
| --- | --- | --- |
| Web 出口 | [restricted_egress.py](../restricted_egress.py) | 28 项；自有双族回环 TCP／SOCKS fixtures，无公网连接 |
| DNS 出口 | [restricted_dns.py](../restricted_dns.py) | 25 项；自有双族回环 UDP fixtures，固定上游在测试内替换，无真实公共 DNS 请求 |
| 实验性 Seatbelt 生成器 | [emulator_sandbox_profile.py](../scripts/security/emulator_sandbox_profile.py) | 16 项；包含 Darwin 无害子进程文件／socket canary，无 emulator |
| 管理员 staging | [isolation_admin.py](../scripts/security/isolation_admin.py) | 26 项；所有系统账号、ownership、cp／lsof 操作 mock，只在新临时目录写 fixture |
| 固定安装包准备 | [prepare_isolation_candidate.py](../scripts/security/prepare_isolation_candidate.py) | 16 项；私有快照、SHA／文件边界、失败收尾与引用检查，不执行系统安装 |

上述合计 **111 项全部通过（4.265 秒）**。实际系统 Python 3.9.6 另已完成 prepare-only 与 loader／helper 编译，19个顶层模块、2个helper和profile精确SHA验证通过。证据目录为 `evidence/m1-host-isolation-20261002/`，原始敏感日志、凭据及 AVD 不进入 Git。

```sh
python3 -m unittest \
  tests.test_restricted_egress tests.test_restricted_dns \
  tests.test_emulator_sandbox_profile tests.test_isolation_admin \
  tests.test_prepare_isolation_candidate -q
```

## Web 与 DNS 出口的具体边界

Web 出口只绑定 `127.0.0.1` 和 `::1` 的同一个端口。HTTPS 使用 CONNECT，目标只允许经过全量地址检查的公网单播 80／443；明文 HTTP 只允许无正文 GET／HEAD 的 absolute-form 请求。实际拨号使用已经检查的数字地址；上游 Clash SOCKS 也只收到数字 ATYP 1／4，不再有一次未经检查的目标解析。内网、回环、CGNAT／Tailnet、fake IP、组播、已知 IPv6 转换地址和登记的宿主地址均被拒绝。默认并发 8、头部 8 KiB、每向队列 64 KiB、连接总时长 300 秒；不记录域名、载荷或请求凭据。

DNS 出口需要管理员预绑定 `127.0.0.1:53` 和 `[::1]:53`，随后完全降至服务 UID/GID 再接收查询。生产入口拒绝缺少任一地址族、全接口监听、错 family／端口或 IPv6 V6ONLY 未启用。固定向 `223.5.5.5:53` 发出有界请求，并用 Darwin `IP_BOUND_IF` 绑定安装器指定的 `enN` 物理接口。客户端不能指定上游、端口、TCP 回退或任意转发任务。

DNS 只接受受限的单问题查询，响应检查 source、随机替换 ID、问题与记录边界，拒绝私网地址及 SVCB／HTTPS 内网 hints。默认 4 worker、8 pending、64 请求／秒，最多两次各 1 秒的尝试。最大包 1232 bytes；DNSSEC、复杂 EDNS、超大／截断回复等兼容性仍待真实安卓检查。

Web 入口不是万能流量过滤：CONNECT 内部数据不透明，公网 80／443 不代表只能浏览视频。宿主的公网地址、可能回流到宿主的公网入口、特殊 NAT／路由必须另外登记或外层阻断。此模块也不能代替阻断安卓直接绕过出口。

## 两个实验中发现的实际绕过点

1. SDK 37.1.11 的 HTTP 代理初始化失败后，emulator 可能继续启动并忽略代理。因此不能只依赖 `-http-proxy`。拟部署沙箱禁止直接外联，仅例外到独立 Web 与 DNS 回环端口；出口失败时不得放行直连。
2. 本机 SBPL 的 `localhost:PORT` 同时包含 IPv4 与 IPv6。无害子进程确实可连接自有的 `::1` 同端口 canary，说明只绑定 IPv4 会留下另一地址族入口。两个出口现均要求双族独占，任一绑定失败就拒绝启动。IPv4-mapped loopback 命中对应 IPv4 listener；本机未配置的 `127.0.0.2` canary 无法 bind（Errno 49），这不能证明将来添加的所有 loopback 别名都被拒绝。真实候选还要验证监听 UID、存活、接口别名和规则生命周期。

## 文件与管理员 staging

Seatbelt 候选只读指定 SDK／代码与明确系统依赖，只写自己的 AVD／缓存。明确排除 `/Users`、其他卷、网络挂载及 Data 卷别名，不整体放行 `/System`、Homebrew 或个人 Home。出站只例外固定回环 Web TCP 和 DNS UDP，管理监听逐项列出 console／ADB／gRPC；不例外共享 ADB 5037。宿主麦克风输入和真实摄像头在克隆配置中关闭。

该 runner 是本机已 deprecated 的 `sandbox-exec`，采用 `allow default` 后明确约束文件与 socket。其余 Mach／IOKit／process 能力、GPU／Hypervisor 兼容与子进程边界尚未验收，不能称为已完成的正式 App Sandbox 或零逃逸保障。

管理员 helper 首次 staging 遇到已有未知用户／组或非空候选目录会拒绝收编／覆盖。创建后读回 UID/GID、Home、非交互 shell 与管理员组资格；阶段失败保留 root-owned journal，不自动重试覆盖部分状态。特权文件读取／创建逐级用 directory FD + `O_NOFOLLOW`，普通文件检查大小和单硬链接；新文件 `O_EXCL`，避免末级或父目录符号链接越界。SDK 只接受树内相对链接；可写 AVD 拒绝链接、FIFO 与非普通对象。

准备工具只生成新的0700 bundle，快照文件0600。外层 `/usr/bin/python3 -I -S` 先核对loader SHA，再核对全部固定叶名、manifest、helper和profile。将已经验证的bytes放进root私有临时目录后才provision，不从仍可修改的checkout再次导入特权代码。成功记profile SHA，失败记journal；不自动启动服务。可审阅的具体stage-only包为 `evidence/m1-host-isolation-20261002/stage-only-bundle-20261002-rootloader2/`；rootloader1为旧证据，不可执行。命令SHA为 `9d28159aef98648b12453ef1adbf473c1d26fb9f12559d989e45f04d1c0c5710`。

冷拷贝前要求候选 UID 没有进程，原实例 launcher 在复制全程受控停用。helper 在复制前后检查源 AVD 无打开文件及文件元数据未变化；发现变化不接受克隆。删除的仅是候选派生 launch／hardware／lock 元数据，原 AVD 保留。未知绝对路径或 `..` 配置路径会被拒绝，初始候选保持 6 核、16 GiB、1080×1920。检查本身不构成原子源锁，不能省去 caller 对 launcher 与正式会话的控制。

## 当前现场与后续验收

本轮读回正式 15556 无已建立连接、原 AVD 存在，原 `auth.json` 权限 0600。管理员认证进程仍在等待本机系统密码，`sudo -n` 没有可用认证；候选根目录未安装。未停止正式 emulator／gateway，未变更 PF、Clash、NPS／NPC、云端国内来源过滤、App 账号或版本。

本轮候选源码只推送 `codex/experimental-udp`；正式 `main` 与v1.30保持原提交。源码推送不构成新APK发布或隔离验收。

完成系统认证后先只 stage 新身份与只读 SDK／代码／策略；独立 AVD 克隆、启动与推广是后续步骤。真实验收必须依次覆盖：

1. 自有文件 canary 与 guest root 的回环／自机 LAN／IPv6 canary；改变 guest proxy、DNS 或 iptables 不能绕过。
2. 两族出口监听身份与死亡处理；禁止原始 ADB／SSH／Clash 访问；账号间会话 owner 边界保持。
3. 原签名 SDK 的 Hypervisor／Metal 和苹果编码实际启用，不能静默退回个人 UID 或软件路径。
4. 保留一加 12 的降频限制，用真实视频检验接收与独立呈现 FPS、长尾、触控与音画同步，推荐缓冲不超过80ms。
5. NPS／新媒体中继走既有国内物理出口，guest YouTube 的 Clash 出口分别验证；再验证指定公网和 Tailnet direct／relay。
6. 重启、网卡变化、TUN 共存后仍 fail closed。有人使用正式实例时跳过打断测试。

上述没有通过前，不能公布“宿主／LAN 已隔离”、新公网 UDP 产品完成或真实手机流畅度提升。链路认证、完整性与防重放继续保留；它们与 UDP 媒体并不冲突。
