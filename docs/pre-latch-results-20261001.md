# M1 1080P 真实视频压力测试与 Latch 前等待

日期：2026-10-01。用户将主要实验目标从较低分辨率改为 1080P。本轮已持久化同一 M1 AVD 的物理 1080×1920 / 480 dpi，保留 6 核、16 GiB、host GPU 与磁盘数据。M5、NPS、Clash 配置未修改。正式手机 App 仍为此前版本，本轮没有发布升级包。

## 配置与实际读回

[配置变更证据](evidence/pre-latch-20261001/m1-1080-config-change.json)记录原/new SHA256、原配置私有备份、冷启动完成、wm reset 后物理尺寸和密度，以及 6 个在线 guest CPU。本次不是仅做 `wm size` 逻辑覆盖。480 dpi 与原 720P / 320 dpi 均对应名义 360×640 dp，减小布局变化。原 AVD `hw.lcd.vsync=120` 保留，guest min/peak 60 设置、Skia Vulkan、供电常亮保留。

M1 gateway 的 `DIRECT_PHYSICAL_DISPLAY=1080x1920`，`DIRECT_MAX_SIZE=2400`（实际编码上限还受物理长边1920约束）。独立 UDP 实验明确使用 max-size1920，VT ready 和手机 source_geometry 两侧均读回1080×1920；540P 对照编码540×960，安卓物理仍1080×1920。

1080P 像素量是720P的2.25倍、标准16:9 540P的4倍，适合作为采集、转换和编码压力测试。较高分辨率流畅不能自动证明所有较低分辨率设置都流畅：源播放器状态、显示节奏、队列及网络抖动也需分别验收。

## 真实手机的受控实验

全部为固定公开视频 BBB 的 Morphe YouTube App，每轮 force-stop 后重开同一URL、请求seek60s、预热8秒、媒体会话前后state3/speed1；保留App数据。URL/会话状态不替代逐帧媒体PTS。轻量source-only trace实际H.264输出PTS以16666/16667µs递增，支持60FPS媒体时间线，不等于实时60FPS或所有phone轮都逐帧验证了媒体内容。

路径为 M1有线en7 192.168.9.128 → 家庭局域网 → 一加15 Wi-Fi 192.168.9.6；AES256-GCM UDP、10+2 FEC、Apple VideoToolbox硬件H.264、手机硬件c2.qti.avc.decoder.low_latency。60FPS cap，VBR目标，buffer60ms，raw queue fifo，arrival clock、async input、feedback、bounded socket pacing，音频与原生多指协议启用。wire ceiling32Mbps仅为瞬时发送预算，不是产品新增24/40M档位。无合成动画或截图估计FPS。

[逐轮数值对照](evidence/pre-latch-20261001/controlled-phone-comparison.json)：

| 编码尺寸/目标码率 | 轮次 | 接收逻辑媒体帧率* | 手机SF显示FPS `[5,30)` | p99显示间隔 | 最大显示间隔 | >100ms | inbox overflow |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1080×1920 / 8Mbps | 1 | 59.057 | 58.20 | 33.27ms | 66.39ms | 0 | 0 |
| 1080×1920 / 8Mbps | 2 | 59.151 | 58.16 | 33.28ms | 66.54ms | 0 | 0 |
| 1080×1920 / 12Mbps | 1 | 59.361 | 58.36 | 38.00ms | 66.38ms | 0 | 1 |
| 540×960 / 4Mbps | 1 | 59.177 | 57.88 | 33.28ms | 66.39ms | 0 | 1 |

*接收逻辑媒体帧数除以 `receive_end - first_server_packet`，包含起停阶段，与显示的25秒稳态窗口不同，不能直接相减求掉帧。显示采用独立SF actual-present时间戳，不用codec callback当显示FPS；也不是光学屏幕测量或像素内容去重统计。每轮35秒，单个短样本不证明长期稳定，顺序的码率/尺寸对照也不是因果收益证明。

