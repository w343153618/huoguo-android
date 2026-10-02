# 发布通道只读审计：2026-10-02

> **历史快照，不是当前操作指南。** 本文是准备 v1.29 时的审计记录，保留当时的观测。当前以 [v1.30 发布记录](release-1.30-delivery-20261002.md)、[yilufa 线路迁移记录](headscale-yilufa-migration-20261002.md)和 [M1 部署状态](m1-deployment.md)为准。下文旧 Tailnet 地址仅作为历史证据，不可作为操作入口；当前 M1 尾网入口为 `100.65.0.2:15556`，Headscale 仅使用 `https://hs.yilufa.site`。

本文件记录本次发布准备阶段的只读快照，采集截至北京时间 2026-10-02 08:36。它记录的是 root 执行切换之前的状态；后续切换、发布和客户端验收须以其独立证据为准。审计 agent 未发布版本、修改 NPS、停止服务或修改签名材料。

后续用户明确要求同时保留 M1 与 M5 两条入口，由 App 手动选择。因此下文停用 M5 重复身份属于临时消除冲突，不是永久移除 M5 服务。新的部署方案采用两个独立 NPC 身份及两个公网端口，见 [双主机方案](dual-host-nps-plan-20261002.md)。

## GitHub 与升级分支

| 项目 | 审计快照 |
|---|---|
| 仓库 | `w343153618/huoguo-android`，private |
| 最新正式 Release | `v1.28`，版本码 29，2026-09-30T15:23:16Z 发布 |
| main | `9987186ac02f8b5c8157d7db33db61d4d859ba64` |
| updates | `f5482e605d8cdff89269c2088d073977f98ae643` |
| 本地待发布源码 | `app/build.gradle`：`1.29`，版本码 30；工作树含既有未提交改动 |
| 发布 APK | 3,011,282 字节；SHA-256 `6438a493b009ca1f0ed86fe733810b18365405e78ee5e734aebcba04f4d5f12d` |

GitHub Release URL：<https://github.com/w343153618/huoguo-android/releases/tag/v1.28>。本地更新清单、GitHub `updates` 分支和公网更新清单当时均为上述 1.28 元数据。

`scripts/publish_release.py` 要求从干净的 `main` 发布，构建后验证原签名、APK 摘要、尺寸、版本和固定下载 URL，再发布私有 Release，最后更新独立的 `updates` 分支。该分支会被强制替换，发布前应单独记录其原 SHA。已发布版本不可重打；若 Release 已成功而更新分支推送中断，可使用 `--resume` 交付已发布原包。

## 签名身份

原发布 APK 的签名证书 SHA-256：

`0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`

本机 `~/.android/debug.keystore` 存在且可读取；只导出公证书进行比较，其 SHA-256 与原发布 APK 和发布脚本固定期望完全相同。本次没有生成新签名，也没有打印或复制私钥。审计会话中四项 `ANDROID_*` 签名环境变量均未设置；这不会证明签名材料丢失，因为既有 Gradle 的默认本机签名刚好使用上述原身份。发布前仍应验证候选 APK 实际签名，而不能只依赖文件存在。

`apksigner` 初次调用缺少 Java runtime 而失败；指定既有 Java 21 后完成验证。该工具错误不属于 APK 签名错误。

## M1 本地运行状态

外部状态目录仍为 `~/Documents/ChatGPT/others/android-remote/m1-compare/`。正式运行代码指向本工程，未删除旧状态目录。

| 服务 label | 运行路径或用途 | 审计状态 |
|---|---|---|
| `local.remoteandroid.m1compare.gateway` | 当前工程 `gateway.py` | PID 40941；15556 LISTEN；无活动 TCP 串流连接 |
| `local.remoteandroid.download` | 外部状态目录的 `download`，8089 | PID 17603；HTTP 200 |
| `local.huoguo.m1.updates` | 当前工程 `scripts/pull_updates.py` | 300 秒轮询；上次退出 0 |
| `local.huoguo.m1.npc` | 当前工程 `scripts/run_m1_npc.py` | PID 25425；连接本机 18024 |
| `local.huoguo.m1.npc-relay` | 当前工程 `npc_physical_relay.py` | PID 18978；连接物理 en7 上的 `192.168.9.128` 到云 8024 |

回环 `https://127.0.0.1:15556/ping` 返回 M1、`emulator-5556` 和 `videotoolbox`。本地更新清单为 1.28；下载目录包含 `AndroidDirect-v1.28.apk`、`AndroidDirect.apk` 和三个 HTML 书签入口。

M1 更新任务使用现有 GitHub 登录认证模式，而不是默认 SSH 部署钥匙模式；默认部署钥匙文件不存在，不代表当前轮询故障。不要为了此缺失生成新授权或切换认证。

M1 `diagnostics_reports.py` 修改时间为 2026-09-30T18:36:37Z，gateway 进程启动时间为 2026-10-01 18:41:19 北京时间，源码已包含新增的诊断字段白名单。后续再修改该导入模块，必须在无会话时重新启动 gateway 才能装载；源文件最新不等于已运行模块最新。`hardware_stream.py` 此后还有修改，应同样在候选检查完成后安排一次有范围的无会话重启。

## 公网入口的实际问题

审计发现公网入口与 M1 回环并不一致，且不是 `gateway.py` 故意隐藏公网诊断元数据：当前 `/ping` 代码没有按来源分支。

