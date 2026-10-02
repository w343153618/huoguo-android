# 认证 LAN UDP 后续实验：系统触控、完整收尾与音频候选

2026-10-02。接续[上一轮正常 App 登录记录](authenticated-lan-udp-results-20261002.md)。这一轮完成了手机系统生成的单指/双指事件到虚拟安卓的关联回执，并让实验主机可靠输出停止后的原生总计。也保留了未通过的四角结果，以及约30次/秒的源视频层供给和大帧发送预算不足两个可行动问题。

**正式发布仍为 v1.30、TLS/TCP；M1 公网15556、M5备选15558没有切换到此候选。没有新公网 UDP、Tailnet 媒体、V50、稳定60/120FPS或物理音画延时验收。** 本文的12M/8M是两次探索性运行，不是控制了其他变量的码率ABBA。

## 范围、版本与保留状态

- 实验入口：M1物理 en7，HTTPS `192.168.9.128:15560`只负责现有账号认证及短期会话描述；视频、AAC、触控和反馈使用认证 UDP15963，没有 TCP 媒体回退。
- M1 guest：emulator-5556，6核、16GiB、物理1080×1920；min/peak设置60Hz。输出实际1080×1920，请求60FPS、VBR、80ms缓冲、32Mbps wire预算，保留默认 socket wait和guard。
- 手机：已授权的一加12 PJD110/Android16，120Hz，用户限频状态保留。它是代理测试设备，不能直接等同真我V50。本轮结束policy0/2/5/7上限分别1689600/1612800/1824000/1939200kHz，没有写入CPU频率。
- 实际手机数据使用原冻结隔离 APK；新PCM候选只构建和离线验证，没有安装或用于这些实测。正式 App、现有账号、NPC身份、Clash规则、国内来源过滤均未改。
- 实验服务有界退出；一次性登录输入和两种测试helper已移除，隔离App及外部候选运行目录保留。最后guest焦点为Morphe。自动重开命令退出251，但恢复焦点的独立读回成功，不能把该命令退出码写成成功。