四轮VT using_hardware=true、手机hardware=true、input timeout0，FEC expired/reference-lost0。12M和540P轮各有一次inbox overflow，不称完全无丢弃。12M没有表现出明确流畅度优势，因此本轮1080P实验基准选8M/60cap/60ms；540P正式App默认值未因压力测试而更改。540P串流并没有获得明显更多显示帧，说明仅减小编码像素不能保证60FPS稳态。

音频PCM有实际写入。8M两轮audio worker late drop0，PCM late drop分别1和0；12M worker late drop8、PCM late drop0。没有声学A/V偏差测量。原生触控保持现有协议，启动前实际wm尺寸读回1080×1920、无override；该轮未做光学触控到显示延迟或真人多指体验验收。60ms是配置的播放缓冲，不能叫实测端到端延时。

## 手机刷新率与未受控轮


一加15实际显示模式90Hz、SF周期11.111ms；60FPS画面间隔主要交替11/22ms。虽然supported modes含120Hz，Window请求120Hz的起止读回仍为90Hz。临时root设置min/peak=120后也仍90Hz；finally恢复原null/165.0并读回。不能宣称做到了120Hz手机对照。

最初未逐轮重开源的三轮全部保留：[8M初轮](evidence/pre-latch-20261001/udp-1080-8m-01-summary.json)58.36FPS/max55.47ms，[120Hz请求轮](evidence/pre-latch-20261001/udp-1080-8m-phone120-02-summary.json)实际90Hz、57.84FPS/max121.70ms；临时floor轮实际90Hz、23.64FPS/max166.37ms，同时源SF23.33FPS。没有核验该异常轮的媒体FPS，源没有逐轮固定重开，不能据此把下降归因于phone刷新floor。见[对照限制](evidence/pre-latch-20261001/refresh-comparison-limit.json)。受控复测才是上表的主要依据。

## Source-only 追踪与观测负担

| 轮次 | 采集 | raw大小 | source SF `[5,30)` | 同窗最大gap | 同窗>100ms |
| --- | --- | ---: | ---: | ---: | ---: |
| 1080-01 | gfx/video/sched + graphics frame + host sampler | 44.91MB | 54.88FPS | 293.147ms | 5 |
| 1080-light-02 | video/sched + graphics frame + corrected host sampler，源重开 | 12.69MB | 57.04FPS | 82.806ms | 0 |

两轮都无gRPC抓屏订阅、无串流编码/手机传输。没有positive loss/overrun，近cap警示为false；完整性还需clock snapshot跨度、行上限和删失边缘共同核对。两个顺序样本同时存在分类和播放器重启差异，不能把差值全部归为观测工具，也不能把重型gfx的293ms直接外推到普通用户体验。轻量轮整个graphics目标Present-event最大gap95.579ms位于不同窗口，不能混为固定25秒窗82.806ms。

重型gfx最大293.147ms事件空档中，右端同一graphics identity track0/buffer257/frame6079：SF receipt Queue→batch Latch202.387ms，Latch→Present事件17.347ms。完整guest SF主线程在Q→Latch窗Running1.333ms、Runnable47.916ms、Sleeping153.139ms；含一段46.528ms Runnable。该窗H.264 onWorkDone观测0次，两侧callback start相隔289.866ms。这是时间窗共现，不是codec PTS→buffer的身份桥；也不证明解码硬件停了。见[同身份graphics](evidence/pre-latch-20261001/source-1080-01/graphics-analysis.json)、[完整scheduler](evidence/pre-latch-20261001/source-1080-01/longest-gap-scheduler.json)。

同身份Queue→Latch中位数在重型/轻量分别42.350/41.408ms；典型Latch→Present仅1.886/1.683ms。这个几十毫秒区间可含目标显示时刻、readiness、backpressure、guest/host调度；不能直接叫GPU处理耗时。Acquire event两轮均0，不能补成buffer立即ready。

## Readiness 与宿主vCPU的新证据

