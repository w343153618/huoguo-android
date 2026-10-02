# M1 隔离候选：出口、文件策略与管理员 staging

日期：2026-10-02。本轮在正式网关会话 owner 加固之后，准备独立宿主身份与受限出口。**独立服务身份、只读 SDK／代码／策略已实际安装，候选 AVD 已完成冷拷贝；候选硬件启动与宿主隔离尚未验收，正式实例没有迁入隔离环境。正式 v1.30 仍是原 TLS/TCP 媒体路径。**

App 账号 `huoguo` 供火锅使用，`wyw` 只供机主测试。拟建的三个 macOS 服务身份是运行权限边界，没有新增 App 登录凭据：

- `_huoguo_vm`：只持有自己的 AVD、缓存和日志，读取只读 SDK。
- `_huoguo_media`：固定实例的画面、声音与原生触控管理，后续需单独验证文件与接口权限。
- `_huoguo_egress`：独立 Web／DNS 出口，没有个人 Home、管理员组或交互登录权限。

管理员持有的候选根目录为 `/private/var/lib/huoguo-android-isolation`。已实际创建 `_huoguo_vm` UID/GID600、`_huoguo_media`601、`_huoguo_egress`602，服务 Home0700、禁用交互登录，未加入管理员组。不迁移个人 Home、SSH、浏览器或 Keychain；不覆盖旧 runtime、AVD、NPC 身份或正式公开入口。

## 本轮代码与可重跑验证

| 组件 | 源码 | 范围检查 |
| --- | --- | --- |
| Web 出口 | [restricted_egress.py](../restricted_egress.py) | 33 项；自有双族回环 TCP／SOCKS fixtures、解析前名称拒绝，无公网连接 |
| DNS 出口 | [restricted_dns.py](../restricted_dns.py) | 25 项；自有双族回环 UDP fixtures，固定上游在测试内替换，无真实公共 DNS 请求 |
| 实验性 Seatbelt 生成器 | [emulator_sandbox_profile.py](../scripts/security/emulator_sandbox_profile.py) | 16 项；包含 Darwin 无害子进程文件／socket canary，无 emulator |
| 管理员 staging | [isolation_admin.py](../scripts/security/isolation_admin.py) | 26 项；所有系统账号、ownership、cp／lsof 操作 mock，只在新临时目录写 fixture |
| 固定安装包准备 | [prepare_isolation_candidate.py](../scripts/security/prepare_isolation_candidate.py) | 22 项；私有快照、SHA／文件边界、private vartmp／profile 双 pin、失败收尾，不执行系统安装 |
| 固定身份权限 canary | [isolated_uid_canary.py](../scripts/security/isolated_uid_canary.py) | 29 项；离线 fixture／mock，Darwin legacy kernel groups、18 项必需 case 和 inode 清理契约 |
| 候选监督器 | [isolation_candidate_supervisor.py](../scripts/security/isolation_candidate_supervisor.py) | 50 项；离线 fixture／mock、实际owned进程组与双族DNS回复fixture、阶段receipt与正式会话保护 |

| 固定 controller 骨架 | [candidate_capture_probe.py](../scripts/security/candidate_capture_probe.py) | 24项，Python3.9／3.14分别通过；闭集阶段/状态、readiness及operation deadline，真实ADB读回offline，非boot/capture验收 |

当前阶段最新结果：**独立UID600文件/socket canary及UID602固定DNS53双族回复已实际通过；候选600 guest与601 ADB固定回环端点关联通过、60秒运行及recorded组收尾通过。安卓控制通道仍offline，未取得boot属性、截图/HVF或rootguest网络负向验收；正式实例仍个人UID501，未迁移。** 新源码仅推实验分支，不能称新公网UDP正式版本或朋友的宿主隔离完成。

| 新增组件 | 源码 | 当前范围 |
| --- | --- | --- |
| LocalOnly系统解析委托canary | [localonly_resolver_canary.py](../scripts/security/localonly_resolver_canary.py) | 21项离线/owned inert子进程；未DNS注册/query/root/600执行；默认describe |

九套源码/owned fixtures最终 **246项全通过（10.909秒；外层11.046秒）**，全程SHA不变，见`source-checks-phase-final-20261003.json`。capture source为`f7af4149e54b42d8f8b5b56153b5aeb2c9518cfcca59585581f7cf8933eba612`，installed仍`4a749...`。LocalOnly源码为`f4294345aad419d7f37ef0ac8f105cfafa073bf8d6686dd59112794f27990749`，需另一个root-owned0700私有副本/source及guest profile full pin才能run；精确fresh TXT/IN且两positivecontrols先成功，未知格式/超时为inconclusive。只准备源码，不代表guest/LAN/OS daemon隔离通过。

