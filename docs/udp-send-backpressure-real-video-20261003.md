# 2026-10-03：独立 UDP sender 候选的真视频复测

本轮两次完整播放及两次短重连均未出现上一轮的 UDP send timeout，退出和 native 收尾正常；手机独立 Surface cadence 为59.430/59.437FPS，仍各有一次232/174ms长空档。这些长空档邻近 Inbox overflow 与恢复供给。本轮既不能证明 sender timeout 永久修复，也不能称为稳定无卡顿60FPS。

这是[四轮 decoder / Inbox 基线](decoder-queue-real-video-20261003.md)之后的独立复测。相同 alpha3 App 保留，helper 增加进度停止识别，宿主启用独立 owned nonblocking writer 候选；不能把本轮改善直接归因为诊断开关或某一个参数。安全数值摘要为[udp-send-backpressure-real-video-20261003.json](udp-send-backpressure-real-video-20261003.json)。完整证据在 `docs/evidence/udp-send-backpressure-followup-20261003/`，原始日志、凭据、私人截图与媒体内容不提交。

**实际路径和内容。** 使用原 M1 UID501 机主环境，尚未完成宿主与 LAN 隔离；一加12保留用户限频，只是真我 V50 的压力替身。两端同家 Wi-Fi，使用已登记 Tailnet 地址的内层认证 AES-GCM UDP；受信 HTTPS 用现有账号完成认证及会话描述。没有逐包验证外层 direct/relay，也没有公网、移动流量、东北 V50、原生触控、光学延时或声学音画同步验收。正式 v1.30 仍未切换为 UDP。

两轮实际串流参数一致：1080×1920、60FPS cap、4,000,000bit/s VBR、80ms buffer、surface lead0、stage diagnostics on、PCM可选队列关闭，AAC音频仍启用，packetizer wire限速32,000,000bit/s。接收端80ms assembly grant始于该frame Mapping首次成功准入的shard；cap8拒绝的早到认证shard不能作为grant起点。本轮未做声学测量，不能验收音画同步。

源为公开 Big Buck Bunny `aqz-KE-bpKQ`，使用蓝色m标志的YouTube客户端。每轮前后均读回itag299、avc1、1920×1080@60；实际启动位置分别1分34秒与1分35秒，结束位置2分24秒与2分29秒，不能把请求90秒位置当作精确达到90秒。格式一致不代表两轮逐帧内容或源播放位置完全相同，也没有内容唯一帧hash验收。

**两轮真实观察。** cadence使用SF首末实际presentation间的间隔；full-window FPS使用帧数除以整个请求窗口，包含未知尾部，不能替代活跃cadence。

| case | 源cadence FPS | 手机cadence FPS | 手机full-window FPS | 手机最大空档ms | >100ms | >50ms |
|---|---:|---:|---:|---:|---:|---:|
| 5-on | 59.901 | 59.430 | 57.447 | 232.056 | 1 | 3 |
| 6-on | 59.932 | 59.437 | 57.655 | 174.035 | 1 | 2 |

源端最大空档分别75.389/54.807ms，均无>100ms。源端与手机单调时钟没有映射，不能将源最大值直接定位到某个手机空档。codec callback分别2092/2111项，全数精确回显请求target，不能当真实显示帧时间戳。表中手机cadence来自独立SF，仍不证明每帧内容独特、零撕裂或物理输入延时。

新helper分别得到33项有界进度样本，worker received与codec callback计数持续增长，没有达到3秒no-growth停止阈值。这是全会话停止识别，不能排除232/174ms的帧间空档，也不把callback计数作为presented FPS。`progress_max_idle_ns`约4.628/1.970ms只是采样记账值，不能宣称最大画面空档、网络或触控延时只有几毫秒。

helper通过ADB实际读取的首份App JSON为27,767/29,728B，短重连最终报告为17,943/18,118B，均低于64KiB。这不是向服务器上传；这里使用helper实际读取的JSON字节数，不使用本地缩进文件大小替代。App实际参数读回lead0/status0/stage1，观察窗口验证为1。

**长空档与Inbox/FEC的关系。** 下列关联以同一手机Java、SF和helper时间范围嵌入一致为条件；`sf_time_domain_verified=0`仍保留，使用空档两侧50ms的粗关联边界，不把时间接近当因果或单向延时。

| 手机SF空档 | 相对App origin | 邻近事件及同秒聚合 | 未观察到的同段长耗时 |
|---|---|---|---|
| case5：232.056ms | 6.587–6.819s | offer/take109.478ms在空档开始后约32ms；4帧overflow在+34.489ms；stale epoch guard在+35.217ms；take162.944ms在+195.393ms。bin6 offered60/scheduled48，overflow1，FEC增量0 | consumer max11.868ms，reserve max2.747ms；不能归为232ms单次input API阻塞 |
| case6：174.035ms | 16.150–16.324s | offer83.425ms在+1.530ms；4帧overflow在+3.739ms；take213.073ms在+131.297ms。bin16 offered60/scheduled52，overflow1，FEC增量0 | consumer max6.812ms，reserve max1.698ms；本秒无clock mapping变化 |

