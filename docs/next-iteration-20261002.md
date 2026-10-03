## 2026-10-03 当前 checkpoint：alpha8 已发布，先校正源环境读回

公开测试版已经是 alpha8/code39，精确 artifact/source `73d196a`、APK `a663c4d0`，不能再把它当未发布候选。两台 owner gateway 已为冻结 `dd43a39`，公网单会话3600秒、进程上限0；App 媒体为认证 UDP。交付、更新 UI 以及物理30Hz记录见 [alpha8发布记录](experimental-alpha8-30fps-onehour-release-20261003.md)。下面早期 alpha7/alpha8候选段落是历史 checkpoint，不替代本段或末尾的新读回。

新只读检查发现 M1 冷启动后的 App HWUI 目标已经从历史 `skiavk` 回到 `skiagl`，实际 YouTube PID3391 的 Pipeline 为 `Skia (OpenGL)`。两份 guest ADB 图片和一份认证宿主 gRPC RGBA 单帧都显示 App 内容局部上下翻转/压缩，系统栏正常；这不经过 encoder 或 UDP，但仍不是物理窗口独立验证。当前播放的是 LIVE 动画页面，不能假定本轮仍是 BBB。保留官方 RenderEngine `skiaglthreaded`，不做全图翻转补偿、不混刷新率改动；见 [源方向只读鉴别](source-ui-capture-orientation-readonly-20261003.md)。手机当前稳定版在前台，本次没有抢用、重开源 App 或改属性。

alpha8既有135秒公网 M1 轮的离线分析见 [接收供给与长空档](alpha8-m1-public-gap-analysis-20261003.md)：接收45个区间平均27.608/s，独立SF最大265.225ms、78个>100ms空档；单次Inbox溢出等累计计数无法对应这些空档。下一安全机主窗口先固定并读回真实内容、Pipeline与CPU限制，再做45秒同参数源SF/host AU/手机接收与SF分层诊断；必要的事件列导出单独验证开销。不得以新的只读图像给历史135秒实验追认内容或根因，也不重复宽矩阵。

## 2026-10-03 早期：UDP 唯一性能研发方向，V50 客户端候选完成

用户实际体验认为 UDP 显著优于旧 TCP，明确停止 TCP 媒体性能优化。后续只沿认证 UDP 收发、解码／Surface 队列、控制和音画时钟降低延时；正式兼容入口与无关 NPC 运维仍保护，不做 TCP 比较矩阵。用户主观改善是反馈，不能改写成量化一程延时。

V50 适配候选 alpha8/code39 已构建/lint、1252 checks 全过，实际限频一加12验收预设按钮以及修改60FPS/reopen保留，见[V50候选](v50-beta-adaptation-20261003.md)。旧0=60/1=120索引保留，30追加2；MTK新安装与一键预设540x960/4M VBR/30/80ms，低负载阶段诊断off。服务端源码支持30并回显display_hz0，手机柔性hint至少60Hz，不改全局Hz。已保存参数不覆盖。H264/H265查询仅厂商声明，实际传输H264，尚未V50持续播放。

**公开最新版仍alpha7，alpha8尚未发布，运行owner gateway仍a776828而不支持新30。** 下一轮先在已有账号认证下做有界M1真实30媒体并安全更新owner gateway兼容，不能先更新下载清单让用户点30得到失败；不抢活跃会话。使用原冻结进程自然退出的方式或实现原子idle→sticky admission drain，不能lsof→kill竞态。朋友/public新入口必须完成宿主与LAN隔离；本轮M5只读发现emulator UID502与yawen同身份，正式状态未改，不当隔离通过。无V50时不能以限频一加替代其MTK硬解/热/功耗验收。

随后固定真实60FPS内容、格式与位置，对UDP stage有界采样，优先解释接收突发、Inbox参考链与codec输出持有；不先扩大容量或关guard。80ms推荐；50/80ms比较必须同源单变量并记录独立呈现长尾、丢帧与物理控制测量边界；100ms可选不作为掩盖根因。多主机密码55f48fa仅设计完成，尚未实现，暂不抢本轮V50/UI文件。

## 2026-10-03 alpha7 已交付，立即继续

稳定1.31/code32与实验1.31-alpha.7/code38已发布；原签名、GitHub三个资产与NPS完整APK实读通过。alpha7实际一加12现有huoguo认证公网M1，保存/重开/清除/重开/再保存，以及两轮返回确认继续/退出/重认证全部通过。见[发布记录](experimental-alpha7-ux-release-20261003.md)。当前root精确artifactsource3387b56，APK1f4c6c1b；不要用较新文档HEAD冒充artifact源码。完整1242源码检查通过，不当蜂窝/V50/朋友安全或60FPS验收。旧首次助手提前检查posted回调的失败保留，密码partial全部为true。

M1/M5独立LaunchAgents已等旧候选自然到期后运行，持久sourcea776828，闭集显式wyw/huoguo、现有认证/账号归属与120秒session限制；没有signal正式/NPC/NPS，也没改账号密码。记录见[生命周期](owner-udp-launchagents-20261003.md)。推荐80ms、100ms可选不变。公网媒体UDP，NPS内部quic.Stream仍不同于Datagram；真实VPN-on及逐包国内出口尚未验收。手机Tailscale App仍为此次VPN-off基线停用而身份保留，不注销重注册。

下一项已由codec_startup_design在其独立worktree开始仅源码设计：多主机各自保存加密凭据、有界组数、旧单条安全迁移与明确clear-current/all，原正式存储不变；先fixture与review，不抢root/phone/live。再独立验证Tailscale-on物理socket是否被allowBypass/lockdown拒绝，必要时采用明确每App例外，不把API绑定当公网P2P/国内路由证据。读取实际node/packet/interface后才推广。保留现有M1/M5数据、phone限频及有效yilufa节点；任一正式/机主候选在线均避免打断。

