# M1 / M5 独立 NPS 入口部署记录

用户最新要求是两台 Mac 都保留，App 手动选择。之前临时停用 M5 NPC 是为消除重复 client 1466；不应把它解释成永久移除 M5。本文记录截至2026-10-02发布v1.29的独立身份部署与分层验收：M5命令模式NPC已上线，两个公网入口均验证TLS及HTTP200，两端升级清单、APK和8089下载页已读回一致版本。真机按钮短连也已分别完成，但持续播放流畅度、实际显示FPS、音画同步和火锅V50仍需另行验收。完整记录见 [本次发布证据](evidence/release-1.29-20261002/README.md)。

| 主机 | 公网入口 | NPC 身份 | Mac 本机目标 |
|---|---|---|---|
| M1 | `146.56.249.175:15556` | 保留现 client 1466 / task 1409 | M1 `127.0.0.1:15556` |
| M5 | `146.56.249.175:15558` | 独立 client 1468 / task 1411 | M5 `127.0.0.1:15556` |

15557由旧隔离实验 NPS 占用，不能取用。准备时正式 NPS 数据库无15558任务，服务器无15558监听；此后 root 的 `--create` 成功创建上述1468/1411。M1/M5各自本机都用15556没有冲突；公网入口以不同端口明确区分，而不是两台共用身份抢同一隧道。

| 验证层 | 当前记录 |
|---|---|
| 云配置创建 | root报告 client1468/task1411/15558创建成功；M1原1466/1409保留 |
| M1原入口恢复 | root已读回公网15556的M1证书及M1/5556 ping |
| M5独立NPC上线 | 新key已传至M5受限配置，命令模式wrapper已上线；实时管理API确认client1468连接 |
| M5公网15558 | 公网TLS证书与M5一致，ping200；实时client1466及1468均在线，来源均180.111.118.161 |
| 两端新版升级投递 | 两公网清单及APK均HTTP200、v1.29/code30；两个8089页面显示新版及双入口，APK摘要与真机测包一致 |
| 真机按钮短连 | 一加12在Wi-Fi上分别手选M1/M5公网入口，短连结束时均仍在运行；该smoke不作为持续视频或V50性能验收 |

## 管理 API 与 helper

现有管理服务是云上 `http://127.0.0.1:18080`，不需新建或公开管理端口。配置没有 API `auth_key`，现有管理员认证应使用 cookie 会话、页面 nonce、公钥加密登录。云上Python已有 `cryptography`。

实现依据固定版本官方源：

