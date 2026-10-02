# Native host deadline 时钟契约（2026-10-02）

这次改动只固定 Darwin 上 native packetizer 与 Python sender 的绝对时钟域。它不改变 pacing、等待方式、QoS、码率、80 ms lifetime、参考帧规则或手机端播放缓冲。当前已完成独立编译、API bracket 与契约测试，随后完成同一新 binary 的四轮1080P／12Mbps手机LAN串流验证，见[独立ABBA记录](socket-wait-1080p-real-video-20261002.md)。这不构成实际Mac休眠／恢复验证，也不证明这个修正带来了FPS收益。

## 为什么需要显式契约

HGUD header 的 `captureUs` 是 native packetizer 收到编码 AU 后的 host 时间。Python `SocketVideoGate` 计算 `captureUs + lifetimeUs`，随后以 `time.monotonic_ns() // 1000` 检查这个绝对 deadline。因此两者必须使用同一个 host clock 域，不能只假定名字含有 monotonic 就一致。

在本机 Python 3.14.7，`time.get_clock_info('monotonic').implementation` 为 `mach_absolute_time()`。旧 native `std::chrono::steady_clock` 的本机 libc++ 读取 `CLOCK_MONOTONIC_RAW`。Apple 的实现中，后者来自 `mach_continuous_time()`，包括 Mac 系统休眠时间；`CLOCK_UPTIME_RAW` 来自 `mach_absolute_time()`，不包括系统休眠。两种 epoch 在尚未累积休眠差异时可以相等，跨系统休眠后不能直接相减。[Apple clock_gettime 实现](https://github.com/apple-oss-distributions/Libc/blob/main/gen/clock_gettime.c)、[Apple clock_gettime 手册](https://github.com/apple-oss-distributions/Libc/blob/main/gen/clock_gettime.3)、[LLVM libc++ chrono 实现](https://github.com/llvm/llvm-project/blob/main/libcxx/src/chrono.cpp)、[CPython Apple monotonic 实现](https://github.com/python/cpython/blob/3.13/Python/pytime.c)。这些 upstream 链接用于解释 API 契约；当前机器的实现读回及 bracket 数据单独保存在下面的 evidence 文件。

本次归档的 8 轮共 56,837 个有配对记录的 native frame，`Python first_read - native capture` 均为小的正值。16 组旧 libc++ API 读数也全部落在同次 Python before/after bracket 内。这证明当前测量期间两者一致，**不是**对已发生几秒 deadline 偏差的诊断。修复预防的情况是后续 Mac 系统休眠导致 native continuous clock 比 Python uptime 大；旧 Python socket guard 因此会多给这段时间，而不是提前数秒拒绝帧。没有在本机主动触发系统休眠，也没有把这个潜在问题当作此前 A/B 改善的原因。

## 各字段的时钟域

| 字段/模块 | 时钟域 | 可直接比较的范围 |
| --- | --- | --- |
| 新 native `captureUs`、pacer slot、IPC deadline | Darwin `CLOCK_UPTIME_RAW`，微秒；Linux 保留 `std::chrono::steady_clock` | native 内部，以及本机 Python monotonic |
| Python socket first/last read、write、deadline guard | 本机 Python monotonic，微秒 | 对应 native HGUD capture/lifetime |
| Swift capture trace、Python capture trace | `CLOCK_MONOTONIC` | 同 trace 域的相邻间隔；不能直接减 native/Python socket 时间 |
| logical AU `source_pts_us` | 原有 emulator 估计生成时间标记 | frame identity 与独立定义的源时间指标 |
| 手机 `NativeUdpFec` assembly deadline | 手机首次 authenticated shard 的本地 arrival + lifetime | 手机 RX/expire 本地域 |

当前 capture trace 相对 Python uptime 约有 −5.747 秒的绝对偏移。trace 不参与 HGUD socket deadline；分析只能按准确 frame identity 关联，再在各自域计算相邻间隔。此次没有修改 Swift/Python capture trace。

手机 `PhoneReceiver` 保留 host capture 为 metadata/identity，但在交给 core receiver 前，将 header offset 24 的 capture 替换成该帧的手机首次 arrival；它不以 Python 握手 host clock 估算 assembly deadline。`clock_mapping_rejected` 当前表示旧 frame 超出 256 窗口、并发 mapping 达到 8 或同 frame 原始 header 不一致。这里没有 host/phone clock offset 判定，不能将 Mac 休眠的 host epoch 差异直接归因于这个计数。

## 实现与验证

`host_clock.hpp` 在 Darwin 明确读取 `clock_gettime(CLOCK_UPTIME_RAW)`，失败时拒绝读数；其他平台保留原有 steady clock 表达式。packetizer summary 输出 `clock_domain=host_clock_gettime_CLOCK_UPTIME_RAW_us`，便于每轮读回。`std::this_thread::sleep_for` 保持原样；本次不是 timer/QoS 优化。

- `tests/native/host_clock_contract.cpp` 注入 uptime 10 秒、continuous 100 秒，确认选择 uptime 并检查读失败，覆盖真实机器当前没有累计休眠差异时不易暴露的问题。
- `scripts/probes/verify_native_host_clock.py` 独立编译真实 header reader，16 次 native 读数全部处于 Python send/receive 时间 bracket 中。该脚本无设备、socket、encoder 或 live service 操作。
- `python3 -m unittest tests.test_native_host_clock tests.test_udp_socket_pacing -v`：32 项通过；清理 reader 句柄后重跑 3 项新契约测试，通过且无 ResourceWarning。
- 独立 Release CMake build 与 `phone_clock_fec_replay` ctest：通过。传入合法 `h264` marker 后 EOF 的 summary 读回：目标 clock domain、32,000,000 bps、final=true；这只是 binary contract smoke check，不是 FPS/延时结果。

证据：

- [修改前 clock API 与 8 轮配对](evidence/socket-wait-20261002/clock-domain-review.json)
- [新 header 的真实 API bracket](evidence/socket-wait-20261002/clock-contract-bracket.json)
- [构建命令、source SHA、binary SHA 与 summary](evidence/socket-wait-20261002/clock-build-validation.json)

新 binary：`/private/tmp/huoguo-udp-uptime-credit/host/h264_udp_packetizer`，SHA-256 `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`。

此前 8 轮冻结 binary：`/private/tmp/huoguo-udp-paced-credit/host/h264_udp_packetizer`，SHA-256 `10aa36d3d159c5290df2e7f0ccc767271e979a7148552450bc0da511104d4b0f`；构建后读回未变。此前数据继续归属于旧 binary。后续若用新 binary 做 A/B，两组都必须使用同一个新 SHA，单独记录 clock domain，不与此前 8 轮混作同一实现。

建议每轮核对 summary `clock_domain`，native/Python first-read 差值的符号和范围，明确 32M wire、2048 byte catch-up、80 ms lifetime/guard 未变，准确 frame identity 与 binary/source SHA。模拟契约测试本身不构成实际系统休眠／恢复验证，跨系统休眠的真实验证需单独安排；四轮真实手机 LAN 验证见 [1080P 独立记录](socket-wait-1080p-real-video-20261002.md)。