## 2026-10-03 NPS KCP／QUIC 缺项已定位，修改前备份完成

最新维护（2026-10-03中午）已按用户单次明确授权完成：正式NPS已重启为both，KCP/QUIC已恢复，M1/M5正式NPC已用QUIC通过物理回环UDP48126到云UDP8025，38个原在线客户端全部重连。见[正式QUIC部署记录](nps-formal-quic-deployment-20261003.md)。这一步不再重复重启，不改其他NPC或过滤；App v1.30仍TLS/TCP。后续恢复M1 codec-ready实验，设计checkpoint见[启动设计](codec-startup-design-20261003.md)，正式M5日常使用保持。

最新用户约束及部署：所有正式NPC（含其他服务器日常运维）在线时都不能被打断，不能因Android空闲就重启正式NPS。独立实例已迁为TCP48024、KCP/UDP48025、QUIC/UDP48026、测试任务TCP48028，本机测试回环TCP48027/KCP48025/QUIC48026；原正式回环18024保留。三协议现有专用测试身份认证均通过物理en7，用后自己的进程退出0，无任务/媒体流量。云增量备份114640、M1配置备份114731已保留；正式PID2242499及配置SHA、国内过滤未变，旧UDP8024/8025已释放。读[迁移记录](nps-high-port-migration-20261003.md)。正式tcp开关和缺项仍未恢复，协议测试只在高位独立实例进行，不重新占用低端口或重启正式NPS。

用户要求核对腾讯2号的NPS来源、KCP/QUIC缺项，并在修改前备份。新检查确认正式v0.34.7实际程序与重新下载的官方发布包一致，55个网页文件无差异；正式`bridge_type=tcp`分别关闭两协议的运行入口和默认展开命令。9月11日备份已tcp，不能归因是谁修改。实验NPS占UDP8024/8025是恢复时需要处理的冲突，不是正式菜单缺项的直接原因。读[本轮诊断与备份记录](nps-quic-visibility-20261003.md)。

云端受限备份`/root/nps-backups/before-kcp-quic-20261003-112543/`已完整校验（82个归档文件，15个配置副本核对），最小`bridge_type=both`候选仅保存于该备份的staged目录，正式配置未改、两个服务未重启。Android公网线路空闲不代表整台NPS空闲：后续仍有其他任务90个已建立前端socket、36个端口，因此本轮没有打断它们。新增监听不能靠配置reload；不得用v0.34.7的`nps reload`硬编码信号30。恢复时必须重新核对会话、备份后的配置变化、实验UDP占用以及官方监听/网页/旧NPC/实际客户端验证，不只打开网页显示开关。不改变国内过滤，正式App仍TLS/TCP；UDP隧道类型、NPC QUIC stream和端到端媒体Datagram分别记录。

## 2026-10-03 M5尺寸维护已实测对齐

按用户要求，M5日常源固定物理720×1280/density320；实际部署worker三个host-only编码轮确认540P→540×960、720P→720×1280、1080请求→720×1280，均苹果硬件ready，不当作手机FPS验收。旧5分钟计时已改环境0，旧idle模块误把0当立即执行也已补正式契约和8项检查。单次有锁维护已在正式会话自然结束后完成重载，受限receipt状态reloaded_after_formal_idle、sourceSHA ae7ed823、no_vm_restart true，加载后及无keepalive10秒均Awake；再次只读确认模块SHA匹配、gateway运行和受信ping200，不能重复建watcher。10秒读回不是无限期常亮压力验收。公网/局域网受信ping正常，M1物理1080×1920未改。见[维护记录](m5-standard720-tiktok-20261003.md)。TikTok原版47.0.3验签/安装/启动通过，但当前首启要求登录，没有找到游客入口；已询问现成账号自行登录，未验收真实视频。用户登录前不重复安装或替他注册；在M1继续现有下一轮准备，不改NPS/Clash全局或抢占M5。

M1并行只读preflight和matching helper编译也完成：helperSHA35c92b41匹配alpha4/code35，原签名；M1源仍1080×1920，正式1.30和CPU限制未改。私有build-pins已补helperSHA，单case完成标签已修；实际M1auth仅有huoguo，wyw验证在安装前拒绝，未修改账号；随后使用此前明确授权的已有huoguo账号完成alpha4安装及单轮真实映射观察（driver0、gateway0、helper已卸载、端口关闭），不是朋友会话，也未新建临时账号。实际结果已分析并保存[报告](native-mapping-observation-real-video-20261003.md)与[公开数值](native-mapping-observation-real-video-20261003.json)：新mapping读回366/64次均status1/enabled1，拒绝全0、未观测0、活跃最大1，没有异常事件；不能解释历史106或扩大cap8。首会话独立phoneSF cadence59.899、max66.314ms、>100ms0，观察尾约354ms未知；不是受控改善或公网/V50。两会话初始化各2次Inbox溢出都早于configure220.589/112.729ms完成，timeout polls0、guard11、waitingIDR33/92。下一轮优先鉴别codec-ready启动准入，保持稳态4帧/2MiB/80ms及认证、取消和参考依赖，不先扩大容量。

## 2026-10-03 当前续接点：M5维护完成，M1 Mapping真机观察已完成