最新仅加探针闭集诊断后八组件 **219项全部通过（10.492秒；外层10.626秒）**，整轮源码SHA前后相同；旧215项证据保留。capture source新SHA为`5825fbb7d32afbd867986f784589dc89b44e2d7c976a48dc4ff27517e1a82bbe`，实际安装仍`4a749...`。诊断字段只允许8个固定阶段、固定category与1..4095数值errno，时间／命令／权限不变，不输出异常原文。新证据为`source-checks-diagnostic-20261003.json`。

最初五组件的111项检查通过（4.265秒）；后续172项记录仍保留。最新八组件 **215项全部通过（10.442秒；外层10.565秒）**，整轮源码SHA前后相同。196项中间轮曾因owned进程组fixture收尾探测报错失败，未当作通过；修复后才取得215项新结果。实际系统 Python 3.9.6 另已完成新的 private-temp prepare-only、loader／helper 编译及19个顶层模块、2个helper和profile精确SHA验证，未执行该新staging包。profile fallback本机实际未触发，只在fixture中验收。证据目录为 `evidence/m1-host-isolation-20261002/`，原始敏感日志、凭据及 AVD 不进入 Git。

```sh
python3 -m unittest \
  tests.test_restricted_egress tests.test_restricted_dns \
  tests.test_emulator_sandbox_profile tests.test_isolation_admin \
  tests.test_prepare_isolation_candidate tests.test_isolated_uid_canary \
  tests.test_isolation_candidate_supervisor tests.test_candidate_capture_probe -q
```

## Web 与 DNS 出口的具体边界

Web 出口只绑定 `127.0.0.1` 和 `::1` 的同一个端口。HTTPS 使用 CONNECT，目标只允许经过全量地址检查的公网单播 80／443；明文 HTTP 只允许无正文 GET／HEAD 的 absolute-form 请求。源码另已在启动系统resolver之前拒绝与DNS guard一致的11类私有名称后缀、单标签和另类数字形式；mock及owned CONNECT／GET／HEAD socket证明不进入resolver。公开名称仍交给OS resolver，不能称Web DNS已经固定物理出口或无OS daemon LAN行为；实际已安装guard仍为旧SHA，该新增修补尚未部署。实际拨号使用已经检查的数字地址；上游 Clash SOCKS 也只收到数字 ATYP 1／4，不再有一次未经检查的目标解析。内网、回环、CGNAT／Tailnet、fake IP、组播、已知 IPv6 转换地址和登记的宿主地址均被拒绝。默认并发 8、头部 8 KiB、每向队列 64 KiB、连接总时长 300 秒；不记录域名、载荷或请求凭据。

DNS 出口需要管理员预绑定 `127.0.0.1:53` 和 `[::1]:53`，随后完全降至服务 UID/GID 再接收查询。生产入口拒绝缺少任一地址族、全接口监听、错 family／端口或 IPv6 V6ONLY 未启用。固定向 `223.5.5.5:53` 发出有界请求，并用 Darwin `IP_BOUND_IF` 绑定安装器指定的 `enN` 物理接口。客户端不能指定上游、端口、TCP 回退或任意转发任务。

DNS 只接受受限的单问题查询，响应检查 source、随机替换 ID、问题与记录边界，拒绝私网地址及 SVCB／HTTPS 内网 hints。默认 4 worker、8 pending、64 请求／秒，最多两次各 1 秒的尝试。最大包 1232 bytes；DNSSEC、复杂 EDNS、超大／截断回复等兼容性仍待真实安卓检查。

Web 入口不是万能流量过滤：CONNECT 内部数据不透明，公网 80／443 不代表只能浏览视频。宿主的公网地址、可能回流到宿主的公网入口、特殊 NAT／路由必须另外登记或外层阻断。此模块也不能代替阻断安卓直接绕过出口。

## 两个实验中发现的实际绕过点

