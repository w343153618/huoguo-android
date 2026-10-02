# 认证 App LAN UDP 候选：正常登录、多指与重连验收

2026-10-02。本轮把既有 UDP 组件接到了隔离 App 的正常登录按钮，并用真实一加12验证了现有账号认证、视频、AAC、多指数据和退出收尾。**正式 v1.30 仍为 TLS/TCP；本记录不表示公网 UDP 产品已发布，也不表示稳定60/120FPS、V50或物理音画延迟已达标。**

## 实现与依赖

候选只在 `-PprobeApplicationId=local.remoteandroid.direct.experiment -PauthenticatedLanUdp=true` 时构建；普通 App 不打包该 UDP 源码集和 JNI。正式包名、原签名、版本31/v1.30、M1公网15556、M5公网15558及独立 NPC 身份均保留。没有新建或删除正式账号，本轮直接使用已有账号。

登录到 M1 `192.168.9.128:15560`：既有账号验证和 App 已信任的证书负责 HTTPS 认证，返回只存在内存里的随机32字节会话密钥、会话标签和参数。媒体入口为物理 en7 的 UDP15963，首个有效 READY 必须来自 HTTPS 客户端的同一 LAN IPv4；随后固定源端口。视频、AAC、触控与反馈都经认证 UDP，没有 TCP 媒体回退。当前范围只允许显式物理网卡及同 `/24`，不是 Tailnet/WAN 入口。

会话 READY 期限10秒、ALIVE 静默期限3秒，开始后的硬期限最多120秒。账号保留位覆盖工厂初始化、后台启动和完整清理；失败收尾不能确认时，禁止下一条会话抢占。触控写入在当前租约复核后进行，有500ms本地写入期限。清理用真正的 `ACTION_CANCEL` 清除 guest 的全部指针状态，避免旧 scrcpy 的 CANCEL 后仍残留 local ID 的问题。Host 必须验证候选 guest JAR 的 SHA 和取消能力，不假定 stock JAR 具备它。

