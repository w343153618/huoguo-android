# 2026-10-03：真视频 decoder / Inbox 分层观察基线

这份报告只整理四轮已完成基线。新指标已经把启动 configure、UDP/FEC 恢复、Inbox burst 和 input API 等待区分开；本轮还没有把所有长空档消除，也不足以估计诊断开关的因果开销。正式 v1.30 与生产服务没有切换。最新 helper 进度保护和 host sender 候选属于下一步，未计入本报告，也没有声明复测通过。

可提交的安全数值摘要为 [decoder-queue-real-video-20261003.json](decoder-queue-real-video-20261003.json)。完整私有数字证据保留在 `docs/evidence/decoder-queue-observation-20261003/`，其中 `stage-association-analysis.json` 包含完整固定段、事件及窗口关联，权限为 0600。这里不复制凭据、私人截图、媒体内容或原始敏感日志。

**实际环境和配置。** M1 原 UID501 机主环境，未完成宿主/LAN 隔离验收；手机为保留限频的一加12，仅是真我 V50 的性能压力替身。两端同家 Wi-Fi，经已登记 Tailnet 地址的内层认证 AES-GCM UDP 串流。本轮没有逐包验证外层 direct/relay，也没有公网、移动流量、东北 V50、物理触控或声学验收。受信 HTTPS 仅用于已有账号认证和会话描述。

四轮使用同一个实验 App `1.31-alpha.3 / versionCode 34` 及同一个 helper，顺序为 off/on/on/off。采样请求每轮稳态30秒，App/helper等待段约32秒；实际可用SF前缀与这个请求时长分别记录。串流实际为1080×1920、60FPS cap、4,000,000bit/s VBR、80ms buffer、surface lead0、PCM可选队列关闭，AAC音频仍启用；宿主 packetizer wire限速为32,000,000bit/s，接收端从首次成功准入该frame Mapping的shard起给予80ms assembly grant；如果cap8先拒绝了早到shard，不能把grant起点写成首个认证shard。本轮未做声学测量，不能验收声音与画面的真实同步。

源为公开 Big Buck Bunny 的 `aqz-KE-bpKQ`，蓝色m标志的YouTube客户端。每轮前后读回均为itag299、avc1、1920×1080@60，profile标签为visionOS 1.02。实际启动位置第1轮为1分33秒，第2–4轮为1分35秒；不能把请求的90秒位置当实际达到90秒。真实视频内容唯一帧没有独立hash验收，SF只代表选中Surface的呈现节拍。

**四轮观测结果。** source/phone cadence使用首末实际SF呈现之间的间隔；full-window FPS使用观察到的帧数除以整个请求时长。后者在会话停止或未知尾部时不能当活跃串流帧率。

| case | 诊断 | 源cadence FPS | 手机cadence FPS | 手机full-window FPS | 手机最大空档ms | >100ms |
|---|---|---:|---:|---:|---:|---:|
| 1 | off | 57.233 | 43.217 | 6.433 | 621.653 | 3 |
| 2 | on | 59.898 | 56.523 | 55.285 | 737.685 | 3 |
| 3 | on | 59.796 | 59.349 | 57.786 | 190.641 | 1 |
| 4 | off | 59.901 | 58.941 | 57.560 | 273.520 | 2 |

case1-off不是有效开销基线：宿主video Python socket.send调用经过时间最大101.110ms，命中100ms socket timeout，video lane有1次send error，network-feedback也观测到约101.699ms及send error。整个媒体会话随后撤销；native packetizer最终自然结束，457输入帧全部输出，手机只收到449帧，接收loop仍存活，后段Surface静止。手机全窗口只有6.433FPS，而活跃首末呈现间cadence为43.217FPS。这个字段包含Python timeout/readiness等待及可能的调度间隔，不能证明一次内核send syscall阻塞101ms。不能拿6.433FPS推断diagnostics-off性能，更不能把它与on轮比较来估计遥测开销。native packetizer预算丢0也不能否定它下游的UDP send失败。

其他轮的源cadence接近60，手机仍有190–738ms长空档。case2/3/4的宿主native final分别2153/2222/2156 source帧，同数output帧，frame budget及dependent source drops为0，video send errors为0；这只证明冻结packetizer及其summary覆盖到的行为，不证明每帧都在手机按时完整收齐。