真实SF ELF哈希和固定marker字串已只读确认，执行分支仍需实际trace验证。gfx轮229168个SF slices中采到1580次全局desired-time-not-current、46次全局!isVsyncValid；desired−expected p50/p95约4.333ms，max21ms。这些marker不含transaction/layer/buffer身份，全部identity_verified=false，不按最近时间关联视频帧；marker数量不是唯一帧数，marker scope不是等待总时长。named-layer fence/backpressure0不证明无readiness阻塞。见[数值readiness](evidence/pre-latch-20261001/source-1080-01/readiness-analysis.json)。

机制参考：[MediaCodec定时release API](https://developer.android.com/reference/android/media/MediaCodec#releaseOutputBuffer(int,%20long))允许未来呈现时间；[AOSP BLAST](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/libs/gui/BLASTBufferQueue.cpp)在显式timestamp路径设置desired time；[SF readiness](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/SurfaceFlinger.cpp)可暂缓未到目标时间的事务。这里guest build CE2A.260420.019/15611780没有精确源码映射，main机制参考不等于该ELF逐字核验，也没有核验Morphe具体release策略。

重启后六个QEMU vCPU线程的角色来自两秒私有callgraph中同时出现cpu_thread_fn/hvf_vcpu_exec，unique ID64用libproc flavor15读取。采样间隔25ms，逐线程与整组都保留PID/starttime和时间边界，数字报告不保存原始栈。

第一轮发现Python time.monotonic_ns与本Mac POSIX CLOCK_MONOTONIC起点不同；旧host数据保留但禁止直接关联fence时间线。新sampler默认统一clock_gettime_ns(CLOCK_MONOTONIC)，host_clock_code1。轻量轮通过fence常偏移区间和graphics BOOTTIME/MONOTONIC snapshot映射，仍保留模型假设和不确定性。见[宿主窗口对照](evidence/pre-latch-20261001/source-1080-light-02/host-gap-correlation.json)。该全trace最大gap位于fence采样约3秒处，早于5–30秒稳态窗；某个完整落入Q→Latch保证窗的约27.47ms采样区间，6角色都获得CPU时间，总25.040ms；这排除了“该整段窗口六线程完全没有CPU时间”这一简单说法，不排除读点之间暂停、guest某核饥饿或GPU等待。run_state1是running/runnable，不能说当前物理核一定正在执行。

## 修改与后续边界

- display_profile新增1080×1920/480验证，gateway启动识别该profile；M1持久化启动配置同步。
- hardware worker/probe CLI允许1920；matrix新增max-size与4/8/12/16M目标，保留fixed-source重开和实际readback。
- UDP touch bridge不再硬编码720P，读取指定emulator的有效/物理尺寸，严格拒绝异常；不改native多指协议。旧scale_touch已做后续缩放，原hardcode主要存在边缘量化差，并非已证明整幅触控错位。
- physical-vsync试验保留资源/geometry，可识别合法720和1080 profile；本轮没有改变物理vsync值。
- 新SF readiness与宿主vCPU工具及回归测试。78项针对性检查通过（display15、gateway4、hardware12、vsync5、readiness10、host14、touch geometry5、native touch13）。

目前仍未证明严格稳定60FPS或120FPS。本轮不含公网媒体、手机流量、远程V50、P2P成功率、声学同步或光学触控延时测试；公网ping健康只证明入口存活。正式App媒体还是此前实现，这个UDP实验不能当作已发布产品。下一步可在精确视频身份下测试暂停静默→恢复首帧的Q/L/P，及手机实际90Hz模式的选择原因，再分离目标时间等待和不规则调度。

[最终读回](evidence/pre-latch-20261001/final-health.json)保存物理配置、常亮、诊断关闭、原phone刷新设置与服务健康；实验环境按用户要求保留1080P。原始trace/栈/精确旧配置均在owner-only私有位置，仓库只有源码、固定数值证据和文档。
