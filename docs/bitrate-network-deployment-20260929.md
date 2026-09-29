# v1.9 码率、应用与出口部署记录

2026-09-29，目标 M5 有线 192.168.9.99，用户 yawen，AVD phone17-root / Android17 API37。

## 已完成与验证

- Native App v1.9/code10 五档目标码率 2.5/4/6/8/12Mbps，加自定义0.5–12Mbps；Java BigDecimal 精确解析，不静默四舍五入越界。记住设置，下次重连生效。
- Android 实测 c2.android.avc.encoder / OMX.google.h264.encoder 的 bitrateRange [1,12000000]、hardware=false。M5 CPU/GPU 加速仍不等同于 Mac 硬件视频编码。
- 服务端兼容旧客户端默认2.5Mbps；拒绝布尔值/字符串/非整数/范围外请求，在动现有session之前检查。
- 实际 LAN 4Mbps、公共NPS 3.75Mbps/12Mbps：H264通道收到720×1600 metadata、codec config与首帧；无效码率HTTP400。此项不代表一加15实际持续播放FPS/延迟验收。
- Native Android17 UI实际查看档位；选择自定义后显示第四个输入框，13Mbps连接前报错，5.5小数可输入。App构建和lintRelease通过。
- NPS物理保护部署：NPC server_addr改成本机127.0.0.1:18024，固定目标relay使用IP_BOUND_IF绑定en11/en0，仅IPv4，无DNS/普通未绑定socket/代理fallback。测试en11/en0绑定连接成功，utun5不可用；缺失接口拒绝。NPS活动客户端1466实时来源保持家庭国内公网，M5活动连接local192.168.9.99，经en11。
- Clash TUN保持开启，真实route-exclude含NPS /32，持久Script.js已有DIRECT与排除规则。公开更新服务LAN/WAN均200，端口仍15556。NPS客户端目标与VM网页出口分开。
- M1只读副本 -vmnet-bridged en0 失败：cannot create vmnet interface: general failure (possibly not enough privileges)。该进程退出，未改M5启动参数，也没有桥接成功/独立LAN IP的声称。
- M5磁盘有约841Gi空闲；AVD /data容量63G，已用6.8G、剩56G，无扩容必要。镜像qcow2虚拟64GiB，guest ext4在dm-5；新增三款App后/data已用约10G、余53G。若未来需要扩容，应离线备份并核对加密映射和文件系统后处理，不只改config.ini。

- 剪映第一次启动检查遇到ADB传输不响应，重连仍offline。按原vm17 LaunchAgent无wipe冷重启，Android17/API37/1080×2400、已装应用保留；KernelSU32601/LKM、Vector2.2与bindhosts mode2仍在，临时测试客户端已卸载。随后剪映真实MainActivity/进程可见，有限崩溃日志无FATAL；持续编辑/导出尚未测试。恢复后LAN与NPS串流重新验证。

## 应用与去广告的边界

原版新增汽水21.1.0、抖音极速版40.6.0、剪映21.6.1。APK来源、摘要、ARM64和证书另见安装证据；基本启动与长期播放/平台登录/VIP并不是同一验收。喜番应用宝3.9.4.4包只有armeabi-v7a，INSTALL_FAILED_NO_MATCHING_ABIS；寻找64位发行中，不能宣称安装完成。

现有FanqieHook0.8.5仅勾选红果7.3.9.32，30hooks安装且已有免费短剧实际播放；bindhosts2.1.5 mode2使用7377域名规则，正常App UID见到过滤。不能保证去掉与视频同域的插播、全部促销入口或奖励广告收益。

汽水/剪映所谓VIP候选未提供足够实现源码审计，未安装。红果综合模块源码完整但只列到7.3.3.18，比生产7.3.9.32旧；不和现有FanqieHook并用、不为了模块降级现有App。修改本地会员标志不等于服务器账号正式会员或取得付费/DRM内容。

## 仍需手机验收

手机更新到v1.9并安装确认后，在国内移动网络测试720p/4Mbps以及540p/2.5Mbps，观察本App接收/显示FPS、滑动、声音与文件互传。朋友如开启VPN，需对本App/NPS地址直连；M5物理绑定不能替朋友控制其手机出口。云厂商风控/条款判定仍不能作绝对承诺。