两轮各有3次whole-session Inbox overflow：case5为启动2次、bin6一次，合计清12帧、waiting IDR34；case6为bin0与bin1各一次、bin16一次，清13帧、waiting IDR173。case5长空档同段ready−input最大290.779ms，case6为225.858ms；这表示已提交input之后较晚才ready，不能单凭这个量把等待全归于decoder/GPU计算。

FEC的whole-session总量与SF有效观察窗应分别看。case5有expiry1/refLost1/dependency9/frame Mapping拒绝15，全部26项异常delta在bin34、origin约34.854/34.954秒的poll才观察到，晚于SF最后presentation33.349秒。case6有expiry2/refLost2/dependency36/Mapping拒绝91、recovered shards2，131项异常delta都在bin0，早于SF首次presentation7.697秒。两轮实际SF有效前缀内都没有保留的FEC异常poll事件，对应覆盖的1秒段FEC delta也为0。因此不能用这些全会话FEC总量解释当前稳态232/174ms空档；本轮更直接的关联是Inbox供给间隔、overflow及IDR恢复，但还没有分清先后触发机制。

Mapping拒绝以被拒绝的datagram计数，未拆分header不一致、frame落后highest≥256、活跃Mapping达到8三支；不能称15/91帧丢失，也不能据此认定都是cap8。FEC时间是nativeStats返回之后的轮询观测，不是异常发生时刻，final poll可能包含收尾。没有异常poll不等于逐包网络或FEC绝对无问题。其他58–66ms较小SF空档附近未必有80ms阈值的事件，不能因环里无事件就宣称正常。

**启动与输入阶段的范围。** 以下含startup和窗外尾部，不是30秒稳态均值。

| whole-session指标ms（mean / max） | case5-on | case6-on |
|---|---:|---:|
| configure包装调用 | 219.753 | 218.470 |
| consumer wall | 2.525 / 220.396 | 2.527 / 219.060 |
| consumer非CPU经过时间 | 1.952 / 127.829 | 1.939 / 126.194 |
| input reservation loop | 0.555 / 20.232 | 0.551 / 9.580 |
| input copy span | 0.101 / 6.120 | 0.102 / 6.807 |
| 非空queueInputBuffer span | 1.494 / 21.369 | 1.502 / 14.423 |
| ready−input | 25.060 / 290.779 | 24.842 / 225.858 |
| target−release | 72.370 / 241.365 | 67.315 / 206.102 |

case5启动configure期间有两次overflow，首次config reservation entry flags11（ageExceeded+staleEpoch+config）、frame epoch0/current2；其timeout polls0，reserve跨度0.362ms。case6相应entry flags9（ageExceeded+config），入口并未stale，polls0、reserve约24.167µs；后面的stale guard是另一事件。不能将两轮timeout统一说成vendor dequeue等了218/220ms或都因stale epoch。case5全会话20.232ms reserve峰值在startup约1.761秒，不是232ms稳态空档里的峰值。

configure success只表示包装函数正常返回；running/generation变化可早退，不能作为SDK初始化成功证明。consumer含旧JSON/记录/hooks及输入处理；wall减thread CPU仅为非CPU经过时间，不能直接认定调度延迟。copy包含getInputBuffer、容量检查、epoch guard与copy；inputCall只含非空queueInputBuffer，不含归还空reservation的取消调用。

clock observer覆盖实际映射变化：case5 offset slow decay237次、累计−213.616ms，case6为177次、−267.651ms；两轮decoder hold gain、decoder reanchor与hold decay均0。因此没有支持“持续增加clock hold造成当前长空档”的证据。小幅decay保留总量/分段，只有大变化与锚点变化进入固定事件环。

**宿主sender候选真正覆盖了什么。** 候选在worker已有socket上dup一个本会话拥有的nonblocking writer；reader原100ms timeout保持，writer timeout0，local endpoint与peer读回相同。默认通用sender的legacy policy不变，仅候选启用owned nonblocking deadline。媒体仍是认证UDP，未静默转TCP。

| actual policy读回 | 值 |
|---|---|
| send policy | owned_nonblocking_deadline |
| writable wait slice | 5ms |
| priority budget | 10ms |
| 最大would-block attempts | 64 |
| video超预算 | PacingDeadline，沿用reference依赖guard |
| priority超预算 | drop计数，不计sent |
| 重试认证nonce | 每次fresh seal |
| wait期间认证send mutex | 不持有 |

两次main及两次reconnect所有lane的send errors、would-block、retry、writable wait、would-block expiry/drop均0。case5/6 main的video `max_send_syscall_ns`分别1.697/2.399ms，发送17,377/19,090datagrams。这是Python `socket.send`调用经过时间，可能含readiness与调度，不能称纯内核syscall耗时。没有真实would-block样本，所以重试、5ms wait与deadline-expiry行为只在owned fixture得到验收；本轮仅证明候选在这两个路径窗口正常工作，不能证明在丢包/拥塞/WAN下必然恢复。