火锅日常线路改用M5正式15558，M1继续机主实验；两个VM/NPC/Headscale身份保留。M5已修复旧worker拒绝标准尺寸和本地scrcpy镜像冲突，实际正式NPC三通道及硬件编码启动已观察，见[m5修复记录](m5-session-startup-fix-20261003.md)。M5在线时禁止抢占或重启；不影响它的独立M1工作可继续。

新Mapping诊断1.31-alpha.4/code35已构建、lint、装机并完成上述真实媒体轮，APK8c00a166、JNI578347ca，954个全仓unittest通过。独立292-long读回拆分三类mapping拒绝，旧stats和媒体策略未改；[契约](native-mapping-details-contract-20261003.md)记录覆盖/淘汰/关停及本轮SHA。匹配helper SHA35c92b41已验证和用后卸载，手机保留alpha4，候选端口已关；尚未作为正式UDP版本发布。下一轮先审查codec-ready前的准入顺序及有界初始化策略，再进行单因素候选验收；不得把本轮零mapping拒绝解释为之前长空档已解决，仍保留动态CPU/源位置混杂和SF/callback测量边界。

## 2026-10-03 较早：发送背压修复与阶段诊断

当轮机主一加12候选为1.31-alpha.3，实际APK SHA `d87bcfd8040db3440ce7b37faa96b0b2c3c00a687c7cbef80d8d47e0f668ccbf`。新增有界configure/offer/take/consumer CPU/input API/FEC poll/实际PlaybackClock observer，实际源码与产物见[四轮基线](decoder-queue-real-video-20261003.md)及[主机发送候选复测](udp-send-backpressure-real-video-20261003.md)。四轮off/on/on/off不是严格ABBA，case1发送超时终止媒体，必须排除开关开销比较；CPU动态限制/位置/热状态也未固定，不据此宣称诊断零开销。

机主UDP worker原来用同一Python100ms timeout socket收发，baseline一次send调用约101ms后TimeoutError造成整个worker撤销。新候选仅独立owned writer wrapper非阻塞；video仍守原frame期限和reference guard，priority10ms晚包明确丢弃，不扩大buffer，不改正式/NPS。同一alpha3两轮复测均持续收到媒体、退出与新认证，手机SF cadence59.430/59.437、max gap232.056/174.035ms。四session没有实际wouldblock，retry仅由离线owned fixture验收，不称公网恢复成功；实际依赖的sender SHA7f90a12与之后只补closed-FD诊断分类的36fbebc须区分。

两段最大空档邻近四帧Inbox overflow，所在1秒段FEC增量0、reserve max2.747/1.698ms；不能继续笼统归输入槽几百ms阻塞。下一步先增加native frame-mapping拒绝的三支primitive计数、capacity时adapter/core深度和有界frame事件，再评估RX burst与已被core拒绝但仍占mapping槽的交互。旧clock_mapping_rejected按datagram合计，不是PlaybackClock、不是帧数，也不能认定106全是cap8。mapping从首次准入shard起80ms，不是首个认证包起；不先扩大容量/期限或改恢复策略。随后才按证据做同APK显式Inbox4→8单因素（保留2MiB、80ms年龄、CPU限制及其他参数），初始化与稳态分别判断，不无目的宽扫矩阵。测试helper现在有持续进度检查，仍不是App用户会话的媒体idle提示修复；后者需独立产品验收。

新SF sampler已实际用于本轮，保留连续有效poll前缀与未知尾部，不能称整个30秒完全覆盖；App callback仍回显target，SF同端关联时域未独立验证。真实光学触控、声学音画、指定公网/蜂窝/V50与朋友安全验收继续缺测。保持80ms推荐、100ms可选、lead0、wait/guard，不改两个Headscale身份或国内云端物理出口。以下各段保留历史SHA及各自日期边界。

2026-10-03较早媒体进展见[机主Tailnet真机记录](tailnet-owner-media-20261003.md)：当时安装ba84客户端已正常UI认证、Tailnet内层音视频UDP、退出与重新认证；同家Wi-Fi，机主非隔离环境。gateway退出barrier及完整记录feed已完成源码/fixture和真实收尾验证：当时两会话1551/269个media记录均完整发布，撤销各丢未发布4字节，native均退出0，无TERM/KILL，两host final齐全。全仓896项unittest通过。

## 2026-10-03 较早：解码阶段诊断与Surface候选

新独立APK1.31-alpha.2 SHA `792c6da7ac92e3c0538351940b15e632367a762552c2b7cd3dd17f60b1a8bf09` 已装机主一加12。真实Tailnet内层认证UDP完成90秒位置的A/B/B及独立95秒位置的A4续轮，见[本轮报告](surface-submit-real-video-20261003.md)和[安全数值摘要](surface-submit-real-video-20261003.json)。这不是严格ABBA：A4源门槛曾N/A后另起，实际95秒，CPU上限动态改变；B3/A4的SF末次deadline超时，只保留有效前缀。16ms没有重复改善，不推广；保持lead0、1080/4M/60/80ms、PCMoff及wait/guard。

新增fixed一秒段/直方图/异常环已真实采到，四轮input reserve最大均<15ms；input timeout都初始化polls0，不能归持续vendor槽堵。B的ready−input、Java输出持有及调度park长尾增加。稳态4帧Inbox overflow与若干长空档重叠，但另有供给下降、接收FEC恢复及发送预算丢，不能统一解释。八个native final自然退出0，无TERM/KILL；本轮没有派发触控、做声学或光学验收，也没有公网/V50验收。现有账号正常认证，helper及一次性输入已移除，候选端口已关闭，正式M1 ping正常；M1 node46/M5 node23保留，NPS未改。

