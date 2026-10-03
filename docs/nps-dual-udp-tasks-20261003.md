# M1/M5 正式 NPS UDP 任务部署（2026-10-03）

用户明确要求两台 NPC 使用基于 UDP 的连接，且 NPS 隧道任务也使用 UDP。本次新增实际 UDP 任务，未修改 NPS 核心、重启服务、改变原客户端身份或国内过滤。正式已安装 v1.30 App 仍使用 TLS/TCP 媒体，不能把本次 NPS 配置验收当成 App 公网 UDP 产品验收。

## 当前运行与实际验证

| 层 | M1 | M5 |
| --- | --- | --- |
| 实时 NPC bridge Mode | `quic,quic`，client1466 | `quic,quic`，client1468 |
| 新公网 UDP task | task1412，UDP15556 | task1413，UDP15558 |
| NPS 新任务 target | `127.0.0.1:45965/UDP` | `127.0.0.1:45965/UDP` |
| 新任务 remark | HuoguoAndroid-M1-UDP-15556 | HuoguoAndroid-M5-UDP-15558 |
| 独立认证小包往返 | 10/10，0发送错误/超时 | 10/10，0发送错误/超时 |
| 保留的旧 TCP task | task1409，TCP15556 | task1411，TCP15558 |
| 测试后受信 TLS 健康 | /ping 200 | /ping 200 |

实时管理员 API 的两台 Mode 为 `quic,quic`。持久化 clients.json 仍可保留历史 tcp 字段，它不是这次运行协议的证据。客户端页面刷新应以新的 /client/list 数据为准。隧道页现在多两行上述 UDP remark，原 TCP 两行是已装 App 的兼容入口。

两台 NPC 都保持 QUIC，经物理接口绑定的 loopbackUDP48126→云UDP8025；M1 en7、M5 en11的部署没有变更。新增 task 的 ProxyProtocol0、LocalProxyfalse，不能把额外代理头混入原生媒体认证包。国内 geo_in/geo_fwd规则与引用比较保持；原38在线客户端全部保留，正式 PID3412973及 /proc start_ticks未变化。源级和惯性fixture共68项通过（新任务42、旧M5 helper9、小包probe17）。

## 备份与限定创建

新配置备份为 `/root/nps-backups/dual-udp-20261003T061535Z-922128a4/`。完整复制 /etc/nps/conf中的9个普通文件，含嵌套项，逐文件SHA校验；目录0700，文件0600。私有manifest保存原进程身份、原在线ID、规则引用与校验值。没有将配置、vkey、cookie或口令提交到Git。

[限定管理helper](../scripts/deployment/create_dual_udp_nps_tasks.py)默认只读，只有 --create 才新增这两条任务；备份后仅调用 /index/add，API返回新taskID后做有界监听/readback验证。客户端配置、任务1409/1411与其他所有既有任务在内存中按静态配置比对。没有直接改数据库、调用restart/reload或重发未知结果的添加请求。

首次只读预检的 API 查询漏传实际 type，admin-global getter返回空表；因此helper拒绝，并未创建任何任务。修正为按持久化 Mode（本轮mixProxy/tcp）另加udp分类型合并，再验证所有持久化ID完整。创建成功后的一次只读比较报 original_runtime_config_changed；独立相邻快照未复现静态差异，随后完整只读保护通过。此瞬态未定位，不改排除规则、不对原NPC重启或改配置，失败原始分类保留在受限evidence中。

## 此次小包测试的测量边界

[probe](../scripts/probes/nps_udp_task_probe.py)每个请求/响应64字节、HMAC、方向字段、fresh nonce和sequence，server保留有界防重放集合。没有临时登录账号；独立随机probe secret只经原SSH复制到受限Mac路径，不发送密钥至公网，结束后两台均删除。

两台server只监听loopback45965，运行25秒后自然退出0。客户端是M1 macOS，IP_BOUND_IF绑定en7并读回，目的明确为云UDP15556/15558。结果包括实际请求与响应计数及毫秒RTT，[裁剪JSON](nps-dual-udp-tasks-20261003.json)可核对。此为同家南京主机经指定公网的真实UDP任务往返，没有运行安卓媒体，也没有手机、流量、异地、V50、视频FPS、声画或触控延时验收。

测试结束后Mac上45965已关闭；新UDP任务保持已启用、可供后续认证媒体gateway绑定。**此时公开UDP任务有监听，但永久认证App媒体gateway尚未部署。不能把它列成旧App已经可用的UDP视频线路。** UDP任务不指向ADB、文件服务器或任意宿主代理。

## App迁移与内部可靠流边界

官方v0.34.7的 UDP task使用 WrapFramed(net.Conn)，当前NPC QUIC用可靠有序 quic.Stream。两端外层套接字可为UDP，但桥接段仍可能等待重传与排队；本次20小包正常不能证明大视频帧的抖动已经解决。真实Datagram中继/P2P仍是另一项研发与验收。

[逐文件适配计划](nps-public-udp-app-integration-plan-20261003.md)列明当前入口阻断：LAN/Tailnet-only scope、固定45560/45963双重App校验、正式gateway缺/udp/session、本地bind与公开描述混用、代理loopback peer以及共用guest准入。下一项先补明确的节点/端点合同与fixture，再做机主M1有界公网UDP App媒体。认证、端到端GCM、防重放、取消、正式在线保护保持。失败明确报错，不静默转TCP媒体。

M5朋友新版上线仍须宿主/LAN隔离验收；日常M5正式会话不因M1实验打断。老TCP入口在匹配App、新gateway和真实验证完成前保留。回滚只停止/删除新task1412/1413，不能用配置整目录覆盖或全量重启影响其他设备。既有QUIC bridge、身份、原任务与备份都保留。
