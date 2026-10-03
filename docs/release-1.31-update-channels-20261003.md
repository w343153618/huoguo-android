# v1.31 正式版更新通道回移

本次从 `origin/main` 的正式版基础上回移更新通道选择，正式包名为 `local.remoteandroid.direct`，版本为 `1.31`／`32`。实验包 `local.remoteandroid.direct.experiment` 独立安装，正式版不包含新的 UDP 入口或实验媒体代码。

手动“检查更新”显示通道选择，分别读取正式版和实验版的 HTTPS 清单。自动检查只查询当前包所属通道。更新前展示更新内容并由用户确认，取消不会启动安装。判断另一通道是否需要更新时，读取该包的已安装版本，而不是当前包的版本号。

下载和恢复安装均重新核对通道、包名、版本、大小、SHA-256 和既有发行签名。清单只使用 HTTPS，APK 的最终包名、版本和签名仍须从实际安装包独立读取。Manifest 仅声明两个包名的查询权限，不申请枚举全部安装包的权限。

更新任务由进程内共享的操作门控制。Activity 销毁时关闭其 updater，取消旧任务并防止旧页面继续弹出安装确认。该门只处理更新生命周期，不调整视频、音频、触控或现有连接参数。

## 验证边界

2026-10-03 本地回移验证完成：更新策略／操作门共 15 项生产 Java 源码测试通过；现有更新交付的 12 项离线回归通过；`:app:assembleRelease :app:lintRelease` 成功。Lint 为 0 项错误／53 项警告，涉及无障碍、图标、固定方向、文本国际化、API 与构建工具提示；尚未逐项做旧版本警告数量对照，也未把这些检查视为真实手机验收。

最终正式 APK 读取到包名 `local.remoteandroid.direct`、版本 `1.31`／`32`，唯一签名 SHA-256 为 `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`，与既有发行身份一致。独立读取 DEX 定义确认包含更新器、通道策略和操作门，不包含 `UdpVideoProbe`、`AuthenticatedLanUdpUi`、`LanUdpContract`、`NativeUdpFec` 或 `CodecStartupGate` 实现；没有打包 native 库。生成的 BuildConfig 为 `AUTHENTICATED_LAN_UDP=false`／`RELEASE_CHANNEL="formal"`。

这些结果属于源码和安装包验证，不能代替真实手机升级安装验收，也不支持任何新的流畅度、FPS 或音画延时结论。APK 的最终摘要与大小由发布清单独立记录，构建产物留在忽略的 `app/build/outputs/apk/release/` 或独立私有发布准备目录。

发布仍由 `scripts/publish_release.py` 的正式版流程完成；签名文件、APK 和清单构建产物不提交 Git。现有 M1／M5 服务、账号、NPC 身份和国内物理出口不在此次回移范围。