下一轮优先补有界configure时段、offer/take间隔与接收FEC异常时刻、共享时钟reanchor发生范围，先独立评估并发采样成本。源码null/default正式路径不启用本轮新诊断，ARTstage-only基准不代表整套采样开销。先分清瞬时AU突发、worker未被调度与配置阶段；需要时才做显式owner Inbox4→8单因素，并保持2MiB/80ms年龄期限，不直接改默认。独立修复SF尾部不足预算仍发起ADB的假失效；新sampler不能追认本轮旧SHA的完整覆盖。之后推进PCM、音画及指定公网路径，朋友新版仍需宿主/LAN隔离验收。


确认BBB标题后，8M轮源SFcadence59.702、手机57.273，有一次754ms空档；最大wire帧322584字节在本层32M至少80.646ms，native预算丢1、参考链丢31。最新4M探索轮源59.652、手机59.094，主机预算/依赖丢0，仍有一次165.763ms空档及手机Inbox溢出/输入超时；feed与码率同时变动，不是受控8→4 AB，不能称稳定60或公网/V50验收。

下一项先补固定直方图/稳态分段关联输入等待、Inbox、target−release与独立SF空档；默认lead0提前把未来target交Surface，官方契约提示这是库存/背压候选，尚未证明根因。认证入口目前拒绝非0；若执行0/16ms ABBA，先实现仅实验opt-in及读回，同APK固定4M/1080/60/80ms、PCMoff与手机实际120Hz，不更改共享目标时钟。固定真实格式/播放位置后再单独检验峰值帧预算；PCM独立ABBA随后。以下早期“尚未Tailnet媒体/PCM未安装”段落保留历史日期与各自SHA边界，以最新记录为准。

# 下一轮有界实验与 UDP 产品化验收计划

2026-10-03用户最新决定：日常机主性能实验可继续使用原有UID501的M1 `RemoteAndroid17Compare`，通过独立实验客户端和有界LAN／已登记Tailnet会话测试。证据标为“机主测试环境，宿主隔离未验收”，不作为朋友安全部署验收。隔离候选另行保留，其ADB offline不阻挡上述媒体实验；新版朋友／公众部署仍须完成宿主及局域网隔离。认证、完整性、防重放、正式在线会话保护和国内云端物理出口规则继续保留。M1现有Headscale节点46已改名`Macbook-m1-64`，地址仍`100.65.0.2`；与M5节点23／`100.65.0.11`同时保留，见[M1节点核对](tailscale-m1-retained-20261003.md)。

新增隔离候选见 [M1 staging 与出口验收](m1-isolation-candidate-20261002.md)。专用身份、只读SDK与独立AVD冷拷贝已实际完成；UID600/profile18项无害子进程canary与UID602固定53双族DNS回复通过。独立90秒及后续60秒候选运行/recorded组收尾通过，ownPopen poll/reap修复在真实601控制器中通过；原45秒探测错误的精确原因和detached/launchd边界仍未验收。正式实例仍个人UID501，没有迁入候选。

最新闭合状态探针f7确认固定ADB transport为offline，非unauthorized：20秒readiness内71次attempt、一次connect-timeout，没有读到boot。不能据此说Android没有启动，也不能归因GPU/HVF。下一轮仅对这个候选的固定受限日志/端点作有界host/guest ADB通道诊断，不继续增加重试时长、不复制ownerkeys或扩大策略。受限启动日志曾显示Metal/ANGLE；boot/HVF/capture/编码仍需独立证据。246项源码/owned fixtures通过不代表这些实际验收。

最后一次固定closed日志读回的管理员调用120秒超时，固定root报告仍缺失；`boot-marker-final-receipt-check.json`只记录报告缺失，不宣称payload未执行或日志无错误。下一次先核对该固定receipt是否迟到，再决定是否准备fresh nonce；禁止自动重用O_EXCL目标。日志分类取得后，分开核验guest adbd虚拟通道、host regular ADB listener与专用601 server的握手状态。上游机制不支持仅因拒绝5037／随机JDWP端口或泛socketpair警告就放宽guest profile；SDK版本对应关系仍待核对，见候选报告的官方源码链接。

另准备了 [LocalOnly系统解析委托canary](../scripts/security/localonly_resolver_canary.py)：21项源码/owned inert检查通过，未注册/query/root/600执行。隔离候选后续先取得boot和该API正向/负向结果，再做guest DNS/root网络与文件负向检查、media身份/策略、GPU/HV及隔离媒体；这些是正式推广前的门槛，不阻挡已授权的机主性能实验。现有正式v1.30仍TLS/TCP。

最新用户优先事项：`huoguo` 只给火锅，`wyw` 仅机主测试。远程安卓（含 root）不能访问 M1 无关文件或成为宿主／LAN 跳板。见 [宿主隔离设计](m1-host-isolation-design-20261002.md)。现有 guest 与 gateway 共用个人 UID；两个新建 host-owned TCP canary 在 guest 中均可达。正式入口 owner 隔离与更新文件 nofollow 已通过100项范围检查并只重载 gateway，原证书／ping／清单正常；这不是 OS／网络隔离完成。管理员读回PF enabled，但尚无经验证的guest UID策略。独立合成 sandbox 子进程可拒绝文件和本机IPv4／IPv6 TCP，但没有套到VM，runner为deprecated，不可直接推广。后续先完成隔离身份、文件与受限出口候选及负向验收，不为新公网UDP开放原始ADB／shell／任意转发；不去除认证、完整性或防重放。保留既有服务，忙时跳过迁移；不能把不同App账号视为macOS边界。

