**Raw 提交预算实验：2026-10-02 cap与精准预算结果**

四轮 cap bundle 已完成。两轮120配置的 host FIFO 等待较小，但第一轮60的旧帧积压未在第四轮同等复现，手机展示数据尚未证明120全链路更稳定。精准 raw-submit60/120 ABBA已完成，下半部给出独立预算数据；长时/分辨率/缓冲/丢包持续性结果仍由后续报告补充。

本稿只离线分析既有数字文件，没有操作设备、服务或安装。实验走真实 M1 Morphe YouTube → Apple硬件H264 → authenticated UDP物理LAN → 实验手机客户端。实际编码尺寸720×1280、目标8Mbps、wire32Mbps、manual pixel pool、FIFO、hint120、submit lead0、手机实际buffer80ms，panel开始/结束回读均约120Hz。源视频播放器质量标记为unverified，未确认媒体文件FPS。

注意manifest的conditions.buffer_ms标量仍记录默认60；实际buffers=[80]、每轮记录和phone.buffer_ms均为80，本稿采用实际值。

cap60/120同时改变Python FrameRateBudget、native --fps以及phone FPS limit。它们均不是gRPC采样FPS限制；截图请求不含FPS字段。因此这四轮只能说明整组配置的阶段表现，不能单独证明预算的因果收益。

**精确窗口与证据边界。** host稳态使用首个gRPC return后的[5,30)秒，固定25秒；逐帧阶段用同一截图capture_seq/PTS跟踪到输出。手机主指标独立使用phone.first_server_packet后的[5,30)秒，固定25秒，取sorted unique SF timestamps、count/25秒，只统计两端均在窗内的相邻gap。每轮都记录左右触边gap与SF时间戳覆盖；完整输入SHA256和所有窗口绝对边界见[cap-analysis.json](evidence/overnight-20261002/cap-analysis.json)。

| 轮次 | host capture FPS | raw queue p99 / max ms | pending2 / raw替换 | host gRPC→socket p99 ms | VT→callback p99 ms |
|---|---:|---:|---:|---:|---:|
| cap-01-60 | 59.24 | 30.9371 / 33.9410 | 62 / 1 | 43.3311 | 11.9956 |
| cap-02-120 | 59.32 | 0.3229 / 2.3400 | 0 / 0 | 16.8126 | 12.2156 |
| cap-03-120 | 58.36 | 1.2510 / 6.7850 | 0 / 0 | 20.8640 | 13.1062 |
| cap-04-60 | 57.64 | 10.3032 / 16.8500 | 0 / 0 | 24.1270 | 12.3092 |

第一轮60有连续30帧（seq1145–1174，capture跨度0.4707秒）raw queue>16.667ms，29次出队前已进入更新截图，队列最大33.941ms。第四轮60只有一帧略高于16.667ms，没有pending2、没有raw替换，最大16.850ms；原先两帧旧队列没有复现。两轮120没有pending2，queue p99分别0.323/1.251ms。

预算是容量2的token bucket，允许短时赶上；FIFO(maxlen2)限制帧数，不限制年龄。在接近60FPS的投递阶段，60预算缺少清空积压的余量。实际Python submit间隔<5ms次数依次为0/5/6/3，未发现60特有burst。native slot wait p99均≤0.001ms，pipe write p99约5.1–7.0ms，没有稳态持久slot饱和；单次pipe仍有长尾。

| 轮次 | 手机固定25s SF帧数 / FPS | gap p99 / max ms | >50 / >80 / >100ms | 左 / 右触边gap ms |
|---|---:|---:|---:|---:|
| cap-01-60 | 1480 / 59.20 | 33.1507 / 49.7300 | 0 / 0 / 0 | 16.5759 / 16.5758 |
| cap-02-120 | 1474 / 58.96 | 33.1522 / 82.8827 | 3 / 1 / 0 | 16.5767 / 24.8645 |
| cap-03-120 | 1457 / 58.28 | 33.1488 / 91.1556 | 5 / 1 / 0 | 16.5712 / 16.5725 |
| cap-04-60 | 1442 / 57.68 | 41.4293 / 91.1476 | 6 / 3 / 0 | 16.5731 / 16.5698 |

