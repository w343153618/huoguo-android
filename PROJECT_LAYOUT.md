# 工程目录与运行环境

本目录 `/Users/wyw/Documents/Codex/others/huoguo-android/` 是后续开发、测试和文档更新的主目录，保留原 `huoguo-android` 仓库的 Git 历史、GitHub 远端和本地未提交修改。不要把旧的 `android-remote/github/huoguo-android` 当作另一个并行开发分支。

当前聊天初始工作区 `/Users/wyw/Documents/ChatGPT/others/` 下另有快捷符号链接 `huoguo-android` 指向本目录；它不产生第二份代码。后续执行命令仍以本目录的绝对路径为准。

| 位置 | 用途 |
| --- | --- |
| `app/` | 手机 Android 客户端：连接、硬件解码、触控、声音、升级和诊断 |
| `gateway.py`、`hardware_stream.py` 等根目录 Python 文件 | Mac 上的安卓网关、VideoToolbox 串流与运行参数 |
| `stream-server/`、`diagnostic-source/` | 安卓虚拟机内的辅助组件源码 |
| `npc_physical_relay.py`、`npc_udp_physical_relay.py` | 对腾讯云 NPS 的物理网卡绑定中继；后者用于 QUIC/KCP 实验 |
| `scripts/` | 发布、部署与网络/视频测量脚本；`scripts/probes/` 中的探针不会自动改变正式服务 |
| `tests/` | 单元与回归检查 |
| `docs/` | 设计、部署、已验证结果；`docs/evidence/` 保留原始或汇总测量，`docs/archive/` 是旧说明快照 |
| `experiments/` | 早期一加 15 和性能基准测试代码/报告；非正式发布入口 |

## 迁入范围和运行边界

2026-09-30 从旧 Git 工作目录复制了**源码、Git 历史、原有未提交改动和文档**，并补入这轮 NPS TCP/QUIC/KCP 测量与 M1 真实视频节奏的证据。M1 测试实例正在运行的 `m1-compare/*.py` 与本目录根目录对应文件逐个字节一致，因此不再复制第二套同名源码；其运行说明保存在 `docs/archive/m1-compare-runtime.md`。测试脚本及测量数据归档在 `experiments/oneplus15-validation/`、`experiments/perf-bench/` 和 `docs/evidence/`。

旧目录仍承担**运行环境**：M1 的 LaunchAgent 和 AVD、M5 的部署目录及下载服务可能引用旧的绝对路径。迁入源码不会自动重载服务或切换它们的工作目录。后续部署必须先核对实例、备份配置，再把本目录中验证过的文件部署到实际服务位置并做真实手机回归。不要删除旧目录、AVD 或日志来完成迁移。

口令、签名密钥、`auth.json`、NPC vkey、Headscale 授权信息、APK 构建产物和 AVD 数据盘不进入 Git。相关文件继续存放在各主机现有受限位置，由部署脚本或环境变量引用。历史报告里可能有设备/网络信息，公开或推送前应重新审查。

## 当前实验入口

- M1 已加入南京 Headscale，节点名 `m1-max-test`、尾网地址 `100.65.0.2`；关闭接受远端 DNS 和路由。`tailscale serve` 现把尾网 `100.65.0.2:15556` 转给 M1 测试网关 `192.168.9.125:15556`，本机从尾网地址读取 `/ping` 已返回 HTTP 200。手机已完成经腾讯云 NPS TCP/QUIC/KCP 和强制国内 DERP 的短时真实 YouTube 对照；证据与限制见下条，尚不代表异地 V50 或移动流量验收。
- 本轮 NPS 测量 JSON 位于 `docs/evidence/nps-transport-20260930/`；测试公共端口 `15557` 是隔离实例，不是正式 App 默认端口 `15556`。TCP、QUIC、KCP 结果应按相同路径、码率、视频内容和设备解码状态比较。
- M1 真实短视频/120 Hz 检查位于 `docs/evidence/live-20260930/`。这些样本不能直接代表 M5 或东北 V50 的长期表现。
- 当天的 App 工作树包含尚未发布的 1.22 改动；`git status` 和 `release-notes.md` 是判断本地状态的入口，不能只凭版本字样判断手机已经升级。

验证时先运行 `git diff --check`、Python 相关单测与 Android 构建；部署之后分别核对 M1/M5 服务、局域网和公网、手机端收到/显示帧率、触控及音画同步。用户的播放缓冲上限要求是 80 ms，不把 120 ms 当作默认推荐。

这台 M1 的 Homebrew JDK 21 和 Android SDK 不在系统默认 Java 路径；本目录的离线调试构建已用下列命令通过：

```sh
ANDROID_HOME=/Users/wyw/Library/Android/sdk \
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
PATH=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin:$PATH \
./gradlew :app:assembleDebug --offline --no-daemon
```

该 APK 使用调试签名，仅说明源码可以构建，不能覆盖安装现有正式签名 App，也不代表已经发布 1.22。
