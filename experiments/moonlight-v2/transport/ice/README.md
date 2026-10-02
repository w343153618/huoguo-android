# 独立 ICE UDP 后端实验

这一步提供可复用的 C 数据报后端，以及**真正经过 UDP socket 的同机双 peer 收发检查**。不改 App、gateway、AVD、NPS、NPC、Clash、云服务或手机，不创建持久服务，也不启用公网端口。音视频媒体接口没有 TCP 或可靠 DataChannel 回退。

采用 [官方 libjuice](https://github.com/paullouisageneau/libjuice) 的 C ICE 实现，固定实际检出的 [commit b89c792e3612faf2f12cf35bcc56857313a06be3](https://github.com/paullouisageneau/libjuice/tree/b89c792e3612faf2f12cf35bcc56857313a06be3)，项目版本为 1.7.4，许可证 MPL-2.0。公开依赖源码、生成构建目录和库只放 `/private/tmp`，Git 仅保存本工程包装、少量补丁和有限元数据。

## Mac 的物理出口约束

`hg_ice_config.bind_interface` 在 Mac 上必须显式为 `en7` 或 `en0`。组件确认接口存在、UP、RUNNING，取得该接口当前 numeric IPv4，不使用默认路由选出的地址、不广告其他接口或 utun 的 host candidate。然后 [小补丁](libjuice-physical-ipv4.patch) 将接口字段从 `juice_config` 传到真正的 `udp_create_socket`：

1. 在 socket 创建成功后、bind／STUN／TURN／媒体 send 之前，用 `setsockopt(IPPROTO_IP, IP_BOUND_IF=25, ifindex)` 约束实际 UDP socket。
2. 用 `getsockopt` 读回接口索引，检查长度、非零索引和完全相等；任何步骤失败都关闭 socket，不能继续 bind／发送，不会退回默认接口。
3. 当前明确只支持 IPv4。不会仅凭 `IPV6_BOUND_IF` 的存在宣称 dual-stack 的 IPv4-mapped 流量也受相同约束；IPv6 需要后续独立实现和验收。
4. 只使用 THREAD 模式，每个 peer 独立 socket。带接口约束时拒绝 POLL 和 MUX；上游 MUX 只按端口复用，未把接口作为复用键，不能直接沿用。补丁也给旧 mux-listen 的局部扩展配置补上清零，避免未初始化字段。

本机 Apple SDK `netinet/in.h` 定义 `IP_BOUND_IF` 为 25，注明支持 set/get bound interface；本次实际 socket 的读回证据也已保存。**这验证的是套接字出口约束，不是腾讯云最终观察到的公网源 IP／地域。** 将来对用户自有国内 STUN／TURN 服务器验证时，还必须从服务器侧核对实际来源；不能用同机验证宣称已完成 Clash TUN 开启状态下的公网路径验收，也不能改变原有国内来源限制。

网卡断开时，本组件不自行迁移路径。控制层可以关闭旧 ICE endpoint、重新选择另一个允许的物理网卡并建立新 ICE 会话；绝不因断网自动选择 utun 或 TCP。

## UDP 与信令边界

[C API](ice_udp_backend.h) 的 `hg_ice_send` 只调用 `juice_send`，最大应用数据报 1,200 字节，返回 EAGAIN 等发送失败时不无限重传或累积旧媒体。小补丁拒绝启用 ICE-TCP，包装也拒绝远端 TCP candidate。只有已经建立的 UDP 候选对可以承载应用数据。

STUN／TURN 配置只接受 numeric IPv4 与 UDP 端口；TURN 用户名和密码只能经内存配置传入，不能使用 URL、命令行或日志。官方实现将 TURN 地址按 SOCK_DGRAM 解析，Allocate requested-transport 固定为协议 17，数据走同一 UDP socket。本次没有配置任何 STUN 或 TURN 服务器，没有声称它们已实测。

外部登录、认证、SDP／ICE 候选交换可以使用已认证的 HTTPS 或私有管道；它们不是媒体路径。`hg_ice_local_description_private` 与 `hg_ice_remote_description_private` 只接收／返回调用者的内存缓冲，组件不会写入文件、命令行或日志。当前包装使用完整描述交换，先等 gathering-done，再交换两端描述；没有额外实现 trickle-ICE 信令产品。

libjuice 的 WARN／ERROR 日志也可能包含 ufrag 或输入 candidate，因此只降到 WARN 不足以满足隐私边界。包装在创建前使用 `JUICE_LOG_LEVEL_NONE`，上游符号隐藏在组件内，仅导出七个 `hg_ice_*` 函数；组件日志只有自有白名单统计。不得打开上游日志或把 SDP 传给通用调试日志。

## 接入既有媒体代码的最短入口

`ice_udp_backend.h` 是 Mac 和 Android 共用的 C ABI。后端只做 ICE 与 UDP 数据报，不自创生产加密、媒体可靠传输或 FEC 协议：

1. 已认证 HTTPS 会话取得应用会话 key 与 ICE 描述；调用 `hg_ice_create`、`hg_ice_gather`、私有描述交换。
2. 现有 native packetizer 的 `[u16BE size][HGUD]` 不变。控制层沿用现有 HGUE AES-GCM 外层、会话序号和方向 nonce，对每个 HGUD 分片加密后交给 `hg_ice_send`，取代原来的 UDP `sendto`。
3. `receive` 回调取得一个完整应用数据报。先走现有认证、来源／序号重放检查，再把 HGUD 明文交给既有 `NativeUdpFec.nativeAccept`；不能把 ICE 连接成功当作应用媒体认证成功。
4. 音频、视频及短的媒体恢复反馈可使用同一个已认证 UDP 通道，通过既有外层类型区分。这里没有音频编码／播放、音画同步或触控实现，也没有声称这些已接入。应用 KEYFRAME 反馈仍由现有有界恢复控制处理；libjuice 重发的是 ICE/STUN 控制包，不为过期媒体做可靠重传。

回调中的 buffer 只在该次调用有效，应尽快校验或复制进有界队列，不要在 ICE 收包线程做阻塞解码／IO。停止连接和 `hg_ice_destroy` 由控制线程执行，不得在 receive 回调中销毁自己；销毁时调用者应停止其他 API 调用并等回调结束。更换 ICE 通道而复用应用会话 key 时，必须保持 AES-GCM 序号单调不重复，或重新认证并生成新 key；不能把 ICE 重连当作清零 nonce 的理由。

## 构建与复跑

需要外部公开源码缓存、已有 CMake 运行时和 Apple Command Line Tools。没有 CMake PATH 时，构建脚本使用已有 `~/.cache/huoguo-v2-tools/bin/cmake`。初次缓存可显式下载并固定公开 commit；本工程 `build.py` 不自行联网：

```sh
git clone https://github.com/paullouisageneau/libjuice.git /private/tmp/huoguo-libjuice-upstream
git -C /private/tmp/huoguo-libjuice-upstream checkout --detach b89c792e3612faf2f12cf35bcc56857313a06be3
python3 experiments/moonlight-v2/transport/ice/build.py
```

构建前验证原缓存 exact HEAD 且 clean，复制到带专有 marker 的临时目录，再对副本检查／应用补丁，不修改上游缓存、系统 SDK 或生产目录。输出：

- Mac arm64：`/private/tmp/huoguo-ice-build/host-arm64/libhuoguo_ice_udp.dylib`。
- 同机验证器：`/private/tmp/huoguo-ice-build/host-arm64/ice_local_probe`。
- Android arm64 API 23：`/private/tmp/huoguo-ice-build/android-arm64/libhuoguo_ice_udp.so`。

```sh
python3 experiments/moonlight-v2/transport/ice/build.py --android
python3 experiments/moonlight-v2/transport/ice/verify_local.py --interfaces en7 en0 --rounds 2
```

Android 构建使用已有外部 NDK `~/Library/Android/sdk/ndk/29.0.14206865`，可以通过 `--ndk` 指定路径。此 `.so` 是 C ABI 库，**没有 JNI 导出、没有装进 APK、没有手机实测**。Android 端不接受 Mac 接口名；将来若有 VPN，必须在 Android socket 创建点接入相应 `Network.bindSocket`／`VpnService.protect` 路由策略并验证，不能把 Mac 的 IP_BOUND_IF 或 Android 编译通过误当作 Android VPN bypass 已实现。

验证器只临时创建两份本机 endpoint，用同一指定物理接口的本机 host candidate 交换 16／256／1,080／1,200 字节合成数据，双向校验全部字节。SDP／ufrag／pwd 只在该进程的两份缓冲中交换，不输出；库销毁后短时 socket 和线程结束。300 轮每轮一次去程和一次返回，没有向公网发送。

## 本次证据与限制

[白名单实际本机证据](evidence/local-host-20261001.json) 包含 en7、en0 各两轮，四轮全通过；每轮 300 往返／600 个应用 UDP 数据报／382,800 字节，零 payload 错误、双方均选 host→host 且 state=COMPLETED。两端每个 socket 都真实读回接口约束：本次 en7 索引 30、en0 索引 16（索引不是永久配置）。

| 接口 | 轮次 | 同机往返 p50 | p95 | 最大值 |
| --- | ---: | ---: | ---: | ---: |
| en7 | 1 | 286μs | 475μs | 1,305μs |
| en7 | 2 | 286μs | 346μs | 1,248μs |
| en0 | 1 | 287μs | 435μs | 3,975μs |
| en0 | 2 | 290μs | 456μs | 2,252μs |

这些数值包含本机控制循环等待，是**同机合成数据的受控往返**，不是手机延时、真实视频流畅度、NAT 打洞成功率、公网抗丢包或 TURN 性能。两 peer 使用本机地址，内核可以在本机交付数据；不能宣称数据已经往返物理网络。

未知接口在真实 socket 创建路径失败，带绑定的 MUX／POLL 被拒绝，TCP 开启／candidate 被拒绝，超过 1,200 字节的媒体被拒绝。这些检查在每轮重复通过。普通沙箱首次运行在候选 gathering／socket 初始化阶段失败，返回 error_code=5／零应用数据报；该次未记录底层 errno，不能把它算作实际收发成功。之后在授权的本机执行环境完成上述验证。构建与二进制类型／导出检查另见 [构建元数据](evidence/build-20261001.json)。

未做：srflx／prflx／relay 实测、真实家庭 NAT／公网 ICE、国内移动线路、V50、音视频编解码／同步、HGUE+FEC 在 ICE 上的完整联调、信令产品与会话恢复部署。候选失败时目前返回失败，保留用户要求的 UDP 原则；将来的中转只能增加 TURN-over-UDP 验证，不恢复 TCP 媒体回退。