双通道源码准备已完成，见[发布说明](dual-release-channels-20261002.md)与[独立交付](experimental-public-delivery-20261002.md)。正式v1.30保持，实验1.31-alpha.1采用独立包名、原签名、独立清单与手动实验更新。候选已编译/lint并安装一加12，真实UI显示版本和通道；媒体对照尚未执行。新增注册Tailnet策略要求100.65.0.2/100.65.0.3精确身份、hs.yilufa控制域、kernel utun与路由一致，失败仅撤销候选。不能把这些离线检查、安装或UI文字当成Tailnet媒体实测。

本轮曾看到正式入口有三条经NPC连接并跳过媒体测试；连接空闲后只做候选安装与UI读回，用户随后讨论NPS传输设计，尚未启动新的媒体会话。接着先完成同APK PCM A/B/B/A和Tailnet真实媒体，明确界面与内容格式；仅发布源码不能称朋友的下载/升级入口已可用。腾讯NPS原版来源核对见[审计](nps-provenance-audit-20261002.md)，保持现有服务和国内来源规则。

最新后续见[系统触控、收尾与音频候选记录](authenticated-lan-udp-followup-20261002.md)，上一轮[认证UDP记录](authenticated-lan-udp-results-20261002.md)仍保留原证据边界。已用一加12内核生成的触屏事件验证手机InputReader/Window→UDP→guest单指/双指；十指仍仅App直接派发，物理手指与光学延时未测。正常登录、新认证重连、两指未抬起退出真CANCEL通过；四角只确认3/4，不能把host ACK当成guest应用收到事件。guest本地对照4秒窗口预热后四角通过，但不能据此修复远程缺角。下一轮先加稳定窗口及guest注入结果关联，再测边缘/旋转。

worker现在可靠排空stdout/stderr并获取真实native final；四条完成会话均正确UPTIME clock、自然退出0、无TERM/KILL。正式连接忙会跳过/取消候选，2秒检查不是原子保护。12M/8M探索性视频轮源SF约28.8/29.2、手机约24.4/26.9，不能当成60FPS源或受控ABBA。最大wire帧656392/575196字节在32Mbps下至少164.1/143.8ms，仍超过80ms期限。先核实真实60帧格式/源供给，再隔离关键帧预算，保留wait/guard，不能默认提高缓冲掩盖问题。

新有界PCM队列候选已源码检查、离线资源生命周期测试及assemble/lint，默认关闭；新APK SHA为9108a6919a632dce08c7f9590520d2e0bb47a0e2d1fc52e770fad2f00cd33f32，尚未安装/真机比较。本轮手机实测仍用2dc1e0e8a60d7996195a74d486b4c15973cc90b880be9a722f71a94a4d722f0e。下一轮同一新APK切A/B/B/A，只改PCM选项，不与码率/时钟重新锚定混改。正式v1.30仍TLS/TCP，未发布UDP。

M1实际内核Tailnet路由已读回utun0，不能继续描述为只能userspace Serve；媒体未验收。后续需显式Tailnet认证范围、已登记peer身份、utun内层与物理外层分别核对，再读实际direct/relay及Clash规则/云端源地址。当前Serve仍TCP；stock DERP可能使外层走HTTPS/TCP，不能把App UDP宣称为全程UDP，也不全局禁用DERP。国内UDP中继/P2P仍需独立实现与指定公网验证。

2026-10-02较早状态：v1.30线路维护版已发布并实读M1/M5公网APK；Mac与测试手机统一yilufa，旧控制域已清理。720P两组socket wait单因素ABBA与1080P一组ABBA已经完成，分别见[720P记录](socket-wait-real-video-20261002.md)、[1080P记录](socket-wait-1080p-real-video-20261002.md)。前者B有关联收益，后者A/B接近且B无明确收益，保留默认wait/guard，不无目的重复同一矩阵。Mac native时钟契约已修并单独验证，见[clock记录](native-host-clock-contract-20261002.md)。音频分原因计数后来已随2dc候选实际采集，但仅ART microbench、没有完整诊断开/关并发AB；PCM候选的最新边界见本文开头。

接下来优先将已验证的UDP组件接入受认证App LAN入口，明确会话取消/重连、坐标/多指与音画验收；然后再测Tailnet媒体和国内UDP中继/P2P。当前手机Tailnet100.65.0.3与M1直连8ms只是连通性，现行Serve15556仍是TCP。先核对M1实际kernel/userspace路由能力，不能直接将物理接口bound sender换成Tailnet目标后宣称完成UDP路径。下面保留初始提案与完整验收清单，已执行部分以各自新记录为准。

本计划基于 [10月2日真实视频证据](evidence/overnight-20261002/README.md) 与 [发布通道审计](release-channel-audit-20261002.md)。它是待执行方案，不能作为已有实现或性能验收。本轮实验主机仍为 M1；正式入口按用户最新要求同时保留 M1 与 M5，由 App 手选，两个 NPC 必须使用独立身份，见 [双主机方案](dual-host-nps-plan-20261002.md)。M5的历史性能证据与M1新测结果分别标记。V50没有参加该局域网实验，一加12限频也不能自动证明等效V50。

## 先保持可用发布，再隔离候选

正式客户端与实验客户端保持不同包名、不同启动路径和明确版本。只发布已经检查过的客户端改动；公网地址必须先证实指向 M1。正式升级与下一轮实验分别记录，不能在用户使用期间把实验失败当成生产服务重启理由。

实验保留当前 guard 与默认节奏机制，不为追求单轮平均FPS关闭参考链保护。每个候选保存精确源码、原生二进制和客户端 APK 的 SHA-256，不改变手机 CPU 限制。用户参数在退出重开后仍保存，80ms为折中起点，100ms可选；不得依靠120ms以上缓存掩盖发送停顿。