**启动阶段与稳态不能混算。** case2 configure包装调用184.084ms；期间有两次4帧Inbox overflow。随后的首次config reservation入口flags=11，即ageExceeded(1)、staleEpoch(2)、config(8)同时成立，frame epoch0/current epoch2；polls为0，reservation跨度仅12.187µs。这次timeout没有实际调用vendor dequeue等待，不能解释成codec等待184ms才交还input slot。configure计数的success表示包装函数正常返回；内部running/generation变化可早退，它不是SDK初始化成功证明。

case3 configure为232.283ms，也有两次启动overflow及一次timeout。事件总量70、固定环64，最早6项已逐出。configure聚合和consumer启动跨度仍在，但不能重建已经丢失的具体guard flags；此项的证明强度低于case2。

**case2稳态的三段长空档。** 以下为同端时钟范围一致条件下的窗口关联，未独立验证Java/SF时域身份，不把关联提升为因果或单向延时：

| SF空档ms | 邻近或重叠的观测 | 同段未出现的长期阻塞 |
|---:|---|---|
| 687.968 | 两次4帧overflow；offer间隔82.976/97.459ms；take间隔179.638/464.820ms | consumer约4.924–6.144ms、reserve约0.921–2.247ms，未有几百ms输入调用跨度 |
| 737.685 | FEC poll先expiry1/refLost1，后dependency39及frame mapping reject106；恢复offer776.418/take775.867ms | 所在1秒段consumer最大14.381ms、reserve最大8.659ms |
| 149.194 | 一次4帧overflow和take132.219ms | 同秒consumer最大6.287ms、reserve最大1.529ms |

737.685ms段的ready−input最大785.009ms，说明之前已提交的输出后来才ready，不能单凭这个量把等待全部归于GPU或decoder执行。此时缺少可交给consumer的新完整帧、FEC/依赖恢复与长offer/take间隔一起出现；具体哪条分支先触发尚未分清。其他58.014/74.600ms SF空档也记录在JSON中，不能凭附近没有环事件就判定其无异常。

case3在实际SF窗内，190.641ms空档邻近一次4帧overflow及183.455ms take间隔。600.909ms offer、661.006ms take及另一轮FEC恢复出现在SF最后有效presentation之后；它们属于窗外尾段，不能用于解释采样窗内的190.641ms最大值。

**新阶段指标的全会话范围。** 下表含startup，不能当30秒稳态均值。consumer含旧的JSON/记录/hooks及输入处理；wall减thread CPU只表示非CPU经过时间，不能直接归因为调度。copy范围包含getInputBuffer、容量检查、epoch guard与copy；inputCall只含有数据的queueInputBuffer，不含归还空reservation的取消调用。

| 指标ms（mean / max） | case2-on | case3-on |
|---|---:|---:|
| consumer wall | 2.567 / 184.292 | 2.565 / 232.523 |
| consumer非CPU经过时间 | 2.001 / 117.760 | 1.946 / 135.056 |
| input reservation loop | 0.545 / 11.275 | 0.550 / 10.967 |
| input copy span | 0.116 / 12.556 | 0.102 / 9.186 |
| 非空queueInputBuffer span | 1.544 / 20.738 | 1.530 / 18.051 |
| ready−input | 25.604 / 785.009 | 26.050 / 672.873 |
| target−release | 69.834 / 244.390 | 77.314 / 302.441 |

全会话configure/consumer峰值184/232ms来自启动。两轮没有decoder hold gain、decoder reanchor或hold decay；case2有234次offset slow decay，累计−205.196ms，case3为291次、−278.075ms。因此本轮长空档不能说成decoder反馈不断强行增加共享clock hold。小幅decay仍计入统计，只有锚点/重锚或≥20ms变化占事件环。

旧字段clock_mapping_rejected与PlaybackClock是两套机制。`phone_receiver.hpp`的frame header→本机arrival Mapping在frame落后highest≥256、活跃mappings达到8、或同一frame header不一致时共用这个拒绝counter。本轮106次以拒绝的datagram计数，无法分这三支，不是106帧，不能称为Java时钟重锚失败，也不能直接认定就是容量8。FEC事件时间为nativeStats返回后的100ms轮询观测，不是异常发生时间；没有准确frame身份，max poll interval包含结束收尾。

case2最大本层wire frame301,264字节，在32Mbit/s下的计算发送预算75.316ms，距80ms assembly grant约4.684ms；实际phone max assembly为78.403ms。case3对应295,116字节/73.779ms/73.512ms实际assembly，case4为286,910字节/71.7275ms/77.351ms。这个余量提示应检查大帧、burst与assembly恢复；预算计算不是实际跨网络延时，尚不能指认是哪张帧引发长空档或认定它就是IDR。