| 入口 | TLS 公证书 SHA-256 | ping |
|---|---|---|
| M1 回环 | `17bdaea4cb05b89cf8d64af02b8ed445bdb731f10923da7505d2aec91109c3c0` | M1 / 5556 / videotoolbox |
| `146.56.249.175:15556` | `15d5e6eb9e8ad36c6e06f94f4db872619d6745e253759a5b46701bb48957139e` | 仅 `ok:true` |
| M5 `192.168.9.126:15556` | 同公网的 `15d5e6…139e` | 仅 `ok:true` |

直接套接字强制 `IP_BOUND_IF=en7` 访问公网后，仍得到上述 M5 公证书与响应，排除了仅仅是应用代理环境变量导致的差异。公网建立 TLS 时，M1 NPC 和 gateway 没有出现相应本地目标连接。

云服务器 15556 由正式 NPS PID 2242499 监听；`PREROUTING` 没有此前的 15556 重定向。任务 1409 是 TCP、目标 `127.0.0.1:15556`、client 1466，旧备注为 M5；状态为 connected，来源为家庭国内出口 `180.111.118.161`。备注和共同出口本身不足以判断是哪台 Mac。

进一步只比较身份哈希确认：M1 NPC 配置与 M5 NPC 配置复用了同一个 vkey，均匹配云 client 1466。未打印或存储 vkey、vkey 哈希、auth 或进程环境值。M5 此时事实上已开机，并且重复 NPC 仍运行，因此公网正在到达 M5，而不是本轮测试的 M1。

M5 精确信息，供 root 有范围操作：

| 项目 | 快照 |
|---|---|
| 主机 | `macbook-m5-128.local`，Wi-Fi `192.168.9.126` |
| 用户 | `yawen`，UID 502 |
| NPC label / plist | `local.remoteandroid.npc-host` / `/Users/yawen/Library/LaunchAgents/local.remoteandroid.npc-host.plist` |
| 物理中继 label / plist | `local.remoteandroid.npc-physical` / `/Users/yawen/Library/LaunchAgents/local.remoteandroid.npc-physical.plist` |
| NPC / relay PID | 16099 / 17980 |
| 配置 | `/Users/yawen/Library/Application Support/AndroidRemote/direct/npc-host/npc.conf` |
| M5 gateway | PID 3394，仅回环与 Wi-Fi 15556 LISTEN，无活动媒体 TCP 连接 |
| nginx | 未发现进程 |

M5 NPC 的四条到物理中继和云 8024 的连接是控制隧道，不应据此称为四个用户正在串流。

## 最小切换、验收与回滚

由 root 执行，审计 agent 不执行：

1. 将上述 M5 两个 plist 与禁用状态的必要 metadata 保存到 owner-only 原有备份位置，不导出凭据和私有配置。
2. 禁用并 `bootout gui/502/<label>` 上述两项重复 NPC 服务；保留 M5 gateway、AVD、数据、身份配置与其他服务。
3. 确认 M5 不再有重复 NPC control；如需要 NPS 重选唯一连接，仅重新启动 M1 的 `local.huoguo.m1.npc`。
4. 重新以物理 en7 访问公网，要求 TLS 证书与 M1一致、完整 ping 指向 M1/5556/videotoolbox；同时观察本机目标连接。随后比较公网与 M1 的更新清单、APK 尺寸与 SHA-256。
5. 发布候选的服务端模块检查完毕后，确认仍无活动会话，再进行一次有范围 gateway 重启与健康检查。

如需恢复旧 M5 路径，先停止 M1 同身份 NPC，再恢复 M5 原 plist 与启用状态并 bootstrap；不能重新让两台常驻 NPC 使用同一身份。客户端发布失败应先恢复交付链，避免改原 Release；已安装新版不能通过现有更新逻辑静默降级。客户端修复应使用更高版本码的新发布，服务器代码可恢复私有备份后重启并验收。

新版发布后可安全触发一次既有轮询任务：`launchctl kickstart gui/<M1用户UID>/local.huoguo.m1.updates`。它执行既有受限更新任务，不替换网关源代码。也可以在 canonical 工程根目录使用 `DIRECT_STATE_DIR=<既有M1状态目录> UPDATE_USE_GH=1 /opt/homebrew/bin/python3 scripts/pull_updates.py`；先确认任务空闲，避免与300秒轮询并行。两种方式均应读回公网清单和候选 APK，而不只检查命令退出码。

## 本版文案和地址的核对事项

- **仅历史审计，已被 v1.30 线路迁移替代：** 当时 App 的旧 Tailscale 快捷地址是 `100.65.0.2`，当时 GUI 客户端 tailnet IP 读回为 `100.64.0.2`（旧线路历史地址，不可作为当前入口）。本次只读实测两个地址都能返回 M1 相同证书与 ping，因此旧地址并非该时点证据中的断路。当时“新版可使用当前地址并保留旧地址与已保存密码的有范围迁移”的建议已经过时；当前只保留 yilufa 的 `100.65.0.2` 为可选入口，旧值仅限一次迁移及旧密码兼容。“能访问”不证明 P2P direct，按钮不能保证直连。
- 下载页生成器仍写“5分钟息屏”，与用户后续常亮要求不符，发布时应修正。
- 正式 App 媒体仍是 TLS/TCP；本轮 UDP 实验结果不能写成已交付公网 UDP/P2P 新版。
- 原签名、已保存设置、旧版本缓存防降级与更新前确认交互须保留。GitHub 发布成功与手机覆盖升级成功需要分别记录。
