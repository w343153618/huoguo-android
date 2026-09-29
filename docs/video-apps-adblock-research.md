# 视频 App 与去广告社区方案核查

核查日期：2026-09-29。本文只记录官方资料、发布元数据和静态源码检查；安装、广告命中、视频播放、账号登录和冷启动验证由部署证据另行记录。

## 选型结论

- 红果优先使用 **FanqieHook v0.8.5**；完整 MIT Kotlin 源码、Modern libxposed API 102，作用域限定红果/番茄主进程。官方最新版红果目前比模块列出的已审计版本新，必须查看运行日志中 installed/skipped/lost 后再判断去广告覆盖范围。
- 通用广告域名阻断使用 **bindhosts v2.1.5**；本部署优先采用 mode 2 普通 bind mount，因为去广告不需要 root 隐藏，避免 mode 10 的默认 umount 让普通 App 看不到过滤规则。必须在普通 App 内验证 DNS 与播放。KernelSU 模块执行自己的 bind mount，不启动 VpnService。这样不会增加 VPN 路由或接管远程串流。只拦独立广告域名，不能推导同域视频广告已消除。
- YouTube 已有官方客户端；**NewPipe v0.29.1** 是开源轻量前端备选。安装成功不代表外网网络和平台接口一定可播放。
- 未找到同时满足“仍维护、完整源码可审计、当前 App 版本、Android 17/Vector API102、只去广告”的优酷/爱奇艺/腾讯视频专用模块，不能承诺三家视频插播全部消除。

## FanqieHook 审计