case5 main native source/output2162/2162，budget/依赖丢0；case6为2350/2348，有1次frame budget drop及1次dependent source drop。最大本层wire frame分别294,476/334,732B，在32Mbit/s下计算为73.619/83.683ms。后者超过80ms assembly grant，提示大帧预算仍需检查；汇总缺少逐帧身份，不能将该峰值直接认定为特定IDR或某一次预算丢的精确原因。手机实际max assembly74.089/70.452ms只覆盖成功组装样本，不代表被拒绝或过期的帧。

**覆盖、收尾与比较边界。** Java/SF/helper范围一致，但时域身份未独立证明。case5手机SF有效首末为origin+4.358至33.349秒，case6为+7.697至36.786秒。两个SF poll链连续、invalid/unverified均0，仍各有unknown tail：phone831.967/755.756ms，source308.074/327.864ms，不能把完整poll链等同完整请求窗口。case5事件51/保留51/evicted0，case6为68/64/4；case6早期4项已逐出，不能完整重建启动FEC事件，但固定1秒聚合保留。阶段mask255表示hook安装覆盖，不能保证每种事件都被完整观测。

CPU policy max before/after（kHz）case5为`1689600,1824000,960000,902400`→`672000,960000,960000,902400`，case6前后为`672000,960000,960000,902400`。未写入用户CPU限制，这些读回也不是全程实际频率。源播放位置、动态CPU状态、helper与sender共同变化，两轮不构成可估计某单因素收益的严格A/B。

全部4个host session都有完整native final，stdin/stdout/stderr EOF确认，native自然exit0，没有TERM/KILL，cleanup confirmed且无errors。线程收尾中ingress显示alive属于stop invoker明确excluded；其他线程均不存活，不能把它当泄漏。App退出、重连、重新认证正常，leave后running false、音频线程0。实验gateway退出0，候选15560/15963端口已关闭，helper已移除，正式受信ping正常、VideoToolbox后端正常，M1节点46、M5节点23、一加12节点48均保留。

**真实使用的冻结产物。** 安全JSON保留完整source pins；以下SHA属于本次两轮实际依赖。

| 产物 | SHA-256 |
|---|---|
| experimental App，alpha3/code34 | `d87bcfd8040db3440ce7b37faa96b0b2c3c00a687c7cbef80d8d47e0f668ccbf` |
| 新progress helper | `ee598169d1b30074c4c38cd7e8a969ae5491f51b3965c40b480caac62f19b97f` |
| UPTIME packetizer | `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142` |
| session pool encoder | `59264ab7cd9d6e76ed3328a117bbf3c866513bc505244203c2328d6808879ecf` |
| scrcpy audio/control | `823faf9c95c64d4ca8740c8c36596c30eb89ce09e112d218402962026e2b9c47` |
| phone JNI UDP | `99365c6741d5bdd35588fb906c17c1fc8f5fcba0a247130bc553212c31ae63c6` |
| actual host worker | `ac3abd08e3e9a5dba53bd6b9f3d0658e11a315f4d4f4ac5587453076cc6278f9` |
| actual host sender | `7f90a12ef8df09975d1ffdd2a3f2dbcf7d99b36e16f27427ce21b06696929535` |

external runtime仍为`/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`、`/private/tmp/huoguo-session-pool-encoder`、`/private/tmp/huoguo-authenticated-lan-udp/runtime/hardware/scrcpy-audio-control`，与历史冻结binary区分。helper源码`147e45b401f08f678246fece41b04270394a746f960099f4c0217a1b7c12d808`、driver`7aa9a6980ba6801e61591153d957cd08cc59c812afde19ffb9dc75a216b806e4`在本次campaign保持不变。

campaign之后仅补closed-FD `select ValueError`操作注解及fixture，最终sender源码SHA为`36fbebc4cf2cef4a58ea28e5d9302dad51ae5eea218e7e557161dcdb03854b0b`；不能追认该SHA已经过这两轮真机验收。最终全仓939项unittest通过、耗时20.356秒，正式与实验assemble/lint均通过，正式candidate没有安装或发布。owned writer相关fixture覆盖真实本地duplex及受控would-block等分支，但不是公网手机验收。

下一次最小区分实验应保持媒体行为及lead0/80ms/4M/1080/60cap参数，先拆frame Mapping拒绝原因并取得大帧/assembly的可定位身份，再区分burst供给与Inbox恢复；新增native诊断必须记录新的JNI/APK SHA与独立受测范围，不能称同一APK。不要因两轮cadence接近60就扩大高码率或高刷矩阵。startup configure也须单独处理。朋友/公众新版仍需宿主及LAN隔离，真实指定公网路径、V50、音画与原生多指触控分别验收。