1. SDK 37.1.11 的 HTTP 代理初始化失败后，emulator 可能继续启动并忽略代理。因此不能只依赖 `-http-proxy`。拟部署沙箱禁止直接外联，仅例外到独立 Web 与 DNS 回环端口；出口失败时不得放行直连。
2. 本机 SBPL 的 `localhost:PORT` 同时包含 IPv4 与 IPv6。无害子进程确实可连接自有的 `::1` 同端口 canary，说明只绑定 IPv4 会留下另一地址族入口。两个出口现均要求双族独占，任一绑定失败就拒绝启动。IPv4-mapped loopback 命中对应 IPv4 listener；本机未配置的 `127.0.0.2` canary 无法 bind（Errno 49），这不能证明将来添加的所有 loopback 别名都被拒绝。真实候选还要验证监听 UID、存活、接口别名和规则生命周期。

## 文件与管理员 staging

Seatbelt 候选只读指定 SDK／代码与明确系统依赖，只写自己的 AVD／缓存。明确排除 `/Users`、其他卷、网络挂载及 Data 卷别名，不整体放行 `/System`、Homebrew 或个人 Home。出站只例外固定回环 Web TCP 和 DNS UDP，管理监听逐项列出 console／ADB／gRPC；不例外共享 ADB 5037。宿主麦克风输入和真实摄像头在克隆配置中关闭。

该 runner 是本机已 deprecated 的 `sandbox-exec`，采用 `allow default` 后明确约束文件与 socket。其余 Mach／IOKit／process 能力、GPU／Hypervisor 兼容与子进程边界尚未验收，不能称为已完成的正式 App Sandbox 或零逃逸保障。

管理员 helper 首次 staging 遇到已有未知用户／组或非空候选目录会拒绝收编／覆盖。创建后读回 UID/GID、Home、非交互 shell 与管理员组资格；阶段失败保留 root-owned journal，不自动重试覆盖部分状态。特权文件读取／创建逐级用 directory FD + `O_NOFOLLOW`，普通文件检查大小和单硬链接；新文件 `O_EXCL`，避免末级或父目录符号链接越界。SDK 只接受树内相对链接；可写 AVD 拒绝链接、FIFO 与非普通对象。

准备工具只生成新的0700 bundle，快照文件0600。CLI要求`--output`与`--system-temp`二选一，后者固定到root-owned sticky的`/private/var/tmp`下新UUID目录；不执行管理员操作。外层 `/usr/bin/python3 -I -S` 先核对loader SHA，再核对全部固定叶名、manifest、helper和profile。将已经验证的bytes放进root私有临时目录后才provision，不从仍可修改的checkout再次导入特权代码。成功记profile SHA，失败记journal；不自动启动服务。Documents下的rootloader1／2仅为旧证据，不能当作当前可执行安装路径。实际首次成功的private-temp包与随后prepare-only新包分别保留receipt；已有候选不应再次运行首次provision来覆盖它。

冷拷贝前要求候选 UID 没有进程，原实例 launcher 在复制全程受控停用。helper 在复制前后检查源 AVD 无打开文件及文件元数据未变化；发现变化不接受克隆。删除的仅是候选派生 launch／hardware／lock 元数据，原 AVD 保留。未知绝对路径或 `..` 配置路径会被拒绝，初始候选保持 6 核、16 GiB、1080×1920。检查本身不构成原子源锁，不能省去 caller 对 launcher 与正式会话的控制。

## 当前现场与后续验收

23:15:34+0800，私有系统临时目录中的固定 SHA 安装包完成原生管理员认证与 staging。此前 Documents 下同内容安装包被拒绝读取；该差异不能独立归因为某项 TCC／Full Disk Access 设置。管理员 proof 的 UID 为0。实际安装结果见 `evidence/m1-host-isolation-20261002/private-tmp-stage-result.json`。

在两次正式15556连接数均为0后，机主 UID 仅停用原 gateway／emulator launcher，检查原 AVD 无打开文件，再生成独立冷拷贝缓存；复制前后元数据一致。随后恢复两个原 launcher，原 guest 的 `sys.boot_completed=1`。23:30:58+0800，root helper将该一致性缓存复制到独立 `RemoteAndroid17Isolated` AVD，仍为6核、16GiB、1080×1920，guest麦克风／摄像头关闭。原 AVD、账号和正式公开入口保留；PF、Clash、NPS／NPC及云端国内来源过滤未修改。见 `cold-source-cache.json` 与 `candidate-avd-clone.json`。这次启动恢复不构成正式实例隔离或硬件性能验收。

