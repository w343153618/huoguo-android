# 2026-10-03 机主 Tailnet 认证 UDP 真机记录

本轮运行在 M1 原有 UID501 / `RemoteAndroid17Compare`，宿主隔离未验收；客户端为限频的一加12。两端同家 Wi-Fi，M1 `100.65.0.2` → 手机 `100.65.0.3`。现有 `huoguo` 账号通过 App 的受信 HTTPS 正常登录，视频、音频、触控通道使用认证 UDP。没有新增测试账号、改动正式服务或写入手机 CPU 上限。正式 v1.30 仍为 TLS/TCP；本记录不作为公众 UDP、蜂窝、异地 V50 或物理触控/声学同步验收。

## 已执行的入口与生命周期

独立实验 APK 的实际安装 SHA256 为 `ba84ac006eb007cbe1b087fe29efec3a3a0c2dcdb6298b8130a1d5eb49331387`，版本1.31-alpha.1/code32。它与旧2dc客户端、9108 PCM-only构建区分。本轮固定 UPTIME packetizer 为 `/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`，SHA256 `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`。原签名保留。

实验 App 初次启动被一加系统屏幕使用时间明确拦截，界面显示该实验 App“停用中，无法使用”；只解除了这个 App 的使用限制。之前临时暂停的 DigitalWellbeing 包已恢复 default/enabled0，未关闭用户 CPU 限制器。OEM USB 安装引导也使普通ADB helper安装超时，随后通过已授权root的 `pm install` 安装固定签名的临时UI helper；每轮结束删除helper和一次性私有登录输入。上述启动前失败没有进入媒体会话，不作为零FPS样本。

正常UI认证、收到真实视频/音频、返回退出、重新认证连接均通过。新增独立音频线程快照检查，每轮两次退出均观察到三个固定音频线程名存活数0。检查在媒体采样窗口外执行；它不是所有native线程/codec资源的原子快照。这几轮采用media-only路径，没有重新派发触控；本轮通道存在与较早单指/双指记录分别保留，不能增加为新的多指验收。

### 网关退出时的真实竞态

第一次功能轮只留下首host报告；第二次App重连实际收到92帧，但没有第二host final。审查发现：UDP STOP线程已取得清理权时，HTTP DELETE与gateway退出的 `registry.close()` 只确认撤销，没有等待这个daemon线程完成 `worker.stop()`。进程可能以0退出，留下不完整的逐会话证据。

现增加专用 `close_and_wait()`：撤销立即生效，退出在锁外有界等待factory/start/stop完成；不重复stop，不在触控热路径等待。gateway默认45秒收尾预算，失败输出固定分类并以非零退出。下一轮真实重连已经落下两份host final，gateway明确 `quiescence_confirmed=true`；此处确认资源和报告完成，native具体退出码仍分别记录，不把final存在当作退出码0。

## 内容身份与性能边界

第一次轮次仅发送BBB URL请求，尚未确认已存在的播放器实际处理它。媒体期间源与手机cadence都约25FPS；稍后的截图显示另一段公开动画。该轮只保留为真实视频功能验证，排除固定BBB/60FPS对照。发送URL成功、App焦点正确、显示器60Hz都不足以证明当前内容就是指定60FPS视频。

随后只停止测试播放器并冷启动BBB URL，截图明确看到“Big Buck Bunny 60fps 4K”公开标题。无串流时独立源视频层cadence59.595FPS；所选codec/itag和内容唯一帧仍未独立读取。重新启动后的轮次使用1080×1920、60FPS上限、VBR目标8Mbps、80ms缓冲、PCM队列关闭，原32Mbps wire预算及wait/guard保留。

| 指标 | 源视频层 | 手机视频层 |
| --- | ---: | ---: |
| 完整观察秒数 | 20.289 | 20.385 |
| 独立SF呈现计数 | 1179 | 1135 |
| 计数/完整观察秒数 FPS | 58.111 | 55.678 |
| 首末呈现间隔 cadence FPS | 59.702 | 57.273 |
| 间隔 p95 | 18.290ms | 16.579ms |
| 间隔 p99 | 20.440ms | 24.866ms |
| 最大空档 | 68.757ms | 754.168ms |
| 超过100ms空档 | 0 | 1 |

