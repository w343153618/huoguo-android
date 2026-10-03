# NPS / Tailscale 真机传输实验

使用当前工程源码与原已安装 App 的接口编译独立 Instrumentation；不会覆盖原 App。需已获授权且 root 的 USB Android 手机。测试把参数放在内存，结束时恢复原参数；保存密码和地址保持原样。无需截图采样。

```sh
python3 scripts/probes/build_phone_transport_probe.py
```

安装产物在本目录 `phone/build/phoneprobe.apk`，已被 Git 忽略。通过 ADB 推送到 `/data/local/tmp/`，用手机 `su -c pm install -r -t` 安装；不要把调试签名 APK 覆盖到正式 App。

```sh
python3 scripts/probes/run_phone_transport.py quic \
  --host 146.56.249.175:15557 --seconds 45 --fps 60 \
  --bitrate 4000000 --buffer 80 --source '实际片源与播放阶段' \
  --credential-file /受限路径/既有账号凭据 \
  --output docs/evidence/本次实验/phone-quic.json
```

凭据文件格式是既有账号的 `用户名:密码`，权限必须 600；不放工程目录，不写入命令行、报告或 Git。脚本经 ADB 标准输入写入 App 私有文件，App 消费后删除，脚本 finally 再清理。结束所有实验后删除宿主机这个临时凭据文件。保留原有账号，不创建测试登录账号。

当前 `run_phone_transport.py` 直接保留 `--host` 指定的完整地址和端口，不添加手机 DNAT。2026-09-30 的原 v1.21 连接函数固定 15556，当时才通过目标 App UID、指定 IPv4 和 TCP 15556 的临时 OUTPUT DNAT 使用隔离 15557；旧测试结束时清理，其他 UID 和目标不受影响。历史脚本若曾被强制终止，检查手机 `iptables -t nat -S OUTPUT` 中的 `huoguo-transport-test` 注释规则；不要把旧重定向用于当前测试。

M1 NPC 二进制和 vkey 配置在用户受限目录，历史外部运行路径见 `docs/evidence/nps-transport-20260930/README.md`。独立实验统一使用下列高端口；这些是 NPS/NPC **bridge** 的端口，不是上面手机 App 的媒体映射端口。

| 独立实验协议 | M1 回环 relay 入口 | 腾讯云实验 bridge 目标 | 仓库入口 |
| --- | --- | --- | --- |
| TCP | `127.0.0.1:48027` | `146.56.249.175:48024/TCP` | `tcp_test_relay.py` |
| KCP | `127.0.0.1:48025` | `146.56.249.175:48025/UDP` | `udp_test_relay.py kcp` |
| QUIC | `127.0.0.1:48026` | `146.56.249.175:48026/UDP` | `udp_test_relay.py quic` |

```sh
NPC_PHYSICAL_INTERFACES=en7,en0 python3 experiments/nps-transport/tcp_test_relay.py
NPC_PHYSICAL_INTERFACES=en7,en0 python3 experiments/nps-transport/udp_test_relay.py quic
NPC_PHYSICAL_INTERFACES=en7,en0 python3 experiments/nps-transport/udp_test_relay.py kcp
```

每个命令是一个独立常驻进程，按本轮实际协议启动。入口仅监听回环，使用 `IP_BOUND_IF` 绑定允许的物理网卡，拒绝隐式代理回退；入口还检查实验端口位于 `32768..65535`，不能回落到正式 `8024/8025`。`udp_test_relay.py` 复用根目录 UDP relay 实现，但只在自身进程中设置实验端口；保留根目录历史默认值，不再用根目录入口直接启动新实验。复制部署时须保留相对工程布局及它引用的两个根目录 relay 模块，或直接使用此工程入口。

运行前先备份云端实验配置，并核对独立 NPS 的 `bridge_port=48024`、`bridge_kcp_port=48025`、`bridge_quic_port=48026`，以及受限目录中的 NPC `server_addr`、relay 启动脚本和高端口放行规则。仓库默认值更新不能证明实际监听、NPC 认证或公网链路已经迁移成功；须逐项读回并独立验证。正式云端 `8024/8025`、M1 正式 TCP relay 的回环 `18024` 及正式 NPC 身份保持原状。高端口用于避免冲突和区分实验，不会直接提高传输性能。

2026-09-30 历史测量采用云 TCP `18024`、QUIC UDP `8025`、KCP UDP `8024`，M1 实验入口分别为 `18027/18025/18026`，当时使用 `NPC_PHYSICAL_INTERFACES=en0`。这些旧值只属于已冻结证据，不能改写为新高端口，也不能继续作为新测试的启动配置。

KCP 的服务端实验客户端必须允许配置连接 `ConfigConnAllow=true`；否则官方 v0.34.7 文件启动方式会在等待 WORK_CONFIG 回应时挂起。修改实验配置须停止并备份实验实例，不能改正式 NPS。

记录手机网络、NPS bridge 协议、物理出口、Tailscale direct/DERP 状态、片源和版本。输出包含接收/实际呈现/丢帧、硬解名称、显示间隔、RTT、动态码率、内部解码补偿、AudioTrack 队列。AudioTrack 队列不是已测得的音画偏差。帧率上限不是片源帧率。

2026-09-30 真机结果和服务状态见 [实测证据](../../docs/evidence/nps-transport-20260930/README.md)。