| 实际依赖/候选 | SHA-256 |
|---|---|
| 本轮实测、仍保留的隔离 APK | `2dc1e0e8a60d7996195a74d486b4c15973cc90b880be9a722f71a94a4d722f0e` |
| 新有界PCM候选 APK，仅构建 | `9108a6919a632dce08c7f9590520d2e0bb47a0e2d1fc52e770fad2f00cd33f32` |
| 两者原签名证书 | `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da` |
| 新 UPTIME packetizer，历史binary未覆盖 | `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142` |
| VideoToolbox 编码器 | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` |
| 真CANCEL guest JAR | `823faf9c95c64d4ca8740c8c36596c30eb89ce09e112d218402962026e2b9c47` |
| 本轮host worker源码 | `0599c98dff0492adbb4da68db69347f498c4c459aa02a3004fdb79e5a25d875a` |
| 最新kernel轮 UI helper | `4d77a295770ee4c3abd7fb6cc1fc1eb0382c45fddda99a30c2b873eb386eee78` |
| 最新kernel轮 guest回执helper | `2ec4475b26bdc89eed9ddc161755316537cacd070de748958c415edb1e55967a` |

外部运行依赖继续放在 `/private/tmp/huoguo-authenticated-lan-udp/`、`/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`、`/private/tmp/huoguo-session-pool-encoder`及已有受限runtime。秘密没有进入源码、Git或报告。完整数值证据只在忽略的 `docs/evidence/authenticated-lan-udp-followup-20261002/`，本文为可提交的摘要。

## 先修复测量与停止收尾

上一轮部分native报告仍是`final=false`。本轮worker取消后先撤销发送，关闭自有源并让feed线程关闭packetizer stdin；stdout/stderr继续排空，等待真实EOF和`final=true`，必要时仅终止自己拥有的子进程。若线程或资源清理不能确认，保留会话占位并明确失败，不能创建新会话掩盖残留。

下表四条完成会话均读回正确的 `host_clock_gettime_CLOCK_UPTIME_RAW_us`，native真实final、自然退出0、无TERM/KILL、EOF及资源清理确认。除正在调用stop的ingress线程明确标成`stop_invoker_excluded`外，拥有的后台线程均停止；不能声称该线程在自身stop内部已经退出。

另外加入每2秒检查正式会话的候选取消机制。检查失败或出现正式会话只取消候选，不停止正式服务。已发生一次正式连接忙时跳过测试并清理一次性输入。该检查不是原子占用保护，也不是零开销：完成轮中lsof单次最长约158ms。测试驱动现在对预检失败、120秒仪器超时、阶段失败及Ctrl-C统一执行有界收尾，停止的包仅限隔离helper/候选。

手机和源端SF采样改为正常认证收到新媒体、再预热3秒后的独立约20秒窗口。两个设备使用各自时钟；不存在跨设备逐帧SF映射。整个App统计还包括后续触控页面，不能直接拿它当纯视频FPS。

## 尝试记录：保留失败，不补零平均

| 尝试 | 结果与使用边界 |
|---|---|
| `os-touch-12m` | UiAutomation首个DOWN返回false，补偿CANCEL也false；该轮guest回执helper在InsetsController处异常，视频源也未持续呈现。不能用于性能或guest触控验收。 |
| `adb-touch-12m` | 有界候选网关已到期，没有认证媒体或host会话，不能算传输性能失败。 |
| 正式会话忙时跳过 | 检出正式15556连接；未执行手机实验，未停止正式服务。 |
| `adb-touch-12m-complete` | 有媒体，但driver只接受简写Activity名，误拒绝实际已聚焦的全名；属于驱动失败。修正后重测。 |
| `adb-touch-12m-receipt-fixed` | 认证、App内双指/十指真CANCEL、新认证重连和退出时释放通过。手机shell tap退出0但没有Window/guest回执，不能算OS触控通过；四角不完整。 |
| `kernel-touch-8m` | 一加12内核生成的单指/双指通过Window→UDP→guest回执；双指/十指直接派发、真CANCEL与重连继续通过。四角只确认3/4，视频仍有长尾和预算丢弃。 |

初版guest helper空InsetsController问题已修。每次触控阶段先重新启动专用guest页面，避免复用旧回执。内核测试只允许这台已授权手机、核对`touchpanel`和固定evdev能力后进行，并在finally释放测试槽；没有解锁其他手机或改变系统手势/CPU限制。

## 原生触控：已通过什么，仍缺什么

最新kernel轮的前七个手机Window事件与新鲜guest回执顺序相符：

| 动作 | 手机Window | guest回执 |
|---|---|---|
| 单指 | DOWN1→UP1 | 相同动作、中心约(539,959) |
| 双指 | DOWN1→POINTER_DOWN2→MOVE2→POINTER_UP2→UP1 | 相同动作/指针数，双指约(494,959)/(584,959)并发生移动 |

手机事件为SOURCE_TOUCHSCREEN4098、device6、display0。链路经过手机InputReader和正常App窗口，再到UDP与guest原生触控；这不是把手机当作Mac鼠标。它是内核生成的受控事件，**不是真人手指或触控到发光延时验收**。十指仍由App SurfaceView直接派发，只确认App→UDP→guest，不冒充手机OS十指。

两指、十指CANCEL后复用新手势ID，以及重连时两指未抬起退出均有guest回执。两条重连host分别输出122/122、133/133帧，native/guard无丢弃；都是DOWN2、UP0、真CANCEL1释放2个指针，最终活动指针0。

四角尚未通过。最新回执有37/64条，未发生环形裁剪；(10,19)、(1069,19)、(10,1900)分别收到DOWN/UP，但(1069,1900)整对缺失。手机35次MotionEvent包含全部四角，黑边忽略/发送错误/重试耗尽均0，edge ACK32；host生成DOWN21/UP9，符合完整四角写入控制通道。**ACK表示写入本地控制通道，不等于guest应用接受了事件。** 当前证据把问题缩到本地控制写入之后、guest回执之前/之内，但还没有guest注入结果逐事件关联。

单独guest本地对照进一步发现：页面只预热0.6秒时，三轮均只记录下方两角；改为4秒窗口预热和逐角快照后，四角全部DOWN/UP都出现。它证明过早测试会漏事件，也证明此guest能够接收这些坐标；不能反推已经解决远程那次右下角缺失。下一轮需加入窗口稳定/guest注入结果关联，然后再验证旋转和边缘。UiAutomation、shell注入为什么在手机上失败仍未确定，不能归因为deviceId或未经证实的OEM拦截。

## 真实视频：源供给约30，大帧仍超预算

视频为Morphe YouTube公共样本`aqz-KE-bpKQ`，请求从60秒位置播放，之前读取过1080p60菜单。具体执行轮的完整format、实际seek落点和结束时播放状态未全部核对。**菜单60、屏幕120Hz或请求60FPS都不证明窗口实际供给60次不同视频画面。** 这两次独立源视频层采样实际约29次/秒，VSYNC读回33.333ms。

| 约20秒媒体窗口 | 12M，receipt-fixed | 8M，kernel-touch |
|---|---:|---:|
| 源SF帧数/实际窗口 | 594/20.604s | 596/20.404s |
| 源SF FPS / 相邻间隔cadence FPS | 28.830 / 29.568 | 29.210 / 29.995 |
| 源间隔p95 / p99 / 最大 | 38.973 / 47.086 / 176.491ms | 37.579 / 39.214 / 70.372ms |
| 手机SF帧数/实际窗口 | 488/20.012s | 546/20.264s |
| 手机SF FPS / cadence FPS | 24.386 / 25.112 | 26.944 / 27.736 |
| 手机间隔p95 / p99 / 最大 | 41.440 / 92.306 / 1806.684ms | 41.440 / 58.013 / 671.283ms |
| 手机超过100ms空档 | 4 | 4 |
| 全首次会话接收/入队/callback | 960 / 953 / 952 | 1073 / 1051 / 1049 |
| 首次会话FEC过期 / dependency丢弃 | 4 / 19 | 1 / 0 |

硬件解码标志为1，输出1080×1920；callback是手机厂商带目标回显的计数，呈现FPS仍取独立SF。全会话接收计数包含后来专用触控页面，不能直接与这20秒窗口相除。8M这一轮比12M短尾较好，但源空档、场景和动作也变化，没有控制ABBA，不足以把收益全归给码率。

| 真实停止后的native首会话总计 | 12M | 8M |
|---|---:|---:|
| source→packetizer输出 | 1076→985 | 1147→1075 |
| 总历时 | 33.630756s | 35.969350s |
| budget / output deadline / dependent丢弃 | 9 / 2 / 80 | 8 / 1 / 63 |
| 第二层guard deadline / skipped chain / incomplete FEC | 3 / 3 / 1 | 1 / 0 / 0 |
| 最大AU字节 | 487174 | 427096 |
| 最大估计IPv4加密/FEC wire-frame字节 | 656392 | 575196 |
| 32Mbps下最低序列化时间 | 164.098ms | 143.799ms |

80ms整帧发送期限与这些最大帧不兼容：最低线速时间就超过期限，尚未计入其他等待。budget丢掉参考帧后又产生dependent丢弃，已有明确计数；尚未逐帧映射到每一次手机长空档，不能声称这是所有卡顿的唯一原因。8M仍有8次budget丢弃，因此仅降低到8M不是完整修复。

下一步先核实真实60帧内容的实际解码格式与源视频层供给。供给不足时，不把网络参数当成60帧优化结论。随后单独比较受控关键帧大小/恢复反馈，保留80ms与参考链保护；扩大wire预算只能作为诊断变量，不直接推广默认值，也不再重复没有区分收益的socket-wait矩阵。

## 音频：新候选已准备，尚未真机比较

本轮实测仍是旧音频A：codec解码输出持有期间完成调度和AudioTrack写入。12M/8M首次会话worker late分别25/12，其中到达年龄8/7、input unavailable17/5；解码输出到释放平均21.229/21.040ms，最大346.120/210.621ms，PCM写入均值19.763/19.870ms。没有PCM late或写入超时不代表没有背压。这些是含触控阶段的探索性数值，尚未测声学输出、口型同步或持续漂移。

新候选B复制PCM后尽快释放codec输出，由独立有界线程沿用原PlaybackClock目标写入：

- 固定5个16KiB槽，80KiB池；最多排队4帧/32KiB，单记录16KiB。
- 队列年龄及原目标逾期分别检查；不重新锚定目标来隐藏等待。容量、年龄、关闭清空和丢弃原因均有数值统计。
- 每个资源epoch只有一个所有者。input/codec drain/PCM线程停止各有有限join；未确认停止时保留资源并阻止新连接，避免释放仍在使用的codec或创建无限残留线程。
- UI新增“实验：有界PCM输出队列（默认关闭）”，只影响隔离App本地会话，正式构建不包含它。

纯JVM队列测试、使用窄Android API替身运行实际Receiver的清理测试、候选assemble/lint均通过，签名与原APK一致。**新APK没有安装，没有手机A/B结果，不能称音频已优化或音画已同步。** 下一轮用同一个新APK的A/B/B/A切换，仅改这个选项，同时记录队列/目标过期、input unavailable、PCM写入、AudioTrack播放头和SF长尾；诊断开销另做独立对照。

## Tailnet与Clash：下一层的明确边界

只读核对M1为100.65.0.2，去手机100.65.0.3走utun0；去146.56.249.175仍经192.168.9.1/en7。peer在线但无活动CurAddr、Rx/Tx0；`Relay=headscale`是home relay元数据，不证明媒体路径。控制服务只使用`https://hs.yilufa.site`。本轮没有改Clash、路由或云端过滤。