第一次实际UID/profile canary中，root-owned journal／profile与UID600身份读回正确，18项测试均未通过：子进程在pre-exec身份切换检查阶段失败，文件／网络正向对照未建立。全部fresh fixtures／监听器已清理；不能把未运行的负向测试视为阻断成功。见 `isolated-uid-canary.json`。后续用Darwin实际kernel组接口修复，未降级身份或放宽guest profile；完成结果见下一段。

后续源码审查确认Darwin的Python `os.getgroups()`采用目录服务默认组查询，不能核验`setgroups`修改后的实际进程组。canary现于fork前加载固定libSystem的legacy `getgroups`，保留清空附加组、real/effective UID/GID全600、实际kernel组仅600和admin成员检查；查询错误／超限一律失败。修正后第一次原生管理员调用120秒超时，当时未取得报告，原记录保留在`isolated-uid-canary-kernel-groups.json`。10月3日00:13后续固定管理员读回中，重新执行的18项必需权限canary全部通过，全部fresh fixtures及监听器清理完成，见`canary-log-readback.json`。进程真实／有效UID/GID均600、kernel组仅600；外部world-readable文件和可写目录在无sandbox对照中可读／写／创建，套用guest profile后被拒绝；自有Home与只读SDK正向可用，其他IPv4／IPv6／mapped回环及自机LAN canary被拒绝。SDK写入在无sandbox时已被DAC拒绝，不能把该项归因单独算作SBPL效果。这是实际服务身份下的无害子进程验收，尚非Android root、完整宿主或网络隔离验收。

监督器固定运行独立AVD／5566／5567／8566，要求`-accel on -gpu host -grpc-use-token`，没有软件或个人UID回退。root只绑定两个DNS FD；Web／DNS完全降至602后解析请求，guest降至600，未审核FD关闭。guard的file whitelist基础Python导入与惰性SBPL编译已实测通过，真实UID／FD继承／HVF／GPU仍待验收。Homebrew runtime仍可由可信机主501更新，不能称为不可变生产runtime。Darwin未实施RLIMIT_AS；只固定NOFILE／NPROC／CORE，日志新建在各role的0700 logs目录，文件0600，不打印内容。

监督器在启动前、每个spawn前和运行／就绪轮询每2秒检查正式15556连接；忙或检查异常时停止仅本轮候选PGIDs。保护不是原子锁，仍可能短暂重叠。固定私有组合包已准备：正式入口空闲→18项权限canary全部通过→校验完整启动计划→最多60秒候选运行与收尾；只新建受信helper，不切正式服务、不修改PF或凭据。该组合包的prepare-only receipt为`bounded-validation-prepared.json`。外层调用180秒超时的`bounded-validation-result.json`只说明调用方未取得最终stdout，不能推断没有执行：后续root-owned helper／日志与完整canary读回证明它实际安装了冻结helper并启动了guest／Web／DNS。旧监督器SHA为`a298e0dce87dd7225a43bef6c09577118d49d7d68354dfe4daaddab688abd362`。旧监督器未持久记录完整运行／收尾，不能称60秒全程或无残留验收。新源码增加root-owned nonce JSONL receipt，每阶段fsync；不记录argv、token、payload或任意异常文本，prepare仍不写。

实际候选日志（`candidate-startup-diagnostics.json`）显示Metal VkEmulation／OpenGL及NexusLauncher、SystemUI ANGLE VkInstance确实初始化，同时crashpad在默认`/tmp/android-_huoguo_vm`写入被拒绝。新的启动候选关闭可选crash报告、metrics与boot动画，不整体放行`/tmp`。`ANDROID_EMU_MEDIA_DECODER_VTB=1`显式保留H.264 VideoToolbox路径；该SDK原本默认已开启，不能把这个环境变量说成新测得的性能修复。没有boot属性、实际HVF或硬编属性读回，GPU启动日志不能替代这些验收。

另发现已安装guard profile的兼容性缺陷：只允许目标UDP53会阻止DNS从已绑定53的listener向回环client临时端口回复。自有无root、非53 socket fixture实测baseline回复`EPERM`；最小`require-all(local udp localhost:53, remote udp localhost:*)`例外（fixture只替换local53为自有端口）在IPv4／IPv6均允许listener回复，同时新建未绑定socket仍被拒绝。这个例外仅限定源端点与回环目标，不表达query身份；DNS源码仍校验client并仅回复其peer。10月3日00:45后已在独立候选更新监督器与egress策略，真实固定53双族验证均成功：DNS uid/ruid/gid/rgid全602，回复peer与本轮listener精确一致、完整记录校验通过并有公网A Answer，IPv4／IPv6分别15.264／17.463ms。见`guard-dns-live-readback.json`；没有读kernel附加组，没有guest Android DNS／Web DNS验收，guest profile保持不变。