四轮手机固定稳态窗都没有>100ms的窗内gap，但两轮120仍各有>80ms gap；第一轮60的gap最大值反而较小。不能用这些结果宣称120必然更平滑，也不能把未落进窗内的触边gap或未观测时间填成零。

**host选中截图的手机关联窗口。** 补充口径仅使用唯一截图PTS、唯一raw submission和唯一phone input建立接收跨度，再统计该跨度内SF时间戳。这不是逐PTS展示帧cohort；可能包含窗前已入队帧并排除窗末待展示帧。四轮现有文件没有重复phone input PTS或重复/乱序SF时间戳。

| 轮次 | 严格匹配输入数 | 关联接收span秒 | span内SF帧数 / FPS | gap p99 ms |
|---|---:|---:|---:|---:|
| cap-01-60 | 1480 | 24.982307 | 1479 / 59.2019 | 33.1507 |
| cap-02-120 | 1483 | 24.939266 | 1480 / 59.3442 | 33.1503 |
| cap-03-120 | 1459 | 24.966299 | 1456 / 58.3186 | 33.1488 |
| cap-04-60 | 1441 | 24.977583 | 1440 / 57.6517 | 41.4293 |

两种手机窗口边界不同，不能混用计数或分母。主结论采用固定25秒手机窗；关联span用于检查host阶段变化能否在相近截图时间段关联到手机表现，未做host-phone时钟相减。

**完整观测范围。** JSON还保留每轮完整trace事件计数、capture跨度、包含startup的阶段分布及完整SF观测范围。trace entries不作为展示帧数。SF采集第一次非空ring只建立baseline，其后约32秒才计入新时间戳，因此完整SF count/采集elapsed与35秒phone receive窗口不同；以下整段数字不能替代上面的稳态窗。

| 轮次 | source SF采集秒 / FPS | source gap max / >100ms | phone SF采集秒 / FPS | phone gap max / >100ms |
|---|---:|---:|---:|---:|
| cap-01-60 | 32.169 / 57.385 | 158.7251 / 1 | 32.169 / 55.955 | 132.5998 / 1 |
| cap-02-120 | 32.330 / 57.438 | 97.8291 / 0 | 32.561 / 57.277 | 82.8827 / 0 |
| cap-03-120 | 32.269 / 57.300 | 107.9779 / 1 | 32.188 / 56.854 | 91.1556 / 0 |
| cap-04-60 | 32.503 / 55.749 | 228.7833 / 1 | 32.449 / 54.301 | 240.2838 / 1 |

所有Python trace都有clean summary且报告drop0，native四轮均缺最终summary。稳态逐帧join除cap01一次raw replacement外均成功，但不能因此声称native诊断无丢失或退出全程覆盖。source SF与host/phone没有经验证的时钟映射，本稿只保留source完整观测，未把它裁成host稳态窗。

**精准预算实验控制。** 后续已完成的四轮固定native --fps=120、phone fps_limit=120及上述其余配置，仅显式改变--raw-submit-fps为60→120→120→60。manifest需通过匹配实验client、worker有效预算、native FPS参数与phone FPS的typed回读；实际提交频率以trace为准。更新时保留本组cap bundle，分别报告host旧帧减少、编码阶段与手机展示是否稳定复现，以及是否值得用用户接受的约20ms缓冲权衡。

原cap bundle只支持120整组配置减少host等待；不能把它与下列精准预算数据混为同一版本对照。

**精准raw-submit预算ABBA。** 四轮均通过matched实验client、worker有效预算、native FPS参数与phone FPS的严格typed回读；native VT ExpectedFrameRate API均status0/readback120。原生编码器和手机FPS固定120，预算依次60/120/120/60，buffer80ms及其他控制一致。

| 轮次 | raw预算 | host capture FPS | raw queue p99 / max ms | host gRPC→socket p99 ms | VT→callback p99 ms |
|---|---:|---:|---:|---:|---:|
| budget-01-60 | 60 | 57.84 | 12.2410 / 13.1090 | 24.7836 | 12.1968 |
| budget-02-120 | 120 | 59.88 | 0.2811 / 1.9280 | 14.9180 | 11.3748 |
| budget-03-120 | 120 | 59.88 | 0.2681 / 1.9850 | 15.9416 | 11.3264 |
| budget-04-60 | 60 | 59.80 | 12.4224 / 13.0020 | 25.7406 | 10.9707 |