下一层需要显式`network_scope=tailnet`策略，不能把整个100.64/10当作物理LAN加入原有白名单。认证和UDP固定M1 tailnet地址，核对内核utun地址/路由以及LocalAPI WhoIs对应已登记手机；未知peer或身份变化失败关闭。内层UDP绑定utun，外层tailscaled才经物理网卡；不能把100.65目的UDP强绑en7。

媒体期间必须读回actual direct/relay、端点和计数。App使用UDP并不保证Tailnet每一跳均UDP：stock DERP可能经HTTPS/TCP，不能把它宣称为符合用户最终UDP中继要求。不得全局关掉DERP破坏既有可用线路；需要单独实现、验证国内UDP中继后再做P2P优先、失败转UDP中继的产品策略。现行Serve15556仍为正式TCP入口，不参与该验收。

Clash共存需核对实际规则命中、tailscaled物理出站与云端观察到的来源，路由读回不能替代源IP/国内出口验收。保留现有国内过滤。公网、蜂窝、远端移动Wi-Fi与V50仍分别待测。

## 验证与下轮次序

本轮最终557项Python/JVM/自有pipe子进程检查通过，其中35项worker检查、5项驱动清理检查。候选与默认正式构建的assemble/lint通过；默认APK中不存在UDP候选类与JNI。旧实测APK未覆盖，正式升级包未替换或增加版本号。

结束时M1正式网关PID97463、下载PID17603仍监听；M5网关PID56991、下载PID1761仍监听，两机正式会话均空闲。候选15560/15963均无监听，私有一次性登录输入不存在。

接下来的有限实验按依赖推进：先用稳定窗口与guest注入回执补齐四角/旋转；核实真实60帧源供给并控制关键帧大小；在新候选APK中独立做音频队列ABBA；随后接入严格Tailnet身份与actual-routing验证，再测国内UDP中继/P2P及指定公网路径。每一步失败都保留明确原因，不静默换TCP、不用超过100ms缓冲掩盖问题。