固定候选45秒运行已记录`experiment_completed`，随后收尾记录`stop_failed/candidate_group_probe_failed`。独立后续killpg0对本轮三个PGID均ESRCH，ps未显示这些进程组；这只能证明读回时已不在，不能消除原收尾异常。源码owned-process-group组合检查也出现同一探测异常。已确认fixture成功收尾后重复probe／异常跳过stdout.close的问题并修复；一次fresh数值对照只看到killpg与ps约10ms非原子窗口，未复现EPERM。新监督器只在有界等待中把EPERM当pending，唯一ESRCH代表absence；到期仍拒绝、signal前身份与permission仍严格。原root探测异常具体原因未证实，最新源码保留数值errno／PGID／固定stage供下一次诊断，不能说已找到根因。新receipt已成功提供实际阶段证据，见`guard-trial-events-readback.jsonl`。本轮只更新独立helper（`b4df3add8da6072bc3a389579bbeedfc2e0ddaeabe7e2d21478d1c5c15eee630`）和egress profile（`357f5cd637028ef33d525a5839968e334482a77069b7974becc93852bf3c2a14`），有旧文件备份；两个文件分别原子更新，并非多文件事务。旧19个staged模块与journal未替换，Web解析前新增拒绝目前只在源码。

后续独立90秒候选实际完成运行，并记录`experiment_completed/stop_returned`；新监督器已在整个recorded PGID层核验消失，root observer自己的601组也清理完成。见`boot-trial-events-readback.jsonl`与`boot-trial-actual-readback.json`。旧45秒探测故障本轮未复现，不能由新轮通过倒推旧故障根因；任意detached／launchd进程仍未验收。实际安装supervisor为`15e81b5d748bc2a7ac4938dc835508cfcdbdb28b329a7b16d04a9c1d8a9df114`，capture helper为`4a7494524a4bf8155307f914b059cc8393a0bb9fd9deeaddd0f61790bee9ee3b`，原19个模块与两profile字节不变。

这轮boot observer在新15037 ADB server监听前失败，未连接guest。独立UID501、自有Home／端口SDK fixture复现精确错误：此Mac SDK不支持`-L tcp:127.0.0.1:PORT`的specified hostname，不能归因guest授权、601身份或GPU。改用`-L tcp:PORT`、不加`-a`后的fixture实际仅监听127.0.0.1，收尾成功，见`adb-socket-spec-fixture.json`；后续真实601监听验收通过，但guest boot探针仍未通过，见下一段。

最新60秒单因素socket参数重试没有重装代码或修改profile：专用SDK ADB真实UID601/PID10908仅loopback15037、受管VM UID600/PID10834的5567由root attester核验；helper已到main并生成结构化JSON，但退出1，generic `system_io_or_dependency_unavailable`仍不能区分attestation FD、ps或socket阶段，因此guest boot/HVF/capture仍不接受。候选supervisor全程receipt再次为`experiment_completed/stop_returned`。observer的probe组10932收尾通过，ADB组10908先报`Refused`，root fallback及独立killpg0读回均确认所有本轮recorded组ESRCH；不得把fallback消失混作observer首次成功。只读审查发现observer自有Popen等待顺序缺少group等待内poll/reap，源级缺口确认，但旧row丢了fixed reason／errno，zombie原因尚未实证。见`boot-retry-actual-readback.json`、`boot-retry-observer-actual-readback.json`、`boot-retry-events-readback.jsonl`。独立health确认原guest boot=1、5556/5557/8556与15556/8089保留、候选管理TCP端口无监听、云端146.56.249.175路由仍en7/192.168.9.1；route不是出口国家抓包验收。随后fresh-owned数值fixture实际复现了源级机制：TERM后未reap的同UID/PGID zombie令killpg0为EPERM1，仅Popen.poll回收后变ESRCH3；这是机制对照，不能回填原10908缺失的errno；有序单例保存在`observer-owned-zombie-probe-7a0f368ed8b58f94.json`，修复helper同型fixture保存在`observer-owned-reap-fixture-ecba1eb0ee66f434.json`（58.246ms只为owned子进程收尾，不是媒体延时）。下一轮只补固定阶段诊断及observer自有child回收，不放宽guest profile／identity或改正式入口。