## 实验一：只切 socket sleep，保留 guard

上轮 `--disable-socket-pacing-bundle` 同时关闭了 socket pacing 和 guard，无法隔离第二层定时等待的作用，且四轮没有可重复改善。因此下一次使用新的单一实验变量：

| 条件 | socket 定时 sleep | frame deadline / reference guard | native pacing |
|---|---|---|---|
| A | 现行逻辑 | 保留 | 保留 |
| B | 去除该层定时 sleep | 与A完全相同 | 与A完全相同 |

实现前为 sender 写针对性的输入/结果测试，确认 B 不会关闭过期截止、关键帧完整性和依赖恢复。保留有界队列、写入字节上限与默认原生 wire cap。禁止出现因删除 sleep 导致无限突发或无限排队的路径。

执行顺序 A-B-B-A，至少两组；固定视频、播放位置、分辨率、码率、原始帧预算、热状态范围和源端设置。先每轮120秒；候选稳定且确有收益时再运行一次更长会话。若连续发生源停止、身份不符或测试App崩溃，该轮无效，不得补零后混入平均值。

主要比较手机呈现间隔 p95/p99、超过80/100ms空档数量和持续时间，同时比较有效参考链丢弃、发送 deadline、assembly expiry 与队列占用。仅 average FPS 增长不够。若 B 减少调度长尾但增加关键帧断裂或丢包，不能推广；若 A/B都没有复现故障，只能判为本条件未能区分。

## 实验二：用 frameID / PTS 打通因果链

为每个帧保留下列阶段的同一身份：

1. gRPC 捕获序号、源 PTS、主机接收时间和原始帧交付时间。
2. 原生编码提交、回调、码流字节数及关键帧标志。
3. packetizer 的 frameID、首/末分片、计划与实际等待、完整写出或截止原因。
4. 手机首包、AU重组完成、解码输入/输出就绪、呈现调度和丢弃原因。
5. 音频源PTS、PCM解码、AudioTrack入队与播放头读回。

主机与手机分别使用明确命名的单调时钟；需要跨端测一程延迟时，记录时钟映射误差，不能把不同设备时间戳直接相减。SF统计目前没有源PTS或frameID，只能与手机阶段在同一时间窗口关联，不能伪造某帧的精确SF匹配。

日志采用可持续的有界聚合与必要事件采样。原始事件环形窗口淘汰后应报告覆盖不足，不从剩余事件推断全程零丢失。长轮需要可靠总计数和最终摘要，以避免上轮8192事件窗口截断造成 receive FPS 缺失。不能为增加诊断精度开启无限日志或逐帧阻塞文件写入。

## 源供给门槛与真实内容

| 门槛 | 要求 |
|---|---|
| 源内容 | 固定可重复的60FPS YouTube样本；记录视频标识和实际所选格式，不把屏幕Hz当作内容FPS |
| 启动状态 | warmup后确认播放器PLAYING，且源视频层统计持续推进；结束时再确认 |
| 有效窗口 | 请求时长与实际测量窗口分别列明；无样本、包名错误、编码设置不符或源码变化必须标注 |
| 同源对照 | 记录视频层供给、截图交付空档和热状态；源供给显著变化时，缓冲/网络收益不能作纯因果结论 |
| 源端独立窗口 | 同一内容无串流时与串流时比较；这能区分源本身呈现空档与传输后新增空档 |
| 分辨率 | 保留物理1080×1920，分别输出540/720/1080；选择高输出不能增加源细节 |
| 高帧率 | 120目标先是压力配置；只有实际内容与UI发生120次变化并独立计数，才能称120不同内容帧 |

在固定内容组件对照后，再做实际刷视频：视频播放、切换视频、拖动进度条、上下滑动和系统导航。记录每段真实动作与连续采样结果。不能把单一 testsrc2、固定文件循环或背景App时间冒充实际刷视频验收。

建议先在720P/8Mbps/80ms隔离机制，再以1080P/12Mbps验证像素负载；最后检查540P/4Mbps及80/100ms对弱手机的代价。码率测试在用户允许的范围内有界进行，不额外提高到已取消的40Mbps档位。

## UDP 产品化的入口与认证

产品媒体需要实际 UDP，而不是在 UDP 隧道内仍传原有 TCP 媒体。HTTPS可用于认证、更新和短期会话描述；认证后建立有限时效、明确目标和参数的媒体会话，失败不能默默改成TCP媒体。

会话密钥通过已验证的认证通道产生和传递，视频、音频、控制明确分离，包含反重放和会话边界。随机会话秘密不能进入Git、常规日志、命令行或长期公共下载目录。严格校验包长、分片数、帧字节数和会话身份；过期会话撤销后不能继续注入触摸。

App连接页展示当前实际路径和失败原因，不把“Tailscale”自动标成P2P。UDP端口固定只解决部署可发现性，不能替代身份验证或保证NAT打洞成功。App仍可手填地址，既有已保存密码只在明确授权的固定地址间迁移。

## 公网 UDP / P2P 验收步骤