[官方仓库与 README](https://github.com/afwfv/FanqieHook)、[v0.8.5 官方发布](https://github.com/afwfv/FanqieHook/releases/tag/v0.8.5)。发布于 2026-09-20；包名 `dev.operit.fanqiehook`，versionCode 28，minSdk 26，targetSdk 35。

审查的是发布标签 `v0.8.5`，不是只检查默认分支：

- [Manifest](https://github.com/afwfv/FanqieHook/blob/v0.8.5/app/src/main/AndroidManifest.xml) 未声明 Android 权限、Activity、Service 或 Receiver。
- [构建文件](https://github.com/afwfv/FanqieHook/blob/v0.8.5/app/build.gradle.kts) 使用 `compileOnly io.github.libxposed:api:102.0.0` 与 DexKit 2.0.4；仅 ARM64 native 库，适合 Apple Silicon ARM64 AVD。
- [入口](https://github.com/afwfv/FanqieHook/blob/v0.8.5/app/src/main/java/dev/operit/fanqiehook/FanqieModule.kt) 限制 `com.phoenix.read` / `com.dragon.read` 主进程；版本仅作告警，不硬拒绝升级后的 App。状态日志落在宿主 `cache/fanqiehook.log`。
- [AdHooks](https://github.com/afwfv/FanqieHook/blob/v0.8.5/app/src/main/java/dev/operit/fanqiehook/hooks/AdHooks.kt) 的 `installVipEntranceHooks` 仅把 `canShowVipEntranceHere`、`canShowVipEntranceInAd` 返回 false 隐藏销售入口，没有修改 VIP 状态。主动激励广告也被屏蔽，相关奖励/金币入口会失效，不产生服务器确认的奖励。
- 对标签内全部 Kotlin 文件进行了网络 URL、网络客户端、进程执行、动态 ClassLoader、VIP 状态关键词检查，未发现模块自己的联网/命令执行/VIP 解锁逻辑。这是有限静态检查，不等于证明发布 APK 与源码完全可复现一致。

模块作者已验证红果 `7.3.7.32 (73732)`、`7.3.5.32 (73532)`；当前官网下载地址文件名为 `73932`，属于未列入已审计集合的新版本。应仅启用红果作用域，不给系统框架全局作用域。

官方 APK：<https://github.com/afwfv/FanqieHook/releases/download/v0.8.5/app-release.apk>

GitHub Release asset SHA-256：`ef70b7a907ce99a7d11196152681b52ebc4061f1c6ed0931a86f5d403b0d2d46`。

## bindhosts 与离线规则

[官方仓库](https://github.com/bindhosts/bindhosts)、[v2.1.5 发布](https://github.com/bindhosts/bindhosts/releases/tag/v2.1.5)、[模式文档](https://github.com/bindhosts/bindhosts/blob/v2.1.5/Documentation/modes.md)。发布于 2026-08-24。

官方 ZIP：<https://github.com/bindhosts/bindhosts/releases/download/v2.1.5/bindhosts.zip>

GitHub Release asset SHA-256：`1568c00bbaddf94a10f191b27c0fe68690375ed0c90cc409c095a53b195f6b13`。

[post-fs-data.sh](https://github.com/bindhosts/bindhosts/blob/v2.1.5/module/post-fs-data.sh) 检测 `ksud kernel umount` 能力后自动选择 mode 10；允许 `/data/adb/bindhosts/mode_override.sh` 内容 `mode=10` 固定模式，写入模块 `mode.sh` 的 `operating_mode=10`。[service.sh](https://github.com/bindhosts/bindhosts/blob/v2.1.5/module/service.sh) 自行把模块 hosts bind mount 到 `/system/etc/hosts`，再调用 `ksud kernel umount add /system/etc/hosts --flags 2` 并通知 module-mounted。因此它的 mode 10 不依赖把模块 system 目录交给 metamodule 自动挂载；其他依赖系统挂载的模块仍遵循 [KernelSU metamodule 文档](https://kernelsu.org/guide/metamodule.html)。

离线配置须依据 [实际处理代码](https://github.com/bindhosts/bindhosts/blob/v2.1.5/module/bindhosts.sh)，不能误认为 `--force-update` 可以空 sources：

- 持久配置目录 `/data/adb/bindhosts/`；`custom.txt` 是 `ip hostname` 格式，run 和 reset 都会保留。
- mode 10 挂载源 `/data/adb/modules/bindhosts/system/etc/hosts`。Mac 经 HTTPS 证书校验获取规则、解析/合并/去重、push 到临时文件后用 `cat` 写入此现有文件；已挂载后直接替换 inode 会让 bind mount 仍指向旧文件。
- `--force-update` 在 `sources.txt` 为空或所有下载失败时提前返回，不会处理 custom.txt。因此离线模式直接提供完整 hosts，并关闭 `sources.txt`、`sources_whitelist.txt`、cron（`--disable-cron`），不调用自动下载更新。
- [模块下载函数](https://github.com/bindhosts/bindhosts/blob/v2.1.5/module/bindhosts.sh) 在没有 curl 时降级到 `busybox wget --no-check-certificate`；本部署应让 Mac 获取并推送规则，避免这条降级路径。
- [customize.sh](https://github.com/bindhosts/bindhosts/blob/v2.1.5/module/customize.sh) 提示音量键选择额外 BindHosts-app；无需此 App，保持模块本身即可。
- 默认 sources 是 AdAway 与 r-a-y/mobile-hosts；可选 [AdAway 官方 hosts](https://adaway.org/hosts.txt) 与 [AWAvenue 官方国内广告 hosts](https://github.com/TG-Twilight/AWAvenue-Ads-Rule/blob/main/Filters/AWAvenue-Ads-Rule-hosts.txt)。只用官方原始源，不走镜像。

实际部署采用 `mode=2` 普通 bind mount：避免 mode10 注册的自动卸载让普通 App 看不到广告规则。现场验证的是独立普通 App（UID10246、没有 su）读取到7377条规则、广告域名解析0.0.0.0、正常视频域仍可HTTPS联网；这一结果比只读取root shell更有意义。root隐蔽性并非此次去广告的目标。

## 排除与限制

- [AdClose 发布仓库](https://github.com/Xposed-Modules-Repo/com.close.hook.ads) 只有 README/SUMMRAY，未提供实现源码；虽然提供 v4.3.2 APK 并宣称广告 SDK 过滤，不能归类为已完成源码审计的开源模块。本次不以该发布仓库证明安全或兼容。
- [KEJIYUNB/hongguo](https://github.com/KEJIYUNB/hongguo) README 明确包含 VIP 权益 Hook 解锁和缓存限制修改，超出用户本次去广告目标；不选。
- [AdAway](https://github.com/AdAway/AdAway) 是开源 root/VPN hosts 阻断器，但当前 GitHub release 仍 v6.1.4/2024-10-27，增加 VPN 或第二个 hosts 管理器对当前需求没有明显收益，先不叠加。
- 抖音/快手推荐流广告可能与正常视频来自同一域名；按域拦截容易一起阻断正常视频。先做通用规则和真实播放验收，不能用 DNS 测试通过代替视频广告验收。

## 官方应用来源

链接在本次现场从官方页面或官方前端配置追溯得到；下载后还应检查 APK 包名、版本、签名、SHA-256 并记录到安装证据。

| 应用 | 官方来源 | 获取地址与边界 |
|---|---|---|
| 红果短剧 | [官网](https://www.hongguoduanju.com/) SSR 配置 `android.link` | `https://novel8662.ugurl.cn/FMXby`，Android UA GET 跳转 APK；HEAD 会404，不能据此断言不可下载。Range GET 实测最终 CDN 为 `https://lf9-apk.ugapk.cn/package/apk/novelread/12267_73932/novelread_74639645a_v12267_73932_d587_1790318644.apk?v=1790318645`，响应206且内容为ZIP。 |
| 优酷 | [官方移动站](https://mobile.youku.com/) 的 APK 链接 | `https://youku-cpms-cdn.youku.com/apk/19babfbcea8e1838/latest.apk` |
| 爱奇艺 | [官方客户端下载页](https://app.iqiyi.com/mobile/player/) Android 链接 | `https://ota.iqiyi.com/2016061602.jsp`，现场 GET 收到约100MB APK/ZIP；文件名不是 .apk，不影响响应内容。 |
| 腾讯视频 | [官方客户端下载页](https://v.qq.com/download.html) JS 调用 [官方版本配置](https://cache.wuji.qq.com/x/api/wuji_cache/object?appid=vqqcom&schemaid=downloadpage_config&schemakey=5dbbd3491a7342ad9bd2ed9bc098484a&filter=isShow%3Dtrue) | `https://dldir1.qq.com/qqmi/aphone_p2p/aphone_agent_http/TencentVideo_V9.04.55.32321_20563.apk`，配置版本9.04.55，2026-09-21更新。 |
| NewPipe | [官方仓库](https://github.com/TeamNewPipe/NewPipe)、[v0.29.1 发布](https://github.com/TeamNewPipe/NewPipe/releases/tag/v0.29.1) | `https://github.com/TeamNewPipe/NewPipe/releases/download/v0.29.1/NewPipe_v0.29.1.apk`；发布2026-08-15；官方asset SHA256 `18447bfb1e06d113edc88df93f471827280de06f6f3d4dc42f56f28b9c1bab79`。 |
| TikTok | [官方支持页](https://support.tiktok.com/en/getting-started/creating-an-account/download-tiktok)、[官方下载页](https://www.tiktok.com/download) | 支持页说明提供 APK；当前研究访问下载页未得到可验证直链，不能因此改用未知 MOD APK。 |
| Netflix | [官方兼容性说明](https://help.netflix.com/en/node/57688) | 未获 Play Protect 认证的设备可能不兼容；安装 APK 不代表 root AVD 可播放 DRM 内容，本次不作此保证。 |

## 验收建议

保留安装前状态；每个模块只给必需作用域。依次确认模块加载、实际宿主 hook 安装统计、真实免费内容播放与滑动、无 FATAL/ANR、root/framework 冷启动后仍正常。需要对优酷/爱奇艺/腾讯的具体视频插播分别观察，不把广告域名0.0.0.0或App首页成功当作插播全部消除。真实一加手机的WAN播放与文件传输需独立验收。

VLC 本次从 [VideoLAN 官方 ARM64 下载目录](https://download.videolan.org/pub/videolan/vlc-android/last/) 获取3.7.1，并核对官方.sha256文件。它用于播放传入的本地照片/视频和支持的网络流，不把它当作收费视频平台的替代。
