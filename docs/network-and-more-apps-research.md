# 新增影音应用、模块与 macOS 桥接研究

研究日期：2026-09-29。本文是源码、官方接口与本地 SDK 的只读研究，没有变更 M5 网络、没有安装新模块。安装、真实 Android DHCP、手机播放与 WAN 体验由部署阶段另行验收。

## 结论与当前需求

Android 仍需要境外访问能力。硬性要求是 NPC 到 NPS 走家里的物理出口，不经过境外代理；爱快自身没有境外代理能力。桥接是否有价值，应按这两个独立目标判断。

当前 Apple Silicon SDK **具备 vmnet 桥接后端和 Android wrapper 原生参数**，不能笼统断言 Google Emulator 只支持 NAT。优先在 M1 的副本 AVD 测试 `-vmnet-bridged <有线接口名>`；成功后再迁移 M5。编译支持已证实；父任务随后在 M1 副本实测普通用户启动失败，日志为 cannot create vmnet interface / general failure / not enough privileges，尚未通过 DHCP。M5 sudo 当前不可用。本轮不强改桥接，保留 NAT，按用户澄清只对 NPC 强制物理出口。

新增四款 App 的原版来源已定位；喜番应用宝包已由父任务实测只含 32 位 native 库，ARM64-only Android 17 安装失败；汽水来自开发者官网，其余三款来自腾讯官方应用宝目录和 CDN。包名/ABI/证书须下载后核对，网页元数据不能替代 APK 解析或签名验证。可审计的去广告模块仍以 bindhosts、FanqieHook 为主。红果综合模块有完整源码，但适配版本滞后且功能更多，适合副本评估。未找到适配当前四款新 App、源码完整且经过本项目验证的通用 VIP 模块。

## 官方原版 APK 来源