随后独立60秒ephemeral5825诊断得到具体失点：`failure_stage=adb_connect`、`failure_category=timeout`、`errno=null`；attestation与PID precheck已经通过，不能再把generic错误说成Python导入或FD/权限失败。仅凭connect timeout仍不能判断guest adbd是否已就绪、guest已授权或Android是否boot。observer仅补ownPopen poll/reap后，两条601 cleanup均首次`group_gone=true`，supervisor全程`experiment_completed/stop_returned`且独立readback各recorded PGID ESRCH3。安装的19modules、15e81监督器、4a749root attester、两profile均保持字节不变；新的5825 boot脚本只经验证bytes临时在601运行。见`boot-diagnostic-actual-readback.json`与`boot-diagnostic-events-readback.jsonl`。下一轮仅在原20秒readiness预算中重试指定connect-timeout，保留其它权限/端点失败即时拒绝，命令2秒上限、guest策略与正式入口不变。

新e127把connect-timeout重试限制在原20秒readiness内，并将socket open/send/status/length/payload统一到每operation最多2秒的共同deadline；历史5825各subphase曾分别续2秒，不能混作同预算。e127真实71次attempt/一次timeout后仍notready。仅补闭合transport-state的f7下一轮确认`offline`，而非`unauthorized`；readiness 20004.667ms、71次attempt/一次timeout、serialnull/bootfalse。计时不包括之前attestation/PID与之后boundedPID核验；这也不证明Android没有boot，只证明固定ADB通道在窗口内不能读命令。两次60秒监督器与601 ownPopen收尾均通过，installed19modules/4a/15/357/a5字节不变，见`boot-readiness-actual-readback.json`、`boot-transport-state-actual-readback.json`及各JSONL。下一步改为有界host/guest ADB通道诊断，不复制个人keys、不开放共享5037/控制器15037给guest，不扩大策略或延长窗口来替代根因。

阶段结束独立readback确认f7全部recorded Web/DNS/guest/media PGID均ESRCH3、候选TCP管理端口及UDP53无监听；原UID501 guest `boot_completed=1`，5556/5557/8556、15556与8089仍在原PID监听，安装pins精确一致。云端路由仍物理en7/192.168.9.1，未对country/source或手机媒体作验收。见`phase-final-health.json`与`phase-final-guard-cleanup.json`。下一诊断只读取固定closed候选日志并输出闭合数字/flags；不把日志初始化标记当boot或HVF通过。

本轮候选源码只推送 `codex/experimental-udp`；正式 `main` 与v1.30保持原提交。源码推送不构成新APK发布或隔离验收。

独立controller源码默认只描述计划。boot仅使用已经运行的专用15037 ADB smart socket，root先核验601 server PID／600 managed VM PID及仅loopback端点，用root-owned0600已unlink普通文件的O_RDONLY继承FD授予短期描述；不用shared5037、个人Home或ADB凭据。固定读取boot／尺寸／GLES，并前后核验PID。token broker只从固定600/PID/AVD/8566来源送至private匿名pipe，不写stdout或报告；真正gRPC截图尚未实现。

staging和冷拷贝完成之后，独立候选启动与推广仍需以下真实验收：

1. 自有文件 canary 与 guest root 的回环／自机 LAN／IPv6 canary；改变 guest proxy、DNS 或 iptables 不能绕过。
2. 两族出口监听身份与死亡处理；禁止原始 ADB／SSH／Clash 访问；账号间会话 owner 边界保持。
3. 原签名 SDK 的 Hypervisor／Metal 和苹果编码实际启用，不能静默退回个人 UID 或软件路径。
4. 保留一加 12 的降频限制，用真实视频检验接收与独立呈现 FPS、长尾、触控与音画同步，推荐缓冲不超过80ms。
5. NPS／新媒体中继走既有国内物理出口，guest YouTube 的 Clash 出口分别验证；再验证指定公网和 Tailnet direct／relay。
6. 重启、网卡变化、TUN 共存后仍 fail closed。有人使用正式实例时跳过打断测试。

上述没有通过前，不能公布“宿主／LAN 已隔离”、新公网 UDP 产品完成或真实手机流畅度提升。链路认证、完整性与防重放继续保留；它们与 UDP 媒体并不冲突。
