# 手机解码与 Surface 提交实测：2026-10-03

本轮没有找到支持将 Surface 提前提交量改为16ms的重复收益，保留现行 lead0。新增诊断已经在真实手机采到数据：有些卡顿与四帧 Inbox 溢出及参考链恢复重叠，有些没有这一事件；持续申请 codec 输入槽80ms受阻的假设也不符合本轮数据。还不能将所有长空档归到同一个原因，更不能称稳定60FPS已经完成。

源码属于 `codex/experimental-udp`，正式 v1.30仍为TLS/TCP。独立客户端 `1.31-alpha.2` 仅更新了机主测试一加12。实验服务flag是操作者显式开关，不是新增的账号角色ACL；不向朋友开放此端点。没有发布朋友/公众UDP服务，也没有修改NPS主程序、NPC身份、正式网关或Headscale节点。

## 实际条件与依赖

本轮是原UID501的M1 `RemoteAndroid17Compare`，属于机主非隔离实验。手机是一加12 PJD110，保留机主CPU限制；它不是实际真我V50验收。网络为同家Wi-Fi、已登记Tailnet `.2 → .3` 内层认证UDP；HTTPS只负责原账号认证及会话，视频/音频为UDP。不是公网NPS、蜂窝、异地、光学触控或声学音画同步测试。

固定参数为输出1080×1920、60FPS上限、4Mbps VBR、80ms播放缓冲、PCM队列关闭、32Mbps本层wire cap、80ms重组/发送期限，手机实际显示模式读回1440×3168／120.000008Hz。没有提高手机频率、取消限制器或增加播放缓冲。