两轮60预算的raw queue p99都约12.3ms，两轮120都约0.27ms；回到60后host gRPC→socket p99也恢复到约25.7ms，这一阶段等待的差异能复现。四轮均无pending2、无raw replacement、无raw queue>16.667ms；这次没有重演cap01的两帧旧队列。该结果支持用120作为消除约60FPS来源下host等待的实验候选，不代表来源或手机显示了120FPS。

| 轮次 | 手机固定25s SF帧数 / FPS | gap p99 / max ms | >50 / >80 / >100ms | 左 / 右触边gap ms |
|---|---:|---:|---:|---:|
| budget-01-60 | 1442 / 57.68 | 33.1518 / 165.7493 | 8 / 3 / 2 | 16.5728 / 16.5733 |
| budget-02-120 | 1497 / 59.88 | 24.8658 / 58.0047 | 1 / 0 / 0 | 16.5752 / 16.5735 |
| budget-03-120 | 1497 / 59.88 | 24.8643 / 49.7238 | 0 / 0 / 0 | 16.5748 / 16.5714 |
| budget-04-60 | 1494 / 59.76 | 24.8627 / 49.7239 | 0 / 0 / 0 | 16.5736 / 16.5724 |

最后60预算手机SF为59.76FPS、p99为24.8627ms，与两轮120的59.88FPS、p99约24.865ms接近，因此不能说120必然让手机更平滑。首轮60有两次>100ms展示gap，同时host capture只有57.84FPS，其他三轮是59.80–59.88FPS；首轮供帧更慢是明显混杂，不能把其手机停顿全部归预算。

**5秒分窗与phase变化。** JSON逐轮保留5–10、10–15、15–20、20–25、25–30秒的host queue和独立phone SF窗口；各分窗包含左右触边gap与覆盖，未把跨窗gap丢弃后当成不存在。host/phone分窗采用各自起点，不直接代表相同帧的延迟。下表列每轮五个host raw queue p99及手机SF FPS，说明单一25秒分位不能代替持续表现。

| 轮次 | 五个host queue p99 ms（按时间顺序） | 五个phone SF FPS |
|---|---|---|
| budget-01-60 | 12.606/12.826/0.204/0.239/12.411 | 58.20/58.00/55.60/57.60/59.00 |
| budget-02-120 | 0.393/0.251/0.373/0.228/0.193 | 59.80/60.20/59.80/59.80/59.80 |
| budget-03-120 | 0.268/0.274/0.211/0.258/0.218 | 60.00/60.00/59.60/60.00/59.80 |
| budget-04-60 | 12.687/12.637/0.285/0.471/0.211 | 60.20/59.80/59.40/59.40/60.00 |

**版本与source fingerprint。** cap四轮probe SHA为44b9bfa0…，精准预算四轮为2395f75a…（新增codec-file实验）；两组encoder SHA均59264ab7…、packetizer SHA均10aa36d3…。两组分别比较，不把不同probe版本汇总成同一软件的改善。精准预算组四轮binary hash一致，预算回读、native API120回读、phone FPS120回读均通过。

budget01/02/04的每轮source_unchanged=true；budget03为false，唯一发生变化的路径是scripts/probes/run_surface_hint_matrix.py，其hash从bc74bb87…变成9d93f3a1…。root在这段时间新增120秒/loss/touch可选CLI，默认行为不变；完整streaming worker、UDP runner和native encoder源码hash在预算组一致，但不能把全部计算源码称为完全未变。完整start/latest哈希和逐字段差异已保存在JSON，不以参数回读替代源码身份验证。

**适用范围。** 120 raw预算可以作为后续实验的host等待候选；当前数据没有证明全链路在所有时间段持续更平滑，也没有测量物理单向延迟、触控或声学AV同步。后续分辨率正反、buffer80/100、120秒长轮和周期丢包/单指触控需要分别看持续窗口与phase变化，不能把35秒稳定段外推。正式App UDP尚未发布，本报告属于独立匹配实验客户端。
