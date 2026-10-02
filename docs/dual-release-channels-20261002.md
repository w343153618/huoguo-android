# 正式与实验双发布通道

用户要求让已验证的正式版继续更新，同时把开发中的候选独立推送。双通道按 APK 身份、源码分支、tag 和更新元数据隔离。

| 项目 | 正式通道 | 实验通道 |
| --- | --- | --- |
| Android 包名 | `local.remoteandroid.direct` | `local.remoteandroid.direct.experiment` |
| 桌面名称 | 给火锅的安卓 | 明确包含“实验”的独立名称 |
| 源码发布分支 | `main` | `codex/experimental-*` |
| GitHub tag | `v1.31` 等正式版本 | `experimental-v1.31-alpha.1` 等候选 |
| GitHub Release | 正式发布 | `prerelease`，不成为 `latest` |
| 元数据 | `updates` 分支的 `update.json` | 实验 Release 独立资产 `experiment.json` |
| APK 交付 | 正式 App 的既有“检查更新” | 首次独立安装，后续“检查实验更新”使用独立清单 |
| 当前媒体边界 | 已发布的 TLS/TCP | LAN/已登记Tailnet认证UDP候选；未宣称公网UDP完成 |

两个包可同时安装。安装或删除实验包不会替换正式 App，也不会迁移其账号、密码或设置；测试时仍使用既有服务账号。它们使用原发布签名证书，但包名不同，所以 Android 分别保留数据。实验包升级必须继续使用同一包名、原证书和更高的实验版本码。

## 实验发布脚本

`scripts/publish_experimental_release.py` 默认只构建、检查和冻结候选。原正式脚本 `scripts/publish_release.py` 及 `scripts/pull_updates.py` 不参与实验发布。

先从已提交的候选建立／切换到 `codex/experimental-udp` 等分支。脚本要求 tracked 文件干净、`origin` 与目标仓库一致；遗留的未跟踪实验数据不会因为发布而被删除或加入 Git。

使用既有 SDK、Java 和本机签名身份。输出目录必须是源码目录之外一个尚不存在的目录，APK 不能进入源码或元数据 Git 树。示例外部路径 `/private/tmp/huoguo-experimental-1.31-alpha.1` 仅存放本次冻结产物，不是新服务状态目录。

```sh
ANDROID_HOME="$HOME/Library/Android/sdk" \
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
python3 scripts/publish_experimental_release.py \
  --version-name 1.31-alpha.1 \
  --version-code 32 \
  --notes-file docs/experimental-release-notes-1.31-alpha.1.md \
  --output-dir /private/tmp/huoguo-experimental-1.31-alpha.1 \
  --dry-run
```

实验版本覆盖仅接受 `authenticatedLanUdp=true` 加独立包名。脚本还会对实际 APK 检查：

1. `aapt2` 返回的包名、版本名、版本码和包含“实验”的桌面名称。
2. `apksigner` 返回的唯一签名证书必须是既有发布身份，SHA-256 为 `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`。
3. APK 中确有认证 UDP UI 和仅 arm64 的 UDP FEC 原生库。
4. 冻结的 APK 尺寸和 SHA-256 与独立 `experiment.json`、`SHA256SUMS.txt` 一致。

输出文件权限为 owner-only。脚本只用现有本机签名材料和 gh 授权，不生成新密钥，不读取或复制账号、NPC vkey 或私钥到产物。

正式发布前，root 仍须在独立实验包上做真实手机安装、启动、认证及本版约定的验收。编译、lint、离线发布边界检查不能代替真机或公网媒体验收。

## 显式发布

核对候选后，以一个新的输出目录重跑命令并把 `--dry-run` 换成 `--publish`。`--apk` 可以指定已经构建的候选；它验证实际 APK 身份与内容边界，但不独立证明该 APK 对应哪个源码提交，需结合构建冻结记录。

发布脚本推送当前实验分支、创建独立 immutable tag，再以 `--prerelease --latest=false --verify-tag` 发布三项 GitHub 资产：

- `HuoguoAndroidExperimental.apk`
- `experiment.json`
- `SHA256SUMS.txt`

发布说明也先冻结到同一受限目录，再用于 Release。脚本没有 `--force`、`--clobber` 或覆盖既有 Release 的退路；发现 tag、Release 已存在或无法确认不存在就停止。中途失败可能留下已推送的实验分支／tag，必须读回检查后处理，不能重打原 tag 来掩盖失败。

## 安装和升级的实际边界

实验 UI 现在显示实际安装版本及独立通道，提供“检查实验更新”。它复用同包名、原签名、递增版本、摘要与尺寸验证，但只查询 `/experimental/experiment.json`，不读正式清单。首次独立安装、权限恢复、后续升级与媒体成功属于不同验收。当前新版已在一加12上完成独立安装和登录页版本文字读回；实验公网交付与实际一键升级仍需发布后完整实读。

APK 中不能放 GitHub token；私有仓库 Release 默认需要已授权的 GitHub 访问才能下载。`scripts/pull_experimental_updates.py` 及 gateway 中独立白名单交付的源码与离线检查已完成，部署方式和未完成边界见 [交付说明](experimental-public-delivery-20261002.md)。未经部署实读，不将私有 Release 链接描述成朋友无需登录即可下载。

正式 App 的“检查更新”继续只读取正式通道；实验候选绝不写入 `updates`。认证LAN UDP仍严格限制RFC1918同网段；Tailnet是单独的精确M1与已登记测试手机范围，验证实际kernel utun、当前控制域与peer身份，不把CGN网段普遍放行。发布实验 APK 不会自动启动候选网关，不会替换NPS，不代表公网UDP/P2P完成。
