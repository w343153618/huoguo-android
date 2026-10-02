# 当前 M1 部署状态

更新：2026-10-02。主工程为 huoguo-android；当前 M1 公网入口为15556，M5已恢复为独立备选入口15558，用户可手选，见 [双主机方案](dual-host-nps-plan-20261002.md)。历史虚拟机和数据保留。

## 已读回的入口

| 入口 | 当前结果 |
|---|---|
| https://146.56.249.175:15556/ping | M1 / emulator-5556 / videotoolbox |
| https://192.168.9.128:15556/ping | M1，有线 en7 |
| https://192.168.9.125:15556/ping | M1，无线 en0 |
| https://100.64.0.2:15556/ping | M1，10月2日已读回的 Tailnet 入口；不据此推断 direct/DERP |
| http://192.168.9.128:8089/ | 下载页 HTTP 200 |
| http://192.168.9.125:8089/ | 下载页 HTTP 200 |

当前模拟器 RemoteAndroid17Compare、emulator-5556 已按用户最新实验要求改为 **6 核/16 GiB、物理 1080x1920、480 dpi**；同一 AVD 冷启动并清除 wm 覆盖后读回，见 [1080p 配置证据](evidence/pre-latch-20261001/m1-1080-config-change.json)。M1 网关明确验证该物理 profile，编码长边上限改为 2400（实际不会超出物理 1920）；独立 1080p UDP 实验使用 max-size=1920。此前 [720p 配置](evidence/native-iteration-20261001/m1-6cpu-16g-720p-readback.json)及其测量保留为历史。不同物理配置的帧率证据不能直接互换。网关 VideoToolbox H.264。请求 FPS 是上限，真实视频不同内容帧率另行测量。

用户随后明确取消 5 分钟自动待机：当前 M1 网关 `DIRECT_IDLE_DELAY=0`，不会再自动发送暂停媒体或息屏；安卓 `screen_off_timeout=2147483647`、`stay_on_while_plugged_in=15`，供电时保持唤醒，见 [常亮配置](evidence/native-iteration-20261001/m1-always-on-readback.json)。这不改变 Mac 物理显示器配置，也不代表 Mac 关机或睡眠时仍能串流。

M1 模拟器启动已启用 `ANDROID_EMU_MEDIA_DECODER_VTB=1`，网关持久化 `DIRECT_HWUI_RENDERER=skiavk`。蓝色 M 视频 App 本次仅修改兼容客户端 `VISIONOS_1_02` 和 `morphe_force_avc_codec=true`，保留其他偏好。真实 YouTube H.264 源解码会话的 VideoToolbox 硬件属性已读回为 true，见 [硬解审计](evidence/native-iteration-20261001/source-avc-vtb-hardware-audit.json)；它不等于每个 App、每一帧都不会回退，也不等于稳定 60 FPS。

源端调试中发现冷启动后的独立 UDP 探针未经过网关的 boot profile，Morphe 实际会使用 Skia OpenGL；本轮已恢复既有 `skiavk` 目标并核对实际 `Pipeline=Skia (Vulkan)`。默认 HWC 合成和原物理120Hz AVD配置保留，播放时 render读回60Hz。连续调试、60/120Hz与GPU合成对照及完整限制见 [源端结果](source-causal-results-20261001.md)；这些实验未发布新版App，也不证明稳定60/120FPS。

## 运行依赖及状态

源程序均来自本工程；外部状态和编译好的硬件依赖在 `~/Documents/ChatGPT/others/android-remote/m1-compare/`。账户、证书、设备镜像、签名、NPC 身份文件不进入 Git。源迁移不删除旧运行目录。

- local.remoteandroid.m1compare.gateway：当前工程 gateway.py；DIRECT_STATE_DIR 指向外部状态目录。
- local.remoteandroid.download：外部 download 目录、0.0.0.0:8089；下载页由当前工程生成，旧 index-m5 书签提供 M1 内容。
- local.huoguo.m1.npc-relay：当前 npc_physical_relay.py；loopback 18024 → 腾讯云 8024；IP_BOUND_IF 绑定 en7，失败再尝试 en0，不允许代理回退。
- local.huoguo.m1.npc：当前 scripts/run_m1_npc.py；身份从既有受限外部文件读取到进程环境，命令行不放密钥，日志 warn，连接上述 loopback。
- local.huoguo.m1.updates：当前 scripts/pull_updates.py；300 秒检查私有 updates 分支，保留原签名与不可降级验证。

LaunchAgent 文件位于用户 Library/LaunchAgents；自动恢复运行不等于 Mac 在睡眠/关机时还能服务。旧 gateway/NPC 配置备份在用户受限 review-backup 目录。

## 本次公网规则修正

云端曾存在临时实验规则：PREROUTING TCP 15556 REDIRECT 到 15557。它只影响外部访问，云端回环 15556 测试仍正常，因此必须分别测。该规则撤销后，物理有线公网入口确认返回 M1。正式 NPS 与隔离实验进程均未重启；国内来源过滤规则保留。云端防火墙快照 /root/huoguo-before-entry-restore-20261001.rules 可审计。

## 版本与验收边界

本轮准备发布1.29维护版；正式交付以GitHub版本、服务端清单、APK摘要和真机读回一致为准。现有 App 视频、音频、控制仍是 TLS TCP 流；不能因为有 Tailscale/QUIC 外层就宣传为原生 UDP 视频。独立原生 UDP 视频探针已在一加 15 + M1 局域网真实 YouTube 上跑通，8 Mbps 样本源端呈现节奏 55.569 FPS、手机 54.601 FPS；它还不是包含音频、触控、公网 P2P 的新产品。用户最新要求新版实时视频和声音必须使用 UDP，信令与认证可以使用 HTTPS；不得回退 TCP 媒体。

本地 App 已移除 40 Mbps 固定档位，旧保存的 40 Mbps 数值按自定义保留；未继续追加 24 Mbps 性能测试。应用发布状态与实验探针状态须分别报告。

本轮真实手机测量、源端供帧与局限见 evidence/gemini-review-20260930/README.md。第二代 C++ 触控和 Moonlight FEC 验证见 ../experiments/moonlight-v2/README.md；它们不代表新的完整串流产品已交付。

此前 720p 的 AVD 与两个启动配置回滚副本仍保留于 `~/.config/huoguo-android/experiments/20261001-m1-720p-16g/`。最新 1080p 调整的精确原配置另存 owner-only 私有临时备份，路径只在本地操作记录中；未复制或删除 userdata 磁盘。物理分辨率调整不等于 App 崩溃已修复，也不等于已经达到 60/120 FPS。最新 1080p 真实视频与手机结果见 [实验记录](pre-latch-results-20261001.md)。