1. **LAN产品入口**：候选正式UI触发认证、视频UDP、音频UDP和原生多指触控。检查旋转、边缘坐标、返回/主页、断开清理和重连。实验探针能运行，不等于UI产品入口已经完成。
2. **Tailnet路径**：手机实际加入相同Headscale网络，读回地址与通路；区分 direct UDP 与 relay。保持Clash TUN开启，验证隧道对国内云端走既有物理出口；不以关闭用户代理作为长期方案。
3. **公网UDP中继**：使用国内云中继，认证与媒体流分离，读回MAC目标身份及国内物理源；服务器过滤规则保持。确认所有实时视频/音频均UDP，不能因中继拥塞降级TCP媒体。
4. **P2P选择**：测试双方NAT候选探测与打洞，成功时验证实际peer与UDP路径；失败时选择已认证国内UDP中继。状态变化不能丢弃账号验证、创建无限隧道或重复注入触控。
5. **网络矩阵**：家中Wi-Fi、公网地址经Wi-Fi、手机电信蜂窝、远端移动Wi-Fi分别记录；公网同地址不能推断同样路由。固定同视频、同参数、同版本并进行正反对照。
6. **有界故障**：周期丢包、成段丢包、乱序、短暂停顿、Wi-Fi/蜂窝切换、链路恢复分别测。FEC数据包不能超出MTU或无界增加流量。关键帧请求和重发需受限，避免恢复风暴。
7. **弱设备**：在火锅V50实际采样硬解名称、CPU/温度、电量、视频到达、呈现统计和音频队列，再给该机推荐值。保留一加12限频替代测试结果的独立标签。
8. **真实交互与音画**：测多指触摸到画面的可观察延迟、听见的声画偏差和持续漂移。先完成共同时钟映射，再评估音频播放头驱动的同步；不能因为AAC能播就宣称已同步。

每层应保存候选参数、实际接受参数、传输路径、源供给、接收/呈现FPS、间隔分布、丢弃原因与音画测量方法。到达/codec callback不是屏幕呈现，也不是物理端到端延迟。

## 推广门槛与停止条件

候选需通过组件检查、真实手机LAN、公网指定路径和真实V50的分层验收。只有 source和measurement门槛通过的轮次进入比较。小样本表现好可以作为试用候选，但不能承诺稳定60/120FPS或近零延迟。

若新增机制未重复显示收益，保留现有保护并记录未能区分，不继续无目的排列参数。若出现跨会话注入、错误MAC目标、秘密日志、无限队列、明显音画漂移或公网规则绕行，停止该候选推广并恢复隔离环境。发布时保留可以回到上个服务端版本的私有备份；客户端回退通过更高版本码的修复发布完成，避免破坏用户数据。

## 2026-10-03最新：alpha5启动准入三轮已结束，发现host ENOBUFS

先读[实际记录](codec-startup-ready-real-video-20261003.md)、[数字](codec-startup-ready-real-video-20261003.json)及[候选契约](codec-startup-ready-contract-20261003.md)。同APK OFF/ON/ON后第三轮健康失败，不能称完成ABBA。ON四连接实际Gate commit成功，初始2秒带overflow/timeout0，但第二轮手机SF49.879、max1931ms，仍不稳定；默认OFF保持。第三轮host udp_video send明确errno55/ENOBUFS→撤销，手机无进度13.019s；Inbox溢出/timeout0，native1340源/output1340、22.861s后EOF自然0不是整会话无错误。不要把此次停止归因decoder槽、扩大FIFO或buffer。

当前alpha5/code36 APK f5cf8bbf、JNI578347ca、helperd537af16，原签名；982checks+build/lint通过，helper/一次性input和高端口45560/45963已清理，手机保留alpha5。首次旧App15963与新端口45963不一致的描述失败保留但排除性能样本；实际parser联合fixture已补。正式默认APK无UDP JNI/Gate/UI，正式v1.30/M5/NPS此实验未改。下一因素立即推进owned非阻塞sender仅显式opt-in的ENOBUFS有界退让/计数/取消；原视频期限、priority10ms、nonce、参考guard不变，先fixture再同APK真实视频观察。实际没遇到ENOBUFS的轮不能宣称真实拥塞恢复已验收。独立duringprepare取消helper源另外准备，未构建/装机。

## 2026-10-03最新：ENOBUFS显式候选两轮已完成，下一步mapping生命周期

读[新记录](udp-enobufs-real-video-20261003.md)及[裁剪JSON](udp-enobufs-real-video-20261003.json)。同alpha5/startupOFF，host显式ENOBUFS原deadline/priority10ms退让，两轮真实BBB前后itag299/avc1/1080@60、四会话正常退出/重连；实际ENOBUFS/EAGAIN/senderror均0，因此只验收ON正常路径，恢复分支仍fixture。1000全仓checks，17新sender fixtures。手机SF cadence59.164/59.579、完整请求窗57.758/58.258、max149.199/74.593ms；有约0.52s未知尾部，不称无卡顿60、公网/V50或光学音画验收。CPU与位置动态，不能归因候选恢复。全部pins结束匹配、gateway0/quiescent、helper/输入清理、45560/45963关闭，机主保留alpha5，正式服务与更新渠道未改。

下一因素先读[adapter/core生命周期](native-mapping-capacity-lifecycle-20261003.md)：历史8次仅两帧数据报，adapter8/core0/56～76ms；本轮再出现10/82容量拒绝，但未知八occupantID/settle原因。先定长settle(ID,reason,phone_us)及capacity occupant快照，不改变292 schema、cap8/80ms/参考链、不凭pending0全清、不直接扩容。独立duringprepare取消helper已javac源码编译，未构建安装/真机，不能当取消覆盖。新的真实campaign已结束，checkpoint在docs/evidence/udp-enobufs-retry-20261003/iteration-state.json；先接续生命周期诊断源码/owned fixture准备，再冻结新JNI/Java/APK/SHA。NPS一次正式重启和两Mac QUIC维护早已完成，不重复。