- [API说明](https://github.com/djylb/nps/blob/v0.34.7/docs/api.md)
- [登录 nonce / RSA 验证](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/login.go)
- [client/add](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/client.go)
- [index/add](https://github.com/djylb/nps/blob/v0.34.7/web/controllers/index_tunnel.go)

可复用源码：[create_m5_nps_tunnel.py](../scripts/deployment/create_m5_nps_tunnel.py)。本机临时交付副本是 `/private/tmp/huoguo-m5-nps-independent.py`，mode600。脚本必须在云上以root运行，默认只读预检；`--create`由root显式执行。

创建前预检已通过：管理员登录和只读列表验证成功、15558无冲突、独立 client/task 当时不存在、vkey文件当时不存在。该预检没有创建身份、隧道或文件，未修改规则。此后 root 已显式执行 `--create`，返回 client1468/task1411。不能把创建前的不存在状态当成当前状态。

脚本的有范围创建步骤：

1. 读取云本地 `nps.conf` 管理员凭据于内存，只连接回环管理 API。
2. 验证端口、备注、目标和已有记录；冲突、重复备注或旧client1466均拒绝。
3. 将原 `clients.json`、`tasks.json`、`nps.conf` 私有备份至 `/root/.config/huoguo-m5-npc/backup-<UTC>-<random>/`，目录700、文件600。
4. 强随机生成独立vkey，仅写 `/root/.config/huoguo-m5-npc/vkey`，文件600。
5. `POST /client/add`，备注 `HuoguoAndroid-M5-independent`，禁止配置模式接管、最多一个隧道、不创建Web登录账号。
6. `POST /index/add`，TCP、15558、目标 `127.0.0.1:15556`、`local_proxy=false`、备注 `HuoguoAndroid-M5-15558`。
7. 只输出状态、client/task IDs与私有路径；不输出secret、cookie或身份哈希。相同完整记录重试可复用；部分创建失败保留私有key供root核对重试，不删除原生产记录。

默认预检和创建明确分开。脚本不会直接修改活跃NPS数据库文件或重启NPS；备份也不应直接覆盖运行中的数据库作为日常回滚。回滚新入口应按新建ID撤销对应task/client，同时停止M5新NPC；保留M1原client1466/task1409。

## M5 恢复与保护

root已完成云身份创建，并经已有验证的SSH将新key传至M5受限配置。不能把key放命令行、App、Git或日志。保留M5物理回环relay与目的云8024、en11/en0优先策略；现有网关、AVD、账号和用户数据不需重建。

原 M5 NPC 使用 `-config` 模式启动，但新client明确 `ConfigConnAllow=false`，因此仅替换配置文件vkey还不能让原启动方式连接。这不是公网端口或证书故障，不应通过重新启用云配置模式来绕过。root已改为与M1相同的命令模式wrapper：从受限文件读取key，将它放入仅供子进程使用的 `NPC_SERVER_VKEY`，使用 `NPC_SERVER_ADDR=127.0.0.1:18024`，进程参数只保留传输类型与日志选项。读取配置文件作为本地秘密来源与NPC本身的配置模式是两回事。

M5重新启用前已核对wrapper、私有配置权限和秘密值不进入命令行；启用后已读回独立client1468在线及公网目标。wrapper已上线，实时API和双公网TLS分别验证。配置、AVD与网关数据保留，手机持续性能验收另行记录。

M1继续原身份，不应同时改两台key。启用M5后必须确认两个client独立上线，即使它们有相同家庭公网来源IP也不能按IP推断目标归属。App固定快捷地址可分别命名M1/M5；用户仍能清空地址手填，保存设置与密码迁移仅限明确的已知入口。

云 INPUT 已全局经过既有 `geo_in` 链，未按15556限定；新15558同样经过现有CN/big4集合和白名单例外、尾部DROP。本方案不增加例外或改变国内来源规则。腾讯云安全组的外部可达性不能由Linux规则单独确定，需创建监听后从国内物理出口验证。

## 分别验收

- M1公网15556：证书 `17bdaea4cb05b89cf8d64af02b8ed445bdb731f10923da7505d2aec91109c3c0`，ping明确M1/emulator-5556/videotoolbox；见 [双公网健康读回](evidence/release-1.29-20261002/dual-public-health.json)。
- M5公网15558：证书 `15d5e6eb9e8ad36c6e06f94f4db872619d6745e253759a5b46701bb48957139e`，与M5本机一致；M5 ping只有ok:true，以目标证书和独立client1468/task1411共同确认归属。两个client在线的读回见 [NPS实时身份](evidence/release-1.29-20261002/dual-nps-live.json)。
- 两端更新清单和APK分别验证HTTP200、v1.29/versionCode30、3026482字节，SHA-256均为 `9f9fdb8c70ca651fbcc33f8706f7e7274b6e3e7433ef6fdb8066fb7164fc442e`，与真机测试的签名包一致；M1/M5的8089页面均显示新版及双入口，见 [发布投递读回](evidence/release-1.29-20261002/release-delivery.json)。
- App实际按钮短连分别记录于 [M1真机smoke](evidence/release-1.29-20261002/m1-phone-public-button-summary.json) 和 [M5真机smoke](evidence/release-1.29-20261002/m5-phone-public-button-summary.json)。这些记录明确未测得实际显示FPS和实际音画偏差，不能将短连或静态页面的回调FPS解释成连续视频表现。火锅V50、手机流量和持续播放仍待测。
- 两个公网端口提供目标隔离，不保证两个虚拟安卓App/文件自动同步；持续重连、已保存参数和覆盖升级后的用户体验须继续检查。
- 现有发布线路仍是TLS/TCP媒体。双入口部署不会把正式媒体变成UDP；后续UDP/P2P产品化按 [下一轮计划](next-iteration-20261002.md) 分层验收。

离线检查已覆盖独立身份、拥有的精确记录恢复、端口/备注冲突、错误目标/LocalProxy、错误key和其他监听拒绝，共9项通过。实际云配置创建、M5独立NPC上线、双公网目标及新版投递均已另行读回；该记录在本次发布后冻结，后续性能实验另写证据，不覆盖本次验收边界。
