# 实验 APK 的公网交付入口

这项交付让朋友下载实验包时无需登录私有 GitHub 或在 App 中配置 token。它不改变正式 `updates` 通道，也不把“能下载 APK”作为公网 UDP 媒体的验收。

## 独立路径

M1 的既有 HTTPS gateway 源码新增两类 GET 白名单：

- `https://146.56.249.175:15556/experimental/experiment.json`
- `https://146.56.249.175:15556/experimental/HuoguoAndroidExperiment-v1.31-alpha.1.apk`

APK 名只允许严格的数字版本加 `alpha.N`；`N` 必须为正整数。此阶段不接受 beta/rc、查询串、目录层级、编码后的路径穿越、任意文件名或原正式 APK 名。实验目录通过 `DIRECT_EXPERIMENTAL_DIR` 指定，缺省为 gateway `BASE/experimental`，正式 `DIRECT_UPDATE_DIR` 保持原样。

入口无需账号认证，因为只交付经过校验的签名安装包和不含秘密的元数据。读取时拒绝文件 symlink、设备和 FIFO，只接受有界普通文件；manifest 上限 64KiB，APK 上限 64MiB。回复含精确 Content-Length、`Cache-Control: no-cache` 和 `X-Content-Type-Options: nosniff`。manifest 原子替换，版本 APK 文件名不可复用为不同内容。

## 从既有私有 Release 同步

`scripts/pull_experimental_updates.py` 用现有 gh 授权，仅取给定 immutable tag 的 `experiment.json` 和 `HuoguoAndroidExperimental.apk`，在 owner-only 临时目录中完成下载、校验，再交付给 gateway。它不生成新凭据，不把 token 放入 APK/manifest，不读写正式 `updates` 目录。

部署者应明确给出既有 M1 状态目录或独立实验目录。下面是命令形式；`<既有M1状态目录>` 须使用该机现有 LaunchAgent 的 `DIRECT_STATE_DIR`，不能为了运行命令新建另一套服务状态。

```sh
DIRECT_STATE_DIR='<既有M1状态目录>' \
ANDROID_HOME="$HOME/Library/Android/sdk" \
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
python3 scripts/pull_experimental_updates.py \
  --tag experimental-v1.31-alpha.1
```

验证包括 GitHub Release 的 tag 和 `isPrerelease`、publisher 的完整 schema、独立包名、版本/tag、原签名身份、APK 字节数和 SHA-256、LAN UDP 的实验范围，以及本机 `aapt2`/`apksigner` 对实际 APK 的读回。它不接受正式 tag、不同包或签名、额外未定义 manifest 字段、伪装的下载目标、降级和同版本内容变化。

发布到公网的 manifest 保留原独立 Release 信息，只将 `apk_url` 改为本节 M1 公网固定入口。APK 先以原子操作落盘，manifest 最后原子替换。下载、摘要、签名、包名或版本校验失败时，原当前 manifest 保持不变；最后写入失败可能留下一个已经验证但尚未成为当前版本的新 APK，不会把残缺 manifest 提供给客户端。同步任务用非阻塞文件锁，拒绝重复任务竞争。

## 部署与验收边界

此记录描述源码和离线检查。将新的 gateway 入口装载到已有服务，需要先确认没有正式在线会话，再由 root 有范围重启原 gateway，随后读回公网 manifest 和完整 APK，核对尺寸、SHA-256、签名、包名及版本。新 reader 是显式单次同步脚本；没有在本任务中创建自动轮询、重启服务、下载 Release 或改动 NPS。

公网入口由 M1 的现有 15556 转发交付。它不修改国内来源过滤、Clash、NPC 身份或物理出口。M5 15558 的正式更新继续独立运行；未经单独部署和读回，不承诺 M5 也已提供实验下载。

首次安装实验 APK 后，它与正式 App 并存；实验内更新应只查询这个独立 manifest，并继续验证同包名、原签名、更高版本码、摘要和尺寸。首次下载、Android 安装确认、实验 App 成功启动，以及认证 UDP 媒体成功，属于不同的验收步骤。
