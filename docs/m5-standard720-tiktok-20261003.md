# M5 标准 720P 与 TikTok 安装状态

2026-10-03，按用户明确要求将火锅日常使用的 M5 虚拟安卓固定为物理 **720×1280**。源码以本工程和 GitHub 正式 `main` 的清晰度定义为准，M1 机主实验的 1080×1920 未改变。

## 屏幕与 App 档位一致

正式与实验分支的 `StreamQuality.java` 均把清晰度定义为长边上限，标签注明 16:9 时的尺寸。不是把 540、720 当成竖屏的高度。M5 本轮实际读回如下：

| App 选择 | 请求长边上限 | M5 实际 H.264 尺寸 | 苹果硬件编码 ready |
|---|---:|---|---|
| 540P | 960 | 540×960 | 是 |
| 720P | 1280 | 720×1280 | 是 |
| 1080P | 1920 | 720×1280 | 是；源尺寸限制，不上采样 |

物理 AVD 是 720×1280、density320，`wm size` 和 `wm density` 没有覆盖值。选择 540P 只降低传输画面尺寸，不改变虚拟安卓的物理屏幕；选择 720P 使用完整源分辨率。M5 固定为 720P 后，1080P 选项不会增加源画面的细节。M1 的 1080P 源仍可独立用于高分辨率实验。

三轮是 M5 已部署 worker 的真实编码元数据检查，每轮3秒、4Mbps VBR、30FPS cap，源停在 TikTok 首次登录页。各轮读到真实画面与硬件编码初始化，并排空音频通道；没有派发触控、登录 App 或通过 NPS 传输媒体。这不是手机呈现 FPS、60/120FPS、TikTok 视频或公网流畅度验收。静态页面仅收到3/3/4个媒体单元，不能按30FPS请求值认定实际30FPS。各轮显式关闭自己的会话，旧 worker 在关闭期间记录 EOFError，worker exit0；不把它写成无错误的自然撤销验收。

源码核对及离线尺寸/坐标检查通过。客户端读取实际编码宽高来配置解码与等比显示，手机各个指针的坐标按收到的画面尺寸映射回虚拟屏幕，保留动作、指针ID与压力。离线边缘/横屏检查不代替本轮真机物理多点触控验收。

## 本次维护与持久设置

M5 配置 `phone17-root.avd/config.ini` 的 `hw.lcd.width=720`、`hw.lcd.height=1280`、`hw.lcd.density=320`，skin同为720×1280；gateway环境为 `DIRECT_PHYSICAL_DISPLAY=720x1280`、`DIRECT_MAX_SIZE=1280`。CPU8核/内存8192MiB及用户磁盘保持原值，本次没有清空应用、账号或登录数据。

此次明确的物理尺寸变更进行了冷启动，执行前说明正在连接的串流会暂断。首次 launchd bootstrap 因旧作业退出期间的暂态返回非零，随后重新 bootstrap 成功；Android boot1、物理尺寸及 gateway 恢复已读回。Pixel6的两项显示装饰关闭后，最终两个 cutout 资源解析为空，屏幕 Awake。

M5 的旧 gateway 还硬编码了五分钟息屏。按用户此前取消自动息屏的要求，在确认无正式在线连接后，将这一处改为现有源码的环境参数读取，并设 `DIRECT_IDLE_DELAY=0`；没有再次重启虚拟安卓。随后实际收尾发现旧 `idle_power.py` 还把0解释为立即执行计时，不能仅凭环境值认定常亮已验收。已同步现有正式源码的0禁用契约，实际部署模块的8项惰性Timer/陈旧回调/在线保护检查通过。M5此时已有正式会话，未强制重载；受限的单次维护程序等会话自然结束后才重载gateway、唤醒并读回，持有独立reload锁和受限receipt，最长20分钟。receipt完成前常亮运行态验收仍待定。安卓自身 `screen_off_timeout=2147483647`、供电常亮15已读回；它不代表 Mac 关机或睡眠时仍能远程使用。

备份都在 M5 原运行目录的受限 `direct/backups`：

- `m5-standard720-20261003-103012`：AVD配置、gateway、显示profile和LaunchAgent。
- `m5-always-on-20261003-104402`：修改息屏计时前的gateway和LaunchAgent。
- `m5-zero-idle-contract-20261003-105848`：旧idle模块及单次重载receipt。

备份目录0700、文件0600。实际运行目录仍在 M5 的 `/Users/yawen/Library/Application Support/AndroidRemote`，没有移动旧目录。回滚需先确认正式会话空闲；恢复旧物理尺寸需要冷启动，不能在朋友使用期间执行。

维护后使用 App 自带 M5 受信证书检查局域网 `192.168.9.99:15556` 与公网 `146.56.249.175:15558`，均 HTTP200。NPC身份和NPS主程序、国内来源过滤没有修改。腾讯目标路由仍为 `192.168.9.1/en11`；这是宿主路由读回，不是逐包国家/实际代理链证明。此前503的两个修复阶段保留在 [原故障记录](m5-session-startup-fix-20261003.md)，其中540×1200及432×960是变更前的历史证据，不能覆盖成本轮新尺寸。

## TikTok：安装成功，视频验收待登录

M5 已安装原版 `com.zhiliaoapp.musically` 47.0.3/code2024700030，arm64-v8a。APK来自 [APKMirror原版镜像](https://www.apkmirror.com/apk/tiktok-pte-ltd/tik-tok-including-musical-ly/tiktok-47-0-3-release/tiktok-47-0-3-4-android-apk-download/)，不是修改版。Android apksigner 的 v1/v2/v3 验签成功，证书SHA匹配实时读取的 [TikTok官方Digital Asset Links](https://www.tiktok.com/.well-known/assetlinks.json)：

- APK SHA-256：`028ece4dab2eeeb1ba1b67c621de7851c95be0050d94070648d429a859203a6a`
- 签名证书 SHA-256：`9041803e91bcb814b4b4399fb5c85a91640b755e5e8ba76813814bf4cf2ab5ba`

ADB install 成功，正常启动进入 TikTok 登录页面，没有观察到启动闪退。普通返回及网站 deep link 仍进入登录流程，本轮没有找到游客入口；没有代用户注册、使用现有Google账号创建TikTok账号或修改APK跳过登录。已向用户询问是否能自行使用现成TikTok账号登录，完成后才能继续验收连续视频播放。

M5既有本机Clash代理7897访问TikTok网页HEAD为HTTP200，只证明这条宿主HTTP请求可达，不证明 guest 视频CDN、地域策略或播放可用。当前没有取得有效的Clash控制API连接读回，未宣称已经查到TikTok实际代理链。没有改变Clash全局选择、虚拟SIM/地域或NPC线路。

脱敏数字记录、尺寸探针与私有截图保存在被Git忽略的 `docs/evidence/m5-standard720-tiktok-20261003`；不提交APK、证书私钥、账号、AVD磁盘或原始日志。正式App仍为v1.30，不需要因为这次后端尺寸维护重新下载安装。