**覆盖和比较界限。** 两轮on都启用了coverage mask255，61新增总计数、9新增直方图、17 active phase数值，加原14总计数/5直方图。1秒段上限120，事件固定64；offer/take ring阈值80ms、consumer/API stall20ms，完整直方图与每秒最大值不随ring eviction丢弃。case2事件62/保留62/evicted0，case3为70/64/6。固定前2秒只表示初始化时间带，不自动宣称后续全部稳定。

四轮SF观察poll链都连续，invalid/unverified均0，但请求窗口末尾没有完全观察到，不能把完整poll链等同整个30秒完全覆盖。phone未知尾部依次810.245、503.947、606.090、525.558ms；source未知尾部247.208、502.761、419.560、260.513ms。analysis保留了SF首末实际ns、App起止及helper steady标记；范围同端嵌入一致，sf_time_domain_verified仍0。源端与手机单调时钟没有映射，源gap最大值不能直接定位对应手机gap。

codec callback在case2/3分别2015/2057项，全部精确回显请求target；这些callback不能当实际显示帧时间戳。独立SF也不证明内容唯一帧、光学触控延时或声学音画同步。

CPU限制没有被本实验写入，但用户保留的动态限频在运行中变化。以下是policy max读回，不能当实际执行频率，也不构成严格同CPU的ABBA：

| case | max before kHz | max after kHz |
|---|---|---|
| 1 | 1689600, 1612800, 1190400, 1132800 | 672000, 1190400, 960000, 902400 |
| 2 | 672000, 960000, 960000, 1248000 | 672000, 1075200, 1824000, 1939200 |
| 3 | 672000, 960000, 960000, 902400 | 672000, 960000, 960000, 902400 |
| 4 | 672000, 960000, 960000, 902400 | 672000, 1497600, 1612800, 1593600 |

**冻结产物。** App与helper安装后的SHA均和下表一致。所有实验脚本/source pins在四轮中保持不变；完整source pin列表保存在安全JSON中。以下只列这轮真实使用且已记录的可执行依赖，没有声称整个macOS/AVD供应链都做了hash冻结。

| 产物 | SHA-256 |
|---|---|
| experimental_app_apk | `d87bcfd8040db3440ce7b37faa96b0b2c3c00a687c7cbef80d8d47e0f668ccbf` |
| ui_acceptance_helper_apk | `77d3526f4830225cddbe11b7d9ae5ac2d10d9a713cb1cc5fd1c1613907a24e59` |
| uptime_packetizer | `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142` |
| session_pool_encoder | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` |
| scrcpy_audio_control | `823faf9c95c64d4ca8740c8c36596c30eb89ce09e112d218402962026e2b9c47` |
| phone_jni_native_udp | `99365c6741d5bdd35588fb906c17c1fc8f5fcba0a247130bc553212c31ae63c6` |

packetizer为`CLOCK_UPTIME_RAW`版本，实际external runtime为`/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`；encoder为`/private/tmp/huoguo-session-pool-encoder`，scrcpy helper为`/private/tmp/huoguo-authenticated-lan-udp/runtime/hardware/scrcpy-audio-control`。不要与历史冻结binary混用。Java observer/metrics源码hash分别为`0a2bedf9be3c32f02ef700de826a8d36dbf943e719d6135dfe6d10e7b5e9edd1`/`c074296da5eed1e3a5ece9bce44c062209d008e7c972037896c17ffe573b9df0`，Probe为`65e477e860739b5f881d55268bf9588ec59c7db660d3399747e28ecfb53266e4`。

有意义的offline/JVM fixture已检查旧constructor默认不启用stage、observer策略等价及首次delta缺测、零增量FEC poll、guard同时age/stale、阶段缺begin/active span、并发固定环及snapshot不别名。actual converter的120段/64事件最大形状numeric fixture为50,188B，5000audio字段超限明确fail-closed。这个证据不是ART/codec/Surface并发采样开销验收。三owner host JVM warm微基准on483–581ns/round、off11–28ns/round也不能替代手机开关对照。

**下一步尚未复测。** 保持lead0/80ms/4M/1080/60cap，先区分frame mapping拒绝的三支及大帧assembly/IDR恢复；把startup configure单独处理；修复helper的进度停止识别和sender失败传播后再采样。新的helper进度保护与host sender候选只属于这些下一步，未包含在四轮冻结SHA或结果中。不能把本报告叫作稳定无卡顿60FPS、正式UDP产品、V50或公网验收。