两种FPS计算口径同时保留；SF不是内容hash，也不能与不同设备时钟直接相减。手机codec callback仍有请求target回显，不能替代独立SF或光学呈现。

首native真实final：1555输入 → 1523输出；1次frame-budget drop与31次dependent-source drop。最大加密IP wire帧322584字节，在32Mbps最低序列化需要80.646ms，已超过80ms assembly预算。这个保护会丢弃不可按时完整交付的帧并等待参考链恢复；不能为了显示漂亮FPS移除它。手机该轮FEC过期/参考链丢弃为0，但解码前队列overflow3、恢复清空12、等IDR准入丢弃34，也有独立的接收/解码背压。没有逐帧时间关联，不能把754ms空档全部归给单一I帧事件。

此轮两host报告都有真实final与EOF/进程退出确认，首native退出1、第二退出0；未使用TERM/KILL。旧feed在65536字节chunk边界停止，可能截断尚未完整发布的AU。历史报告没有记录退出当刻的输入边界，故这次退出1的精确原因仍不追认。

### 完整记录转发与实际撤销验证

现已按native契约实现有界完整记录转发：识别H.264 marker、尺寸、config和AU；单AU最多1MiB、config最多64KiB，不建立额外帧队列。仅本worker的视频socket使用100ms取消检查；不完整记录从首字节起共用2秒异常读取预算，不能把这个异常预算解释成播放缓冲。未发布的部分记录在撤销时丢弃；已开始写入的完整记录由唯一writer写完，再关闭stdin。运行中EOF、超长、非法记录和读取超时仍失败，停滞pipe仍受既有TERM/KILL收尾预算约束。

9项新fixture覆盖逐字节分片、各阶段取消、完整但未发布的记录、运行中EOF、尺寸/PTS/长度边界、最大AU、整体读取期限及writer失败。其中一项包含真实owned socketpair和严格reader子进程：在一个131084字节记录的部分pipe写入中触发撤销，子进程仍收到完整记录与边界EOF，以0退出。这是输入/收尾验证，不是网络或FPS验收。

后续真实4Mbps轮的首、重连两个会话，分别完整发布1551/269个media记录。两次撤销都丢弃尚未发布的4字节部分记录；native均退出0、final/双输出EOF确认、未TERM/KILL，gateway退出0且quiescence通过。两轮没有live EOF、partial timeout、invalid record或publish failure。这个实际结果确认修复后能在该取消位置正确收尾；不把它反推为旧退出1的唯一原因。

## 最新4Mbps探索轮

该轮仍为1080×1920、60FPS上限、VBR、80ms、32Mbps wire预算、PCM队列关闭。它同时使用新完整feed并降低目标码率，播放器重新冷启动，CPU限制器自行变化；**不是8→4Mbps单因素AB对照，也不据此修改正式默认值**。实际视频标题已确认BBB，所选codec/itag与内容唯一帧仍未读取。

| 指标 | 源视频层 | 手机视频层 |
| --- | ---: | ---: |
| 完整观察秒数 | 20.438 | 20.471 |
| 独立SF呈现计数 | 1187 | 1175 |
| 计数/完整观察秒数 FPS | 58.078 | 57.397 |
| 首末呈现间隔 cadence FPS | 59.652 | 59.094 |
| 间隔 p95 | 19.079ms | 24.864ms |
| 间隔 p99 | 20.836ms | 24.867ms |
| 最大空档 | 71.357ms | 165.763ms |
| 超过100ms空档 | 0 | 1 |

首native1551输入=1551输出，frame-budget/deadline/dependent丢弃均0；最大wire帧228081字节，在32Mbps最低序列化约57.020ms。wire值是native估算的本层加密IPv4字节，不含外层WireGuard额外开销，也不是逐包观测的公网速率。手机收到1549个完整media帧，入codec1500、vendor callback1499；网络重组没有frame expiry/reference/dependency丢弃，但App输入Inbox仍溢出3次、清12帧、等待IDR丢32帧，并有1次codec输入超时。重连也有3次溢出、清12帧、等待IDR丢87帧、输入超时1次；这些是全会话统计，包含初始化，未与稳态165ms空档逐帧关联。

