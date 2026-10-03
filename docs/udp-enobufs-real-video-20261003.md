# 有界 ENOBUFS 候选与真实视频观察，2026-10-03

上一轮已经定位一次明确的 host 中断：Darwin `send` 返回 ENOBUFS，旧 sender 撤销整个媒体会话，手机随后没有新帧。本轮完成了仅机主显式启用的有界候选，并用同一 alpha5 APK 做两轮真实视频、退出和重连。四个 host 会话都正常结束，但 **ENOBUFS 实际调用为0**。因此这是候选开启后的正常路径验证，不能称真实拥塞恢复或整体卡顿已解决。

## 候选的范围

只在独立 gateway 使用 `--allow-owner-enobufs-retry` 才启用；worker 和 sender 默认均关闭，客户端不能通过会话描述替服务端打开它。正式 App、正式 NPS、M5 日常服务没有在本轮改动。

实际 owned nonblocking sender 对 ENOBUFS 最多允许8次失败调用。前7次分别退让1、2、4、5、5、5、5ms，第8次失败即丢弃该数据报；原有混合瞬态失败总上限64仍保留。等待可以被会话取消中断，不持有认证/发送锁，不把“socket writable”误当作 ENOBUFS 已恢复。每次重新发送使用新的序号和 nonce。

视频只使用原 frame deadline，音频/ACK/feedback只使用原10ms调用预算，不续期。超过期限或次数时，视频进入已有参考链保护，优先 lane 明确计数丢弃；其他 hard errno 仍上报失败。没有把媒体回退为TCP，没有增大手机80ms、mapping cap8或4帧/2MiB Inbox。

新增分 lane 计数区分 ENOBUFS调用、直接跟随退让的syscall、socket接受后的恢复数、默认OFF fatal、期限/次数/混合上限丢弃和退让耗时。`recovered_datagrams` 仅代表 syscall 接受，不能当成手机收到或屏幕显示。

17个针对实际 sender 的 owned inert fixtures 覆盖默认OFF、显式布尔/owned策略、单次恢复、7失败后第8次成功、8失败耗尽、原期限、priority预算、取消、mixed EAGAIN/ENOBUFS、新nonce及参考链 guard；全仓1000个 unittest 通过。它们不是自然网络拥塞测试。

## 真实条件与结果

机主原UID501 M1非隔离环境，限频一加12，同家Wi-Fi、已登记Tailnet内层认证UDP。HTTPS仅认证/撤销；候选45560/45963。外层direct/relay及逐包路径本轮未验收，不代表指定公网或东北V50。

蓝M YouTube BBB `aqz-KE-bpKQ` 两轮前后实际itag299、avc1、1920×1080@60均确认；输出1080×1920竖屏、4M VBR、60cap、80ms、lead0、stage ON、startup OFF、PCM queue OFF但AAC启用，wait/guard保留。使用同alpha5/code36 APK。手机CPU上限动态变化、实际源位置与热状态没有锁定，不是受控A/B。

| 轮次 | 手机独立SF cadence | 完整30秒请求窗口FPS | 最大已观察呈现空档 | >100ms空档 | 源SF cadence | mapping容量拒绝数据报 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 59.164 | 57.758 | 149.199ms | 1 | 59.554 | 10 |
| 2 | 59.579 | 58.258 | 74.593ms | 0 | 59.594 | 82 |

两轮均持续推进，没有上轮那种约13秒无新worker/callback尾部。手机SF连续poll仍有约0.524/0.520秒未知尾部；源约0.498/0.513秒。最大gap只量相邻已观察呈现，不覆盖未知尾部。callback仍全部回显请求target，不能作为独立呈现。SF时钟与phone System.nanoTime尚未证明等价，不能由事件同窗推导光学延时。

第1轮源最大已观察gap106.598ms，手机149.199ms；第2轮分别96.026/74.593ms。不能宣称全部空档来自网络或decoder，也不能由一次较好轮认定稳定无卡顿60。两轮首会话全程Inbox溢出分别3/2、清12/8帧；这些包括初始化。FEC expiry/reference各1/1及2/2，dependency drop60/39。逐阶段事件和固定分段见JSON，不能拿全会话总量解释某个稳态gap。

四个host实际 `enobufs_retry_enabled=true`、原deadline/priority10ms、owned close均读回；16个lane的ENOBUFS、EAGAIN和send error计数全0。合计video39131、audio3832、touch ACK11、feedback79个成功发送的数据报。ACK计数不构成多指/边缘坐标验收。**此次没有执行恢复分支，正常持续运行不证明候选修复了历史 ENOBUFS**。

## 证据和收尾

APK SHA `f5cf8bbf69f9e7fe1f3babb78700abc6f254ce41b71c75f324765d9fa945da85`，JNI `578347ca24008ce7b987c77f2f1356c56761ac3d9ee4e7be89b8e9130476e402`，matching helper `d537af1660b2162eb168a402222c6c1e410e1cf042be2bdc8728ad69c96ac3cc`；原签名、手机限频和数据保留。host sender SHA `d37ff2a18f1ccb39b70c18aae97319a5c3e6c58b8316026f3c5b3153ae4af8ca`，worker `c7330ed0c56afaddd5df93ced55f019d33dd81b7b9af77adf74b24346e2bb6e9`，native packetizer仍UPTIME契约的 `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`。所有实际源码pins在结束时匹配。

四个native final齐全，自然exit0，EOF完整，无TERM/KILL；无session failure。每个host final的ingress是stop invoker，记录为仍alive/excluded，不能说各final中所有线程均已退出。最终gateway另行确认quiescence、stop failures0、exit0，helper及一次性输入删除、45560/45963关闭。没有新建账号或写CPU限制；原账号正常认证。机主手机保留alpha5，正式更新渠道仍v1.30。

公开裁剪数值见 [JSON](udp-enobufs-real-video-20261003.json)；秘密和原始日志保留在受限、忽略的 `docs/evidence/udp-enobufs-retry-20261003/`，不提交。上一轮失败保留在 [startup记录](codec-startup-ready-real-video-20261003.md)，不能用本轮无错误抹去。

## 下一因素

ENOBUFS候选保持默认OFF；自然压力覆盖仍缺，不能无限重跑相同低压力轮来冒充恢复验收。重复出现的 adapter8/core0 容量拒绝提供了更有区分力的下一因素。既有代码会让已被core settle的较新mapping占槽直到自身原80ms期限；这是有界生命周期浪费的机制，尚未拿到真实八个占槽ID/逐帧settle原因，不能仅凭pending0全部清空。

下一轮先加定长core settle原因/ID/时间与容量占槽快照，保留现有292字段契约和64KiB报告边界，不改变cap8/80ms/参考链。取得逐帧证据后再比较仅“明确已settled的ID立即retire”的机主单因素，保留tombstone防止晚到分片重建grant。见 [生命周期分析](native-mapping-capacity-lifecycle-20261003.md)。取消duringprepare helper只是源码准备；尚未做真机该阶段取消。音画、真实多指/旋转/边缘和指定公网路径仍需分别验收。