| 应用 | 公开来源核实的包名 | 当前版本 | 下载入口与核对数据 |
| --- | --- | --- | --- |
| 汽水音乐 | `com.luna.music`（待 APK manifest 最终核对） | 21.1.0 / 100211030 | [开发者官网](https://www.qishui.com/download)、[官方跳转](https://ugapk.com/GMg3)、[官方合规接口](https://safe.usergrowth.com.cn/safe?Token=GMg3) |
| 喜番免费短剧（此包 32 位，当前不可安装） | `com.kwai.theater` | 3.9.4.4 | [腾讯官方应用宝](https://sj.qq.com/appdetail/com.kwai.theater)，94986927 字节，MD5 `8D36D40FCD590370DB85F93427516341` |
| 剪映 | `com.lemon.lv` | 21.6.1 | [腾讯官方应用宝](https://sj.qq.com/appdetail/com.lemon.lv)，469187961 字节，MD5 `56F95060E0F0F91D3514B6D03F3332F6` |
| 抖音极速版 | `com.ss.android.ugc.aweme.lite` | 40.6.0 | [腾讯官方应用宝](https://sj.qq.com/appdetail/com.ss.android.ugc.aweme.lite)，206845895 字节，MD5 `A83A361ADA3ED2AAB458605A84B3F818` |

已验证的 URL：

- 汽水：`https://lf9-apk.ugapk.cn/package/apk/luna/8986_100211030/luna_43074760a_v8986_100211030_f1be_1789985359.apk?v=1789985404`
- 喜番：`https://imtt.dd.qq.com/16891/apk/8D36D40FCD590370DB85F93427516341.apk?fsname=com.kwai.theater_3.9.4.4.apk`
- 剪映：`https://imtt.dd.qq.com/16891/apk/56F95060E0F0F91D3514B6D03F3332F6.apk?fsname=com.lemon.lv_21.6.1.apk`
- 抖音极速版：`https://imtt.dd.qq.com/16891/apk/A83A361ADA3ED2AAB458605A84B3F818.apk?fsname=com.ss.android.ugc.aweme.lite_40.6.0.apk`

汽水入口由官网当前 JS `index-4d21fa75.js` 中 `Hr="https://ugapk.com/GMg3"` 追溯，合规接口标注北京抖音科技有限公司，更新 2026-09-21。三款应用宝 URL 按详情页 `__NEXT_DATA__` 公布的 MD5 定位；逐一 GET `Range: bytes=0-0` 返回 206、APK Content-Type、总字节数与目录一致。腾讯 CDN 会跳转到带短效参数的 `rdt.tfogc.com`，应保留上述原始 imtt 链接，勿保存单次短效重定向作为长期入口。

抖音自己的 [极速版下载页](https://m.douyin.com/lite_app_download) 当前 SSR 指向 `ugurl.cn/dfqdL` → `ugapk.com/dBDf8`；其合规资料仍是 2024 年 16.6.0，实际 GET 显示无当前系统有效下载地址，因此不采用这个旧 token。剪映 [官网](https://www.capcut.cn/) 当前主要提供 PC 下载引导，`/mobile` 返回 404 内容；魅族官方商店目录可读，但其网页下载 API 返回 404。这里没有拿软件下载站的“官方版”宣称代替真实来源。

安装门禁：完整下载后计算 SHA-256 用于部署记录；应用宝三款额外比对官方 MD5。用 SDK `aapt2 dump badging` 或 `apkanalyzer manifest application-id` 核对包名/版本，检查 ZIP 中 `lib/arm64-v8a`；用 `apksigner verify --verbose --print-certs` 验证 APK 签名与证书指纹，并和已有官方安装/同渠道后续升级证书对照。本研究没有完整下载这四个文件，签名证书摘要由父任务安装验证补齐。**父任务已实测喜番应用宝 3.9.4.4 / MD5 8D36D40FCD590370DB85F93427516341 只含 32 位 native 库，M5 Android 17 ARM64-only 返回 INSTALL_FAILED_NO_MATCHING_ABIS。此来源正确，但不兼容当前镜像，不能安装或宣称已完成。**

进一步查 [小米官方喜番目录](https://app.mi.com/details?id=com.kwai.theater) 和 [移动目录](https://m.app.mi.com/details?id=com.kwai.theater)，当前均列 3.9.4.4（2026-09-22）；桌面按钮为维护中，移动下载需官方验证 token，直接入口重定向回详情，未获取可检查 ARM64 的 APK。华为官方网页脚本提供公开详情 API，但此次请求返回 403；魅族 API 返回 404；快手 `/xifan` 重定向 404。本轮**没有找到并验证官方 ARM64 喜番包**，这是待完成缺口，不能从这些失败推断厂商永远没有 64 位版。第三方站的 universal 标签不证明包含 arm64-v8a，未采用非官方改包，也未给当前系统拼接未经审查的 32 位兼容运行库。

### 喜番原版镜像有限补查

本轮按现有安装授权有限检查原版镜像。APKPure 的近期版本页面仅标注 universal，这不是 ZIP 中存在 arm64-v8a 的证据；APKMirror 未找到喜番有效条目。

[PGYER APKHub 喜番页面](https://www.pgyer.com/apk/apk/com.kwai.theater/download) 标注 1.2.4.0（2024-06-11）、arm64-v8a，页面下载链接为 `https://storage.appmeme.com/com.kwai.theater--1000126.apk`。这是**尚未下载验证、且明显过旧的候选**；页面标签不能代替 APK 实查。必须先验 `apksigner verify --print-certs` 成功，证书 SHA-256 与已验证腾讯官方 32 位包精确一致：`11932a20288420b0bfe4b38815745eb41d62f023710f777ce9cf833e62aa925a`；再检查真正的 `lib/arm64-v8a/*.so` 和 Manifest 版本/包名。任一不符即不安装。不使用改签纯净版、会员版或证书来源不明的 APK。

父任务尝试获取上述候选返回 HTTP403，未取得可验 APK。旧版即使签名与 ABI 验证通过，平台可能要求强制升级，尚不能视为当前可用方案。本轮没有验证成功的近期 ARM64 喜番，研究到此结束，避免继续无边界搜包。

## 去广告与本地会员候选审计

### 已有小范围方案

bindhosts v2.1.5、FanqieHook v0.8.5 和 NewPipe 的源代码审计、下载摘要见 [此前研究](video-apps-adblock-research.md)。bindhosts 使用 mode2 普通 bind，规则由 Mac 验证 TLS 后推入；域名阻断可能影响登录/奖励/播放，应在普通 App 进程验证 DNS 和实际播放。hosts 无法区分同域名里的广告与视频正文，不能保证去掉平台服务端插播。

### 红果综合模块：源码完整，但当前目标 App 版本未适配

[KEJIYUNB/hongguo](https://github.com/KEJIYUNB/hongguo) 采用 GPL-3.0，包名 `xyz.kejiyu.hongguo`，现代 libxposed API 102。官方 [v1.0.1 发行](https://github.com/KEJIYUNB/hongguo/releases/tag/1.0.1) 发布于 2026-08-12；APK 为 `https://github.com/KEJIYUNB/hongguo/releases/download/1.0.1/app-release.apk`，GitHub 官方 asset digest 为 SHA-256 `90a95fd0f74ec5f61daa8406cd542cbc454c7fa3fed8c395d48a8c32cc0f689c`。

实际审查了 Manifest、MainHook、UpdateChecker、Hooks、TargetNames 和 Gradle：

- [Manifest](https://github.com/KEJIYUNB/hongguo/blob/main/app/src/main/AndroidManifest.xml) 声明 INTERNET、FOREGROUND_SERVICE_DATA_SYNC；未见无障碍/设备管理员/短信权限。注入代码仍能使用宿主权限，因此不能把模块自己权限少理解为绝对隔离。
- [入口](https://github.com/KEJIYUNB/hongguo/blob/main/app/src/main/kotlin/xyz/kejiyu/hongguo/MainHook.kt) 限制红果国内/海外包及自身演示包，跳过 WebView 沙箱/renderer。源码未发现 Runtime.exec、ProcessBuilder、远端代码下载执行或 native 动态库加载。
- [更新器](https://github.com/KEJIYUNB/hongguo/blob/main/app/src/main/kotlin/xyz/kejiyu/hongguo/UpdateChecker.kt) 在 module load 自动 HTTP GET 官方 GitHub `releases/latest`，检查重定向/网页版本；用户点击才打开浏览器去更新。可关闭/删除这项自动检查后自建审计版本，避免宿主启动外联。
- [版本表](https://github.com/KEJIYUNB/hongguo/blob/main/app/src/main/kotlin/xyz/kejiyu/hongguo/hooks/TargetNames.kt) 仅列国内 7.3.1.32 / 7.3.2.32 / 7.3.3.18，海外 7.3.1.32。当前官方 7.3.9.32 不在表内；fallback 选择旧映射不是适配成功证据。
- [Hooks](https://github.com/KEJIYUNB/hongguo/blob/main/app/src/main/kotlin/xyz/kejiyu/hongguo/hooks/Hooks.kt) 广告与 VIP 开关默认 false。VIP 部分改变本地 PrivilegeManager/isVip、NsVipImpl、VipInfoModel/KMP 对象，构造远期日期；这是本地状态/界面和客户端广告判断，不是给平台账号充值会员，也未见可证明取得服务器付费权益或 DRM 密钥的实现。下载数量修改同样只改客户端限制，服务器限制仍可能生效。

建议保留为副本候选，先单独测试界面清理。不要和 FanqieHook 同时作用于红果，避免相互拦截；不要为了模块适配降级覆盖带私人数据的现有 App，也不能把“模块已加载”报告成所有功能通过。

### 汽水、剪映、喜番与抖音极速版

[汽水辅助模块 me.bingyue.fuckqishui](https://github.com/Xposed-Modules-Repo/me.bingyue.fuckqishui) 自述本地会员/去广告且不解锁服务器功能，但公开树只有 README 和 SUMMARY，没有实现源码、可审计权限或网络行为。所谓全版本通杀也没有 Android 17 + 汽水 21.1.0 的本项目验证证据。按当前“免费、开源、可审计”条件不推荐安装未知二进制。

[Deer God com.wengui.hook 发行页](https://github.com/Xposed-Modules-Repo/com.wengui.hook/releases) 提到剪映模板无网络修复、通用广告拦截和其他本地会员功能；Xposed 模块目录上的发行描述不等于完整实现开源。HookVip/AdClose 也不能仅因 GitHub 有目录就当成源码已审计。未发现喜番、剪映、抖音极速版当前版本可直接推荐的完整源码专用模块。先安装原版、使用已有系统域名规则并观察，遇到明确广告入口再做针对当前版本的小范围可复核处理。

## Android Emulator 37.1 ARM64 的真实桥接支持

### 本地发行物证据

实际读取本机 Google SDK：

```text
Android emulator version 37.1.11.0 (build_id 15917651)
/Users/wyw/Library/Android/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64
SHA256 eaa97a970b81f81640db73ed79e19ee173323653a0a1203217e1199d078011f3
```

`emulator -help-all` 明确列出 `-vmnet-bridged <host network interface>` 和 `-vmnet-shared`；`otool -L` 显示 Apple `vmnet.framework`；`strings` 同时发现 `vmnet-bridged,id=mynet,ifname=%s%s`、`vmnet-bridged,id=virtio-wifi,ifname=%s%s` 及 buildbot `emu-37-1-release/net/vmnet-bridged.m`。因此本机发行物确实编入该网络后端，并非只在上游 QEMU 存在。

### Android wrapper 与 NIC 路径

Google 官方 [main.cpp 源码](https://android.googlesource.com/platform/external/qemu/+/emu-master-dev/android-qemu2-glue/main.cpp) 的 Apple ARM64 分支直接把 `opts->vmnet_bridged` 接到现有 `mynet` 和 `virtio-wifi` netdev。`net-tap/net-socket`、`wifi-tap/wifi-socket` 优先级更高，不能同时指定，否则覆盖 vmnet 路径。ARM64 现有 `virtio-net-device` 与启用 VirtioWifi 时的 `virtio-wifi-pci` 继续绑定这些后端；不需要用通用 `-nic` 另创建一块 guest 未配置的新 NIC。Google [引入后端的源码 diff](https://android.googlesource.com/platform/external/qemu/+/d446eb2c8d5205870c10c12190b938bff4a16820%5E1..d446eb2c8d5205870c10c12190b938bff4a16820/) 也明确此参数接入。

研究期间 Gitiles 原始完整文本 GET 返回 503；以上源码分支来自官方页面可检索片段，并用本地 37.1.11 帮助/链接/字串交叉核对。未把 emu-master-dev 当成精确发行 commit，精确 M5 二进制与运行日志仍须部署阶段记录。

副本试验启动形式：保留已验证的 AVD/kernel/root/GPU 参数，仅加 `-vmnet-bridged enX -no-snapshot`，独立 console port，避免影响 emulator-5554。`enX` 必须用 `networksetup -listallhardwareports` 查真实有线网卡，本项目 M5 父任务只读报告为 en11，不假定其他机器一致。

### 权限、接口与验收边界

Apple [vmnet 框架](https://developer.apple.com/documentation/vmnet) 和 [com.apple.vm.networking entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.vm.networking) 对权限有要求。SDK 当前 `codesign -d --entitlements :-` 返回 invalid entitlements blob，所以不能假定普通用户 vmnet API 一定成功。先读实际错误；父任务随后在 M1 副本普通用户实测出现 general failure / not enough privileges，未获取 DHCP。这表明本机运行权限是当前真实阻碍；M5 sudo 当前不可用，不继续强改。若后续授权条件满足，副本测试需经过核实的 vmnet 授权/root 运行条件，不擅自给正式 SDK 加 setuid 或替换其签名。root 运行 AVD 还需明确 AVD_HOME/SDK 路径，避免因 HOME 改变找不到 AVD 或把主用户文件变成 root 所有。

QEMU 官方 [vmnet-bridged.m](https://github.com/qemu/qemu/blob/master/net/vmnet-bridged.m) 使用 `vmnet_copy_shared_interface_list` 验证 ifname，指定 `VMNET_BRIDGED_MODE` 和 `vmnet_shared_interface_name_key`；错误会列可用接口。Apple [bridge 枚举](https://developer.apple.com/documentation/vmnet/operating_modes_t/vmnet_bridged_mode) 与 [官方技术支持说明](https://developer.apple.com/forums/thread/822025) 区分共享 DHCP 和物理桥接：桥接 guest 的 DHCP 不是 vmnet 内置 DHCP。

成功判据：guest 从爱快取得单独 LAN IP/默认网关/DNS；爱快租约表出现 VM MAC；Android 到内网、互联网均可用；Mac TUN 保持开启仍不影响 guest 直连；镜像原有 KernelSU、Zygisk、Vector 正常；ADB 仍只经本机连接，不能因为 LAN IP 暴露 5555/root shell。vmnet 桥接不会自动改变串流服务监听或 NPC 进程出站。桥接的 Android 要访问境外，可显式使用经过授权的代理入口或 Android 自己的代理配置，国内与 NPC 规则单独 DIRECT；爱快无境外出口时，裸桥接不会凭空带来境外连通。

## Clash/Mihomo 与 NPC 固定物理出口

[Mihomo 规则](https://wiki.metacubex.one/config/rules/) 支持 PROCESS-NAME/PROCESS-PATH。`qemu-system-aarch64` 或 `npc` 设 DIRECT 只表示 Mihomo 接管后使用直连出站；若系统路由仍进 TUN，就不能称完全绕开 TUN。Android 内 App 包名不会自动变成 macOS 的进程名。

[TUN 参数](https://wiki.metacubex.one/config/inbound/tun/) 的 `route-exclude-address` 可按目标 CIDR 排除系统 TUN 路由，影响所有 Mac 进程到该目标，非专属 guest。include/exclude UID、package 的平台限制不能照搬到 macOS。fake-IP 场景需要同时规划 DNS，避免绕过 TUN 的连接得到 198.18/15 虚拟地址却没有还原通道。单纯给 emulator DIRECT 不能提供 Android 独立 LAN IP，这属于 NAT 路径优化。

对固定 NPS `146.56.249.175`，可把 `146.56.249.175/32` 加入 TUN route-exclude-address，并将目标 IP 和 NPC 进程规则放在规则顶部；同时维护主机路由经物理接口/网关。Mihomo [DIRECT 出站](https://wiki.metacubex.one/config/proxies/direct/) 可通过命名 `type: direct` 出站的 `interface-name` 明确有线接口，此方式仍是 Mihomo 直连，和系统 route exclusion 层次不同。配置应持久加入真实使用的合并/覆写文件，避免订阅刷新覆盖。

父任务现有只读证据是 `route get 146.56.249.175` → en11 / 192.168.9.1；这证明内核路由查询，仍需用活动 NPC TCP peer、物理接口抓包与 NPS 端观察源公网地址确认连接实际走南京家庭出口。父任务随后部署了只监听 localhost、只转发固定 NPS 目标的 relay，通过 macOS IP_BOUND_IF 将出站绑定 en11，并保留 en0 作为明确受控的物理回退；NPS 实际观察客户端为家庭国内公网，服务端读回同一来源；完整隐私证据只留本地，Clash TUN 保持开启。此为父任务提供的实际连接证据，本研究未自行执行远端变更。它是 NAT + NPC 固定物理出站，不是 Android 桥接，也不禁止 Android 使用境外代理。只查看 generic curl/ip 网站不证明 NPC 流量路径。若 NPC 用纯 IP 连接不会涉及 fake-IP 解析，但域名的后备地址与升级地址应另查。

### NPS IP 的归属核实

[APNIC 官方 RDAP](https://rdap.apnic.net/ip/146.56.249.175) 返回 `TENCENT-CN`、CN，范围 146.56.192.0–146.56.255.255，实体 Tencent Cloud Computing (Beijing) Co., Ltd。[RIPE 官方 network-info](https://stat.ripe.net/data/network-info/data.json?resource=146.56.249.175) 本次返回 AS45090 / 146.56.224.0/19。因此此 IP 属于腾讯公告网络。附近 Oracle 的 146.56.128.0/18 不包含本地址，不能将整个 /16 判为 Oracle。注册国别不能证明机房城市：南京物理出口是 NPC 源路径要求，NPS 所在城市仍由控制台区域、服务器信息与路由测量确认。

## 待部署验证

1. 四款 APK 完整下载、摘要、包名、ARM64 ABI、签名、原版安装与基本启动；喜番现有包明确失败，仍缺官方 ARM64 包；登录与会员状态由实际平台账号决定。
2. 桥接在副本 AVD 的权限、DHCP、网关/DNS、root 模块及 loopback ADB；失败保留原 NAT，不影响正式串流。
3. NPC 活动连接物理接口与 NPS 源 IP；Clash 开/关前后都维持同一国内出口。
4. 普通 App 广告拦截/奖励/登录/视频与音乐播放测试，给出具体 App 版本和命中日志；未验证的功能保持候选状态。
