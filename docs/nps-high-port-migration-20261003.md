# 独立 NPS 实验迁移高端口

2026-10-03按用户要求迁移唯一独立实验实例。正式 `nps.service` 没有停止、重启、替换或接收reload信号；所有正式NPC，包括其他服务器日常运维连接，都受保护。只有 `nps-android-transport-test.service` 在备份和空闲核对后重启。

## 实际端口与配置

| 独立实验用途 | 云端新端口 | M1实验回环入口 |
| --- | --- | --- |
| NPC TCP bridge | 48024/TCP | 127.0.0.1:48027 |
| NPC KCP bridge | 48025/UDP | 127.0.0.1:48025 |
| NPC QUIC bridge | 48026/UDP | 127.0.0.1:48026 |
| 唯一旧TCP转发任务ID1 | 48028/TCP（原15557） | 目标配置未修改 |

云端独立目录仍为 `/srv/nps-android-transport-test`，原 `bridge_type=both`、账号及专用client1身份均保留。TCP bridge由18024迁48024，KCP由8024迁48025，QUIC由8025迁48026。云端两份保留的NPC配置和M1 `~/.config/huoguo-android/transport-test/npc.conf` 已同步。实验服务仍为原来的disabled启动策略，没有设为开机启动，没有创建第二个实验实例。

正式云端TCP8024、TLS/TCP8025、M1正式物理relay回环18024、App正式映射15556/15558及所有正式NPC身份均未修改。云端现有geo_in/geo_fwd链摘要前后相同，没有调整国内来源过滤或为新端口增加绕过规则。

TCP测试入口为 [tcp_test_relay.py](../experiments/nps-transport/tcp_test_relay.py)，UDP测试入口为 [udp_test_relay.py](../experiments/nps-transport/udp_test_relay.py)。后者复用旧物理绑定实现，仅在独立进程中设实验端口，不改变根目录模块的历史默认值。两入口检查端口必须处于32768..65535；以后实验先核对占用，不使用正式8024/8025及正式本地18024。高端口是隔离和避免冲突的安排，不是帧率或时延改善证据。9月30日历史测量保留原端口，不能追改。

## 修改前备份与范围

此前完整NPS备份 `/root/nps-backups/before-kcp-quic-20261003-112543/` 保留。本次另在云端 `/root/nps-backups/high-ports-20261003-114640/` 保存独立conf全目录、两NPC配置、systemd单元、原服务状态及身份摘要，并在stop前核对副本。M1受限配置原件保存在其transport-test目录的 `backups/high-ports-20261003-114731/`。备份不进Git。

迁移事务的失败分支只恢复独立实例；本次未触发回滚。实验原PID497073变为3374787，三项高bridge监听均属于这个新实验PID；旧UDP8024/8025均已无监听者。任务ID1持久化端口读回48028。正式PID2242499和主配置SHA `2c75fad8bb04d0697e46df86409f7d2d6bdfc179015ad46fc59189c11cc786bf` 前后相同；正式TCP/TLS监听保持，控制入口观察到39个已建立socket。连接快照不是39台设备或39个用户的计数，也不能证明整个观察间隔逐包零抖动。

## 验证层次

迁移前没有运行中的实验NPC或relay；云端UDP8024/8025做了25秒被动元数据观察，未看到包。它支持当前未发现活跃实验，不保证休眠客户端永不重连。

高端口部署后，使用既有实验client1分别完成一次TCP、QUIC、KCP真实NPC认证。三个协议均通过M1物理en7绑定relay抵达腾讯云，官方客户端读到 `Successful connection with server`，均无认证错误。测试身份与所有正式身份无重叠；每轮顺序执行，用后关闭自己的NPC及relay，退出码均0，没有并发正式vkey影子。

TCP首次观察的匹配表达式漏掉该版本实际成功文案，导致初始JSON错误标false；核对同一原始受限日志后修正分类，未重复TCP认证。初始分类记录仍保留，不能把观测器错误当TCP网络失败。

这些是实际公网bridge认证验证，未向任务48028发送数据，没有启动安卓媒体、操作朋友手机或进行FPS测试。旧task1目标不是loopback15556，迁移不改变其目标权限；以后若验证真实转发，应先单独核对并限定目标，不能凭本次认证成功宣称安卓高端口转发或UDP媒体产品已验收。测试任务48028已持久化，监听生命周期取决于实验NPC在线状态，不要求客户端退出后仍保持转发监听。

源代码语法、导入无副作用、UDP入口help及共用relay的5项已有检查通过。受限原始日志和迁移/认证receipt留在忽略目录 `evidence/nps-high-ports-20261003/`，不提交密钥、运行配置全文或原始日志。

## 正式 NPS 的保护边界

用户明确其他服务器依赖正式NPS日常运维。正式NPC只要在线，就不能为恢复KCP/QUIC菜单停止、重启、替换或发送reload信号；Android媒体端口空闲不是整台NPS维护许可。本次已解除实验端口占用，但正式 `bridge_type=tcp` 仍保持，网页KCP/QUIC缺项没有因此自动修复。后续协议实验继续使用此独立高端口实例，不能拿它的认证成功当正式入口恢复。参见[正式缺项诊断与完整备份](nps-quic-visibility-20261003.md)。