因此本条件已经接近60FPS呈现节拍，**仍存在明显长空档，未达到稳定无卡顿60或120FPS**。1499个vendor callback时间戳全部回显请求target，其中1494次在回调观测时仍为未来，不能作为独立呈现或缓冲释放时间；上述手机FPS来自SF独立采样。

源码另确认默认lead0是在输出ready后直接以未来target交给Surface，不是Java先持有80ms。官方[MediaCodec定时输出契约](https://developer.android.com/reference/android/media/MediaCodec#releaseOutputBuffer(int,%20long))说明SurfaceView buffer要在target已过且不再使用后才还codec，顺序处理可能挡住后续buffer，建议约两次VSYNC前提交。这使未来Surface库存/decoder背压成为下一项候选机制，**尚未证明是本轮空档原因**。80ms×60≈4.8只是目标时刻跨度，不等于固定5槽；已知App Inbox上限4帧/2MiB，vendor codec和Surface槽数未配置或读取。

## 音频与通路

真实音频诊断已在当前APK采到。上述8Mbps轮codec输出持有平均21.151ms，PCM写平均20.253ms；原target相对AudioTrack队列尾平均-144.639ms，1228次观察中1122次≤-80ms。累计直方图显示这是多数输出的内部排队差，不是少数启动点，但没有时间分段与声学输出，不能将其当作实测耳朵延时或口型偏差。PCM队列候选仍默认关闭；后续同APK只切该选项，验证codec所有权释放与输入背压，而不是预设它能消除AudioTrack实际出口延时。

Tailnet媒体期间LocalAPI读回活跃phone节点48、直连端点及Tx/Rx增长；最新轮36次有效采样覆盖37.264秒，34次有直连端点、34次Active，Tx/Rx增长18598068/112068字节。home Relay字段仍为headscale元数据；没有逐包外层捕获，不能宣称所有瞬间都经直连UDP，也没有跨地域打洞/DERP中继结论。Clash、既有物理出口和云端国内来源规则未修改。最终只读核对M1节点46在线、M5节点23在线；到手机内层utun0，到云端146.56.249.175仍en7／192.168.9.1。这是路由核对，不新增云端出口国家验收。

源码校验：全仓`python3 -m unittest discover -s tests -q`通过896项（19.055秒），`git diff --check`通过。新增退出barrier和feed包含并发、pending factory/start、真实pipe及错误分类检查；这些测试不替代手机呈现验收。临时helper独立编译并核对实际签名/安装SHA；没有重建或升级正式App。

源证据在工程私有 `docs/evidence/tailnet-owner-media-20261003-attempt6`、`...-attempt7`、`...-attempt8`，保存source pins、binary/APK pins、数值报告和清理结果。最新一加CPU上限从787200/960000/960000/902400变为672000/960000/960000/902400kHz，是原限制器的读回变化，本轮没有写频率。最终候选15560/15963监听均消失、helper与一次性输入均不存在、原guest仍boot_completed=1；停止调用线程的自身存活快照有单独排除边界，不宣称所有线程原子消失。APK、登录输入及原始敏感日志不提交Git。

## 下一步

先给输入等待、Inbox与输出target−release加入固定数值直方图/稳态分段，关联独立SF长空档；不靠callback回显推断呈现。现有probe支持submission lead8/16，但认证descriptor只允许0；若测试0/16ms ABBA，先加仅实验入口的显式opt-in及实际读回，同APK、完整feed、4M/1080/60/80ms、PCMoff及手机实际120Hz保持一致。B把未来buffer移至一份Java输出，也可能加重背压，结果方向不能预设。不要直接发16然后假设契约已经接受它。

同时固定已确认的视频身份、位置与实际格式，再检查I帧峰值预算；4/8Mbps单因素对照不得与输出lead或PCM混改。PCM独立ABBA随后执行，并先读取实际AudioTrack配置而非仅请求LOW_LATENCY。推荐80ms缓冲继续保留。公网国内UDP中继、跨NAT/P2P、远端V50、旋转/四角与实际声学同步仍分别验收。正式v1.30及M1/M5两个NPC身份保持。