| 实际依赖 | SHA-256 |
| --- | --- |
| 本轮手机已安装APK | `792c6da7ac92e3c0538351940b15e632367a762552c2b7cd3dd17f60b1a8bf09` |
| 原签名证书 | `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da` |
| 测试UI helper | `da3095d3700986cd5c81b35e45b0cd5a6f56d8ac93bdd9904041e39428708e8a` |
| UPTIME packetizer | `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142` |
| VideoToolbox encoder | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` |
| 本轮实际SF sampler | `dc588c419d0540ea2d1345ec5f0ba8e08f4ac7e30aa81dcb97c8782e628b15bd` |

packetizer路径仍为 `/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`，encoder为 `/private/tmp/huoguo-session-pool-encoder`，外部运行资源为 `/private/tmp/huoguo-authenticated-lan-udp/runtime`。八个host final均读回 `host_clock_gettime_CLOCK_UPTIME_RAW_us`；没有混用历史冻结binary。APK及凭据仍在受限位置，不进入Git。

真实来源为Morphe里的公开Big Buck Bunny，视频ID `aqz-KE-bpKQ`。本轮开启播放器Stats for nerds，逐轮读取实际 **itag299／avc1／1920×1080@60**，然后关闭统计覆盖层再采样；没有凭标题中的“60fps”判断格式。前三轮实际从1分30秒开始，前后都读到299/60。SF仍只证明所选层的呈现节拍，未读取唯一内容帧hash。

## 四个案例及覆盖限制

计划是只切换 Surface submit lead 的0／16／16／0，保持共享目标时钟不变。前三轮A/B/B完成后，第四轮的冷启动格式门槛没有通过，尚未进入媒体就停止，其后播放器Stats读回N/A。随后独立重启候选完成A4，增加冷启动等待并尝试进度条定位；实际读回为“10分35秒中剩余9分”，即95秒，**没有确认定位到90秒**。所以不能把这四行称为无混杂、不断开的完整ABBA。

| 案例 | lead | 源端点cadence | 手机全请求窗口FPS¹ | 手机端点cadence² | 最大观察空档 | >100ms | 手机poll链完整 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A1，实际90秒 | 0ms | 59.904 | 58.122 | 58.631 | 223.793ms | 3 | 是 |
| B2，实际90秒 | 16ms | 59.866 | 57.980 | 58.844 | 265.191ms | 2 | 是 |
| B3，实际90秒 | 16ms | 59.866 | 53.089 | 54.402 | 1002.892ms | 4 | 有效前缀，末次超时 |
| A4，独立95秒 | 0ms | 59.900 | 58.255 | 59.800 | 66.305ms | 0 | 有效前缀，末次超时 |

¹ 已观察呈现数除以实际约30秒sampler时间，保留起止未观察范围，不能当精确完整屏幕FPS。² 首末实际端点间的节拍，不包含首端点之前/末端点之后的未知时间。没有将codec callback请求target回显当作真实呈现。

源端四轮poll链均完整，最大源间隔分别82.149／54.602／61.624／69.439ms。A1/B2手机层全程同名、相邻ring有交集，没有换层、空ring、历史不重叠或ADB失败。B3/A4此前44次比较连续，但最后只剩很短deadline时仍发起ADB，在30.006秒超时；这是采样边界失败，**不是实测手机在这一刻冻结**。此前真实长空档可以讨论，不能把`continuity_complete_observed`改成1或追认完整30秒无卡顿。

启动前的准备失败另存为provision-attempt1/2/3；一个是Android UID读回含工作资料用户后缀，另外是菜单文字实际在content-desc及动画时机。这些均没有媒体会话，不作为失败FPS样本，也不把重复准备计为性能测试数量。

## 阶段计数带来的区分

新增[有界解码阶段遥测](decoder-stage-telemetry-20261003.md)仅在独立UDP客户端启用：120个固定一秒段、固定直方图和64个异常事件。四轮真机App首报告分别31401／32202／32063／31293字节；最终报告字节数以[数值摘要](surface-submit-real-video-20261003.json)为准，均在64KiB内。没有截掉异常事件；前后两个正常认证会话都读回lead和实际执行状态。

| 全首会话统计（含初始化） | A1 | B2 | B3 | A4 |
| --- | ---: | ---: | ---: | ---: |
| input reservation最大跨度 | 14.621ms | 8.251ms | 7.628ms | 12.330ms |
| ready−input平均跨度 | 25.25ms | 64.40ms | 74.85ms | 26.98ms |
| Java输出持有平均跨度 | 0.030ms | 14.22ms | 14.31ms | 0.033ms |
| Inbox overflow总次数 | 3 | 5 | 6 | 2 |
| helper稳态范围内overflow | 1 | 2 | 4 | 0 |
| wait-IDR admission drops | 32 | 100 | 135 | 33 |

四轮各自的唯一input timeout都在初始化段，实际polls=0。当前reservation计时不含独立configure阶段；不能由这个超时推断vendor输入槽堵了80ms。相反，本轮持续申请输入的最大观测跨度均不到15ms。

A1的一个223.771ms长空档与四帧容量overflow在手机数值时刻上重叠；当时队列字节仅7497B，触到的是4帧上限。另两个>100ms空档没有对应Inbox异常：一个一秒段完整AU供给为50，另一段为48且ready−input最大275.791ms。这些数据支持多条链路需要分别排查，不能将所有停顿归为Surface未来buffer库存。

B2两次长空档与overflow粗重叠；B3坏段有多次overflow、等待IDR和ready−input超过700/1000ms。事件与SF范围在同手机数值上相容，但还没有某个AU/PTS到某个SF端点的精确身份对应，`sf_time_domain_verified=0`及`clock_reanchor_coverage=0`继续保留。源SF来自另一个Android时间域，不与手机ns直接相减。

B确实执行了实验等待，不只是回显参数：两轮均读回lead16、status1、实际applications及wait_count>0。target−release平均约15.5ms，A1约78.7ms；但B的输出就绪跨度及Java持有增大。每次park请求最多2ms，**实际**最长却为28.876／22.508ms，完整ready到提交前的最大跨度80.252／83.769ms，并有1／2次预算回退。这个机制没有带来重复流畅度收益，不能作为默认优化。

## 发送与恢复不是同一类丢帧

八个host final均齐全，退出0、stdin EOF、stdout/stderr自然结束，不使用TERM/KILL。A1首会话2156输入=2156输出，native budget/deadline/dependent以及socket guard丢弃均0；手机却记录2次FEC expiry/reference loss、16次dependency drop。发送端已记录的guard丢帧不能解释这两个接收恢复事件，但缺少其逐帧事件时刻，暂时不能精确对应每个SF长空档。

B3首会话出现1次native预算丢和1次依赖丢；最大估算单帧wire为327036字节（包括因预算被拒绝的帧），在32Mbps下理论发送下限81.759ms，超过80ms期限。这是另一条可解释的预算限制，不能与手机Inbox溢出合并统计成同一个原因，也不能为了消除数字去关闭参考链保护。其他首会话估算峰值297122／296124／299966字节，理论下限约74.3／74.0／75.0ms；这些wire估计不包含VPN、以太网或无线链路开销。平均4Mbps不保证每个关键帧都小。

## 不变项与未完成项

CPU限制器在本轮自行变动，1秒级非原子读回记录了多种上限组合，而非只读前后。观察过程也有成本，组合的CPU/LocalAPI读回最大351.8ms；没有验证整套诊断采样对手机并发媒体的开销为零。A1与A4开始时上限较高，B3前后较低，因此不能给lead计算无混杂的因果收益。

LocalAPI同时观察到已登记peer48的Active/CurAddr与字节增长，支持本次实际媒体期间有活跃直连端点；没有逐包核对外层，不能将它称为异地P2P、全程公网UDP或国内云中继验收。最终只读路由仍为腾讯地址经192.168.9.1／en7，Tailnet经utun0；这不是逐包源国家验收。M1节点46 `.2`、M5节点23 `.11`均保留在线，正式M1受信TLS ping仍为VideoToolbox网关。候选15560/15963已关闭，临时测试helper和一次性登录输入已删除，现有账号保留。

当前仍未测：光学触控延时、声学音画偏差、远端V50、蜂窝、公网指定UDP中继、Clash共存逐包出口及朋友宿主/LAN隔离。这一轮没有派发触控，历史内核单/双指和四角3/4的验收边界保持独立。

## 本轮实现与验证

服务端新增显式实验开关，严格只接受整数0/16，默认服务拒绝16；App正常登录重置0，实验值不持久化。新descriptor须与冻结请求相同，两次认证会话都核对真实执行。helper和driver加入有界30秒采样完成握手，两个SF child退出后才离开/重连；完成握手与SF覆盖有效性分别验证。

924项unittest通过，独立实验APK及正常正式构建的assembleDebug/lintDebug通过。正式APK检查不含UDP entry/probe/JNI，正式metrics构造不启用新遥测。新的尾部预算修复有纯fixture和36项相关检查，但尚未独立实机运行，不用它更改旧报告标志。单线程stage-only宿主微基准与旧ART数值基准都不是完整并发采样开销验收。

## 下一轮的最小区分工作

先保留lead0、1080／4M／60／80ms／PCMoff，补有界offer/take间隔、configure阶段和接收侧FEC异常时刻，区分瞬时完整AU突发、worker配置暂停、线程调度和参考链恢复。共享时钟重新锚定仍未观察，需要在不改变策略的情况下补其发生范围。先独立评估采样成本，不直接修改缓冲或宽扫参数。

只有这一证据支持时才做单因素Inbox4→8试验；仍保留2MiB、80ms年龄期限、认证和参考链保护，不让增加容量变成无限排队。16ms候选不推广。SF尾部deadline修复已完成源码/fixture：新poll前按最近4轮ADB耗时的2倍、200～500ms预算判断能否完成；不足就跳过该poll，保留未知尾部。新sampler SHA为 `1fcbc67af72c4748e636acd1d3d6de943feb8df67261b2455b72bb218d63d742`，尚未真机验证，不能追认改变本轮旧 `dc588c4` 的覆盖标志。随后继续PCM、音画和指定公网路径验收。

可复核的[安全数值摘要](surface-submit-real-video-20261003.json)进入Git；完整阶段/事件关联在工程内 `docs/evidence/surface-submit-20261003/stage-association-analysis.json`，原始截图、APK及私有日志不提交。