下一原生源码子任务已开始，checkpoint为docs/evidence/mapping-lifecycle-diagnostics-20261003/source-task-state.json；running时不重派、不并发改其两header和两fixture。读[240-long独立契约设计](native-mapping-lifecycle-diagnostics-design-20261003.md)，目前未接JNI/Java/未打新APK，alpha5不含新字段。现core framesDelivered在output回调之前增加，不能据其推导settle通知；用实际settle固定总计保留输出异常语义。另完成[认证入站存活与结束提示审查](udp-media-revocation-liveness-design-20261003.md)：20ms socket timeout只是轮询，ALIVE单向；既有HGPQ可供已建立会话的独立实验看门狗，静态画面不能以无新帧判失败。finished中的同步DELETE可能延迟UI但未定位历史13s全部原因；看门狗/attempt-scoped快速提示仍只是设计，未执行。保持后续单因素，别同时改变mapping、心跳和码率解释收益。

## 2026-10-03续接：native观察源码完成，用户要求双Mac公网UDP任务

Native生命周期四文件与文档已经6f978b0提交，29项owned/兼容fixture及全仓1010通过，独立review无阻断；新240字段尚未接JNI/Java/App/helper或真机。原cap8、期限、retire、参考链和默认OFF均保留，不能追认alpha5数据。

用户最新要求M1/M5既有NPC用QUIC且NPS任务用UDP。新鲜管理员API读回1466/1468均`quic,quic`，38在线；持久化clients.json的历史tcp值不能替代runtime。原TCP任务1409/1411均runtime RunStatus true。下一步在完整私有配置备份后，仅通过管理API新增同数字UDP15556/15558 companion到各Mac loopback45965；现任务保留兼容已装SSLSocket App。没有重启正式NPS或改变身份/国内过滤的必要。独立有界认证packet往返仅验任务，不当真实媒体。旧App不会因任务变UDP而自动变协议，需匹配的公网认证会话、网关和实验App适配；失败不静默TCP媒体。

## 2026-10-03最新：公网UDP任务真实部署与往返完成，App仍需迁移

[部署记录](nps-dual-udp-tasks-20261003.md)与[JSON](nps-dual-udp-tasks-20261003.json)：完整9文件私有备份后，只新增UDP task1412/M1/15556、1413/M5/15558到各Mac127.0.0.1:45965。38原NPC在线/身份/原任务/geo保持，正式PID3412973不变；runtime Mode两台quic,quic。各Mac有界HMAC64B往返10/10，M1 physical en7客户端，没有手机媒体。probe25秒自然退出且两边secret删除，目标45965关闭，新UDP任务启用等待认证媒体gateway。原TCP1409/1411保留；受信公网ping各200。42+9+17=68组件checks，初始API type遗漏无任何创建，postprobe一次未知静态比较失败后独立相邻snapshots无差异与完整readonlyguard通过，保留失败，不放宽比较规则或重启。

用户明确理解正式v1.30公网仍TCP，要求App也升级。下一源因素现在开始独立`udp_nps_profile.py`/owned fixture合同，checkpoint docs/evidence/nps-public-udp-contract-20261003/source-state.json；running时接续不重派。M1候选HTTPS控制公网49556到loopback45561（只信令）、UDP媒体15556到loopback45965/lo0；M549558规划但朋友隔离gate保留，不能据源合同宣称已部署。逐文件合同见[nps公共入口计划](nps-public-udp-app-integration-plan-20261003.md)。随后接registry本地bind/公开描述分离、GCM peer pin/cancel、App双重端口/节点scope校验与真实手机；不静默TCP媒体。NPS内部仍可靠quic.Stream，这一步不是纯Datagram最终链路或P2P产品。

## 2026-10-03最新：UDP alpha5已发布，M1 LAN机主试用运行中

[发布与试用记录](experimental-alpha5-release-20261003.md)：GitHub prerelease `experimental-v1.31-alpha.5` 已实际上传及下载hash校验，独立原签名包/code36/精确source d863302，native新240诊断不在包内。M1 LAN `192.168.9.128:45560`信令、UDP45963媒体试用，精确源snapshot、原UPTIME packetizer/encoder/JAR已核对。启动UTC06:35:18，max3600s/session120s，受信ping200；UID501非隔离机主，正式M5与正式更新不变，不称公网NPS/朋友UDP。读private `docs/evidence/alpha5-owner-trial-20261003/runtime-state.json`，试用活着时不要并开另一个gateway或抢guest/手机；自然shutdown quiescence后才复用。

`udp_nps_profile.py`独立纯合同已done，21checks与独立review通过，root新增任务/probe/profile共80checks通过。仍未接gateway/registry/worker/App，不当公网媒体验收。试用期间继续不抢资源的离线合同集成；正式NPS和其他NPC不重启。

## 2026-10-03 alpha8 follow-up

Read alpha8-onehour-public-results-20261003.json and physical30 deployment record first. Released alpha8/source73d196a is immutable. Both public App endpoints use actual authenticated UDP. Public session cap3600/process max0; LAN/Tailnet120. Preserve actual guest30Hz and owner phone CPU/global refresh. M1 135-second steady SFcadence26.423/max265.225ms, versus M5 short29.999/max49.735, is not a controlled host AB comparison. M1 complete host feed3900/141.465seconds (~27.569 records/sec), packetizer output3897, budgetdrops0/deadline1/dependent2; these unequal host/phone windows do not locate every gap or prove unique source FPS. Next verify actual BBB format/play state/position, collect source SF and timestamps with the existing bounded diagnostics, then identify whether long phone gaps follow source supply or receive bursts before choosing one parameter. No high-refresh/high-bitrate wide matrix, no TCP-media optimization. Real one-hour expiry UI, MTK/V50, cellular and friend host/LAN isolation remain separately pending.