| 本轮实际使用的组件 | SHA-256 |
|---|---|
| 已安装隔离 App | `2dc1e0e8a60d7996195a74d486b4c15973cc90b880be9a722f71a94a4d722f0e` |
| App 原签名证书 | `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da` |
| 取消能力 guest JAR | `823faf9c95c64d4ca8740c8c36596c30eb89ce09e112d218402962026e2b9c47` |
| 新 UPTIME packetizer | `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142` |
| 原生 VideoToolbox 编码器 | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` |
| 第五轮 UI 测试 helper | `bbabfde261a8ae9d5b7bebf0a50e94274259c3dc361b44965f6bc8cafa686ed1` |
| 第四/五轮实际安装的 guest 回执 helper | `99d46399a181b39e391aece174ff69eb1a02e6498d8262e33cbd431910ce88c4` |

packetizer 本轮摘要确实读回 `host_clock_gettime_CLOCK_UPTIME_RAW_us`。历史冻结 binary 没有被覆盖；真实宿主睡眠/唤醒仍未测试。Guest 取消能力实际读回 `touch_cancel_clears_pointers_v1`。

外部候选运行目录为 `/private/tmp/huoguo-authenticated-lan-udp/runtime`；hardware 的 venv/proto/脚本继续引用旧受限运行目录，只有候选 JAR 放在独立目录。正式账号与证书继续使用现有受限主机位置，不复制到源码树。App 候选和 helpers 在 `/private/tmp/huoguo-authenticated-lan-udp/`；它们不进入 Git。完整数值证据在忽略的 `docs/evidence/authenticated-lan-udp-20261002/`。

## 真实测试与失败记录

M1 guest 为 emulator-5556、6核/16GiB、物理1080×1920、min/peak60Hz；手机为已授权的一加12 f7fc9469，Wi-Fi `192.168.9.149`。没有修改 CPU 限制，但其上限会动态变化；最终读回 policy0/2/5/7 分别为1132800/1824000/1612800/1939200kHz，不能把不同轮次当作同一固定CPU条件，更不能等同V50。

首次媒体阶段使用 Morphe YouTube 公共样本 `aqz-KE-bpKQ`（Big Buck Bunny 60fps 标题），之后切换到独立 guest 回执页面。编码输出实际为1080×1920，目标12Mbps VBR、60FPS、80ms缓冲；wire预算32Mbps、默认第二层 socket wait 与 guard 保留。真实播放器该轮所选完整 format 没有重新完整读回，不能单凭视频标题或屏幕Hz保证60张不同内容帧。

共五次正常UI尝试，其中第一、三、四轮未完成整个 helper；第二轮多指有效但重连判断有缺陷；第五轮完成修正后的流程。没有把失败样本补零后混入性能平均。

| 尝试 | 来源 SF 全采样 FPS | 手机 SF 全采样 FPS | 手机最大呈现空档 | 功能结论 |
|---|---:|---:|---:|---|
| 1 | 57.589 | 46.372 | 66.3ms | 已有认证音视频；就绪文件在发布后被 helper 删除，造成 driver 的 chown 竞态；改为原子发布 |
| 2 | 48.191 | 39.962 | 605.0ms | 双指与十指回执有效；重连误读前一 generation 计数，不能算重连通过 |
| 3 | 57.544 | 46.614 | 679.5ms | 系统 CollectInfoActivity 抢焦点、helper 失败；旧 guest 文件无 freshness，不能复用 |
| 4 | 53.750 | 34.142 | 182.3ms | 明确读回 `OS_touch_injection_rejected`；未完成触控/重连验收 |
| 5 | 55.286 | 50.429 | 74.6ms | 正常登录、App内双/十指、取消、重新认证、断开时两指取消及报告收尾通过 |

这些20秒 SF 全窗口包含启动时没有新媒体呈现的时间，且各轮环境不同，**不是码率或协议的AB对照**。第五轮手机在自身首个呈现后固定 `[2,12)` 秒窗口计数607次，即60.7次/秒，p95/p99约16.58/24.87ms、最大58.02ms；完整20.524秒窗口仍为50.429次/秒。该计数包含可能重复内容与追赶调度，不能称“严格稳定60帧不同画面”。源端自身同规则窗口58.8次/秒，最大101.96ms；两个设备各用自己的时钟窗口，没有逐帧跨端映射。

## 第五轮功能回执

测试 helper 通过正常登录按钮启动，未从ADB下发媒体密钥。账号输入是一次性的 App 私有0600文件，读取后、发起HTTPS前立即删除。App本身的连接不需要 root 或测试 helper；root只用于本轮受控 UI/私有数值读取。

首次约29.90秒接收区间记录1709个完整媒体帧、1698个入队帧、1697个 codec callback，硬件解码标志为1。该区间含真实视频和触控页面，不能用它直接计算纯视频FPS。手机厂商 callback 时间戳有目标回显现象，屏幕呈现仍以独立SF为准。

第五轮向 App 的实际 SurfaceView 派发原生 `MotionEvent`，经 UDP 后 guest 收到 `SOURCE_TOUCHSCREEN=4098`，依次增长到十指、MOVE、一次十指CANCEL，接着新的 DOWN/UP 从 local ID0 开始。第二轮曾成功通过手机OS注入双指；三、四轮失败使其重复性未完成。第五轮明确没有再次OS注入，因此**十指是 App→UDP→guest 原生事件管线验收，不是手机OS十指或十根真实手指验收**。本轮没有四角/旋转的真实手机验收；这部分仍只有离线检查。

第五轮重新点击正常登录按钮，用新的 HTTPS 会话重连。helper 等待 generation 真正推进、至少15个新接收帧和10次新callback，不能重复第二轮的旧计数问题。第二条会话实际125个接收帧、118个入队帧、117个callback。保留两指DOWN后退出，guest最后收到双指CANCEL；Host为2次DOWN、0次UP、1次CANCEL，最终活动指针0、写线程退出，无 writer/ACK错误。两条 Host 都收到认证STOP，UDP15963随后无监听。

收尾已删除一次性账号输入、临时标志及两种 helper 包，恢复暂时调整过的 `three_finger_flash_memory_enable=1`。保留候选App、冻结依赖和独立运行目录；Morphe已重新打开。候选HTTPS服务有界结束，正式网关PID97463继续监听15556；M5、NPC身份、旧用户数据和国内来源过滤未改。

## 音频采样开销与新可行动线索

每次真实媒体前先运行 ART 数值直方图 microbench，启用的100000次组合操作约31–260ms，存在很大的JIT/预热差异。这只能说明纯计数方法的成本范围，**没有完成真实AAC/PCM并发诊断开/关AB验证**；不得据此默认推广诊断或重写旧报告。新字段仅存在这个新候选APK中。

第二轮音频 worker late 218次中，206为 `codec_input_unavailable`、12为到达年龄过期；AAC组包过期0。第五轮首次会话对应44次，其中36/8。PCM写入超时0，不表示写入从不阻塞。现有音频代码把解码输出缓存持有到调度等待和PCM写入结束；第二轮平均持有24.36ms、最大272.98ms，超过通常21.333ms AAC节拍。它是待验证的本地背压原因，不能把全部损失说成网络或80ms缓冲不足。

同轮还出现共享时钟重新锚定与旧音频目标差异的少量长尾。输出时只确定一次目标，等待期间的最新目标当前只记录诊断。音频拆PCM队列和重新锚定必须分别作为单因素，避免混改后无法解释。`actual_acoustic_output_measured`、`actual_lip_sync_measured`仍为0，没有完成物理音画同步。

第三轮 native 已记录明确的发送预算方向：最大单帧预计 wire457998字节，在32Mbps下至少114.5ms，超过80ms整体帧预算。1596源帧→1524输出的72帧差，恰为3 budget丢弃、2 deadline丢弃、67依赖丢弃。默认VBR不消费自适应 recovery 降码率反馈。它不是所有轮次卡顿的唯一原因，但比笼统“硬解不够快”更可验证。当前摘要仍 `final=false`，不能冒充完整停止后的native总计。

## Tailnet读回与下一轮

本轮只读确认 M1 `utun0`实际持有100.65.0.2，去手机100.65.0.3的内核路由为utun0。不是先前担心的只有 userspace Serve。去146.56.249.175的路由仍经192.168.9.1/en7。手机peer在线但当时无活动CurAddr；`Relay=headscale`是状态字段，不能凭它判断实际媒体正在中继。此次没有新Tailnet媒体、公网UDP或P2P数据，没有改Clash/云端规则。

下一轮按可辨识顺序推进：

1. 单独完成真实OS触摸/边缘/旋转及认证取消期间重连检查，保留本轮数值和辅助工具的测量层区别。
2. 在同视频、同源供给门槛下，单独12M→8M，保留wire32M/80ms/wait/guard，核对关键帧大小、预算和依赖丢弃。扩大wire仅可作为隔离诊断，不能直接默认推广。
3. 单独将PCM复制到固定容量队列后立即释放codec输出，独立线程使用现有目标/期限写入；ABBA检查输入不可用是否转移成PCM溢出/过期，再另测共享时钟目标更新。
4. 用已核实的utun0引入明确Tailnet认证UDP入口，验证实际direct/relay与Clash共存；保持国内物理出口。再测试国内UDP中继及P2P，失败不得静默转成TCP媒体。

源码验证：完整539个Python测试PASS（含24项worker认证/来源/清理检查）；App descriptor的实际Java断言、guest指针清理Java检查、候选assemble/lint及不启用候选的正式Java编译均通过。源级PASS和本轮有限LAN功能验收不替代公网、V50、连续稳定性和物理音画验收。
