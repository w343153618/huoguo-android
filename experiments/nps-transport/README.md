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

原 v1.21 的连接函数固定 15556。为了使用隔离 15557 而不更换手机 App，脚本仅对目标 App UID、指定 IPv4 和 TCP 15556 添加临时 OUTPUT DNAT，测试 finally 删除；其他 UID 和目标不受影响。如脚本被强制终止，检查手机 `iptables -t nat -S OUTPUT` 中的 `huoguo-transport-test` 注释规则并清理。

M1 NPC 二进制和 vkey 配置在用户受限目录，外部运行路径见 `docs/evidence/nps-transport-20260930/README.md`。测试 TCP relay 的固定目的地是云 18024，QUIC/KCP relay 是根目录 `npc_udp_physical_relay.py`，目的地分别云 UDP 8025 / 8024。仅监听回环，用 IP_BOUND_IF 绑定允许的物理网卡，拒绝隐式代理回退。M1 本次使用 `NPC_PHYSICAL_INTERFACES=en0`。

KCP 的服务端实验客户端必须允许配置连接 `ConfigConnAllow=true`；否则官方 v0.34.7 文件启动方式会在等待 WORK_CONFIG 回应时挂起。修改实验配置须停止并备份实验实例，不能改正式 NPS。

记录手机网络、NPS bridge 协议、物理出口、Tailscale direct/DERP 状态、片源和版本。输出包含接收/实际呈现/丢帧、硬解名称、显示间隔、RTT、动态码率、内部解码补偿、AudioTrack 队列。AudioTrack 队列不是已测得的音画偏差。帧率上限不是片源帧率。

2026-09-30 真机结果和服务状态见 [实测证据](../../docs/evidence/nps-transport-20260930/README.md)。
