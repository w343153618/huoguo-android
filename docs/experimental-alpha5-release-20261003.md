# UDP 机主实验版 1.31-alpha.5 发布（2026-10-03）

用户要求尽快获得可下载的 UDP 测试 App。本次已将原签名、独立包名的已测试 APK 发布为 GitHub prerelease，并开启独立有界 M1 LAN 入口。正式 v1.30 与 M5 日常服务保留；这不是公网 NPS UDP 或朋友安全部署完成。

## 发布与准确来源

- [GitHub release](https://github.com/w343153618/huoguo-android/releases/tag/experimental-v1.31-alpha.5)
- [APK 下载](https://github.com/w343153618/huoguo-android/releases/download/experimental-v1.31-alpha.5/HuoguoAndroidExperimental.apk)
- 包名 `local.remoteandroid.direct.experiment`，版本 `1.31-alpha.5` / code36，标签“给火锅的安卓 · 实验版”；与正式版并存。
- APK 7,808,151 字节，SHA-256 `f5cf8bbf69f9e7fe1f3babb78700abc6f254ce41b71c75f324765d9fa945da85`。
- 原单签名证书 SHA-256 `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`；JNI `578347ca24008ce7b987c77f2f1356c56761ac3d9ee4e7be89b8e9130476e402`。
- Android 11 / min30，arm64-v8a，`debuggable=true`，可调试机主实验构建；发行说明中已明确披露。
- 精确 tag/source `d86330293834a6c107eb5eed6bb6672611013a6a`。ENOBUFS 轮外部 pins 的18项已逐文件 `git show` 对比，18/18匹配。实验分支较新 native 生命周期240项字段不在此 APK/JNI中，不能追认旧包数据。
- 调用已有 `validate_apk` 与 `prepare` 实际校验；仅推送精确 artifact tag，没有把较新实验分支回退。原 APK 未重建或改签。`experiment.json`、SHA清单随包上传；正式更新manifest和默认latest未变。
- GitHub readback三资产uploaded/prerelease；通过 `gh release download` 下载后的 APK SHA与冻结文件一致。没有把 APK、签名私钥或真实配置提交到Git。

有界ZIP/DEX检查未发现检查模式内的私钥/凭据字面量，两份内嵌PEM均为公开证书；默认用户名 `huoguo` 存在，不是内嵌密码。这是范围明确的静态检查，不是全面秘密审计。

## 立即试用线路

当前只开启 **M1 LAN**：手机接家里 Wi-Fi，在实验 App 选择“物理局域网 · 手填 M1 IP”，填写 `192.168.9.128:45560`，输入已有 `huoguo` 账号口令。HTTPS45560仅用于登录/描述/状态/取消，实时视频、AAC音频和原生触控均用认证UDP45963。失败不会回退TCP媒体。

建议首次 `720P / 4 Mbps / 60 FPS cap / 80 ms`，勾选“UDP音频”，PCM队列和启动准入实验保持OFF。App保存参数，口令仅本进程内存；60/120是请求上限，不是独立呈现证明。

试用监听器启动于 `2026-10-03T06:35:18Z`，接受连接最长3600秒（北京时间约15:35结束），单次session仍最长120秒，结束可重新连接。最终清理可能额外等待45秒；只有自然shutdown/quiescence确认后才复用资源。不会自动重复启动常驻实例。实际active PID及private source snapshot见受限checkpoint，不以公开文档PID替代活状态。

本次启动前复核物理en7地址、正式M1 TCP15556无ESTABLISHED、45560/45963无占用、guest boot completed、物理1080×1920；从已有正式plist仅继承DIRECT_环境，不打印秘密。`DIRECT_IDLE_DELAY=0`保留，不调用ensure_android或修改屏幕/账号。精确d863302 Python源冻结在外部受限snapshot，worker子进程也从该snapshot运行；packetizer、encoder与cancel-capable JAR均按原真实实验SHA核对。

受信证书 `https://192.168.9.128:45560/ping` 返回200、`network_scope=lan`、`media=UDP`。这是监听器健康，不替代此次新LAN用户视频/音画/触控验收。该包既有真实视频结果见 [ENOBUFS轮](udp-enobufs-real-video-20261003.md)，限频一加12同家Tailnet样本不是公网、V50或物理延时保证。

APK也有已登记M1 Tailnet客户端选项，但本次不并开第二个gateway；当前选Tailnet不可连接，不应提示用户它已常驻。候选公网上49556控制与15556媒体适配仍未完成，不能填NPS公网地址试本入口。M5正式公网仍TLS/TCP。

## 安全与后续保护

这是 **UID501机主非隔离** 试用，不给朋友/公众使用，不把界面“隔离实验版”误读为宿主/LAN隔离已通过；标签仅指独立客户端。保留现有认证、完整性、防重放、短期限、取消以及正式会话优先保护。未来朋友M5新版仍须宿主文件、LAN和权限隔离验收。

当前试用checkpoint `docs/evidence/alpha5-owner-trial-20261003/runtime-state.json`；后续heartbeat/实验先核对PID、命令与端口，不抢占此guest、手机或试用listener，不另开LAN/Tailnet/NPS媒体实例。试用期间可继续离线源工作；自然结束后核对quiescence，再开始独立有界下一轮。

下一源阶段是已完成21项组件检查的 `udp_nps_profile.py` 合同接入：split advertised与local bind、registry、首个GCM READY pin、App双重descriptor校验、共同guest准入。正式NPS新增UDP任务已有10/10有界小包往返证据，但内部仍可靠quic.Stream；不能声称等价Datagram或已解决视频卡顿。所有其他NPC和正式M5在线会话继续保护，不重启NPS。
