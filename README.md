# 给火锅的安卓

原生 Android 客户端，用手机直接控制 Apple Silicon Mac 上的 Android Emulator。视频为 H.264，音频为 AAC，触控直接发送到安卓；不依赖 Mac 桌面鼠标。当前版本 1.7。

## 照片图标与交互

使用用户提供的原始照片作为自适应桌面图标与圆形头像。底部按钮采用海盐薄荷、柔粉胶囊样式，可见高度 32dp，点击区域 48dp；原生触点波纹、80ms 按下微缩与 180ms 释放回弹，仅点击时播放。动画遵守系统关闭动画设置，触感反馈遵守系统开关；远程操作立即发送，不等待动效。

## 清晰度与连接

| 档位 | 竖屏视频尺寸 | 长边限制 |
|---|---|---|
| 流畅 | 432 × 960 | 960 |
| 540p | 540 × 1200 | 1200 |
| 720p（默认） | 720 × 1600 | 1600 |
| 1080p | 1080 × 2400 | 2400 |

尺寸基于 1080 × 2400 的虚拟安卓。更换清晰度后重新连接。最高 60 FPS、目标码率 2.5 Mbps，实际表现取决于编码、画面和网络。虚拟机的 CPU 和 GPU 加速不代表已使用 Mac 硬件视频编码：当前服务采用安卓内部软件 H.264 编码。

地址可填写 IP、主机名或带 `:15556` 的地址，随时修改，无需重新安装。App 记住地址、用户名和清晰度，不保存密码。多个账号共享同一台虚拟安卓；只允许一个活动会话。

## 编译

需要 Java 21、Android SDK Platform 37、Build Tools 36.0.0。配置本机 `ANDROID_HOME`，执行 `./gradlew assembleRelease`。独立开发安装会使用本机 debug 签名；正式升级必须使用原发布签名。

发布编译通过 `ANDROID_KEYSTORE_FILE`、`ANDROID_STORE_PASSWORD`、`ANDROID_KEY_ALIAS`、`ANDROID_KEY_PASSWORD` 读取签名材料，禁止把私钥放入 Git。

## App 内升级与私有 GitHub 发布

源码仓库为私有。正式升级包在本机签名，随后发布到私有 GitHub Release 和 `updates` 分支。M5 每 60 秒通过专用只读部署密钥同步该分支，将签名 APK 提供在已有的 TLS 串流服务中。App 不包含 GitHub Token。签名私钥不上传 GitHub。

客户端更新入口为 `https://146.56.249.175:15556/updates/update.json`，使用与串流相同的服务器公开证书校验。客户端每天在连接页自动检查一次，也可以点击标题旁的“检查更新”。下载完成后校验 SHA-256、包名、递增版本号和现有签名，再打开 Android 安装确认界面。首次需允许此 App 安装更新；安装需要用户确认。

以后更新：修改代码并递增 `app/build.gradle` 的 `versionCode` / `versionName`，更新 `release-notes.md`，在 `main` 分支提交，然后在配置了 Java 21、Android SDK 和原签名密钥的 Mac 上执行：

```sh
python3 scripts/publish_release.py
```

脚本本机编译、校验签名身份、推送源码并发布升级包。`updates` 分支只包含 APK 和更新元数据；源码工作区保持原分支。已发布的版本不可覆盖，必须增加版本号。若发布已成功但升级分支推送中断，执行 `python3 scripts/publish_release.py --resume`：它下载并验证已发布的原包，继续投递，不重新签名或覆盖版本。M5 同步程序拒绝降级或同版本内容变化，先写完整 APK，再原子更新清单；下载包若损坏，客户端拒绝安装。

GitHub Actions 的 `Android build verification` 只负责编译、lint 和地址解析检查；CI 的临时签名包不发布，发布密钥始终留在本机。

手机升级和 M5 服务端升级是两件事：新协议所需的服务端变更应先部署到 M5。此处自动同步只发布客户端 APK，避免未经测试替换运行中的服务端。

## M5 服务端

`gateway.py`、`lan_interfaces.py` 和官方 scrcpy 4.1 服务文件部署在 Mac。ADB 仅绑定回环地址。通过环境变量配置 `DIRECT_CERT`、`DIRECT_KEY`、`DIRECT_AUTH_FILE`、`DIRECT_AVD`（现有 AVD）、`DIRECT_MAX_SIZE=1600`、`DIRECT_INTERFACES` 等参数。请复用已有虚拟机及其 App 数据，不要重新创建或擦除 AVD。

`python3 add-user.py USERNAME --auth-file /private/path/auth.json` 以隐藏输入创建账号，密码仅保存为带随机盐的 scrypt 哈希。原单账号格式会自动迁移并保留旧账号。凭据、服务端私钥、NPS vkey 不属于源码。

NPS 可运行于 Mac，并将云端 TCP 端口映射到 Mac 回环的 `127.0.0.1:15556`。客户端到 Mac 保持 TLS。不能公开 ADB 端口。LAN 监听应限制到预期物理网卡；Clash 规则需要针对实际隧道服务器和本地网段设置 DIRECT。

`app/src/main/res/raw` 只有客户端用于验证服务器的公开证书，服务端私钥不在仓库中。换成自己的服务器时需要建立并更新相应信任关系。

## 验证与许可证

`tests/EndpointCheck.java` 检查地址输入。真实手机流畅度、硬件解码、移动网络连接和 Android 升级确认需要在实际手机验收；服务端或虚拟机测试不能代替真实手机结果。

scrcpy 服务遵循 Apache 2.0，见 `LICENSE.scrcpy`。本项目未另行授予其他源代码的开源使用许可。
