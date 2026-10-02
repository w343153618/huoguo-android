# 原生触控与 UDP 串流第二代验证

本目录是独立实验，目标主机为当前 M1。现有 App、M1 网关、M5 磁盘和线上账号均保留。这里还不是可安装使用的新一代完整 App。

2026-10-01 后续进展：加密 UDP 视频、AAC 音频、单指触控及限时恢复已经在真实 M1 YouTube / 一加 15 上跑通；最新无主动丢包与周期性 2% 丢包实验的实际手机 Surface 呈现 cadence 均约 53 FPS，丢包轮仍有近 500 ms 停顿。详见 [当前真实结果与未完成项](../../docs/private-udp-protocol-progress-20261001.md)。以下早期 C++ 组件检查和当时实例尺寸作为历史证据保留，不代表最新配置或产品验收。

## 已完成及边界

2026-10-01 在 M1 编译固定提交的 `moonlight-common-c` 和项目 C++ 触控适配器。依赖提交见 `upstreams.json`，第三方源码及编译结果存于用户缓存目录，不进入本仓库。

- 原生触控：独立 pointer ID、归一化坐标、压力、最多十指、边界检查、断开/调整尺寸时释放手指；拒绝隐式鼠标回退。
- 纠错：链接 Moonlight 使用的 nanors，在 10 个数据片和 2 个校验片中遍历全部 66 种双丢失组合，数据恢复一致；三个数据片丢失被拒绝。20% 是额外校验流量占原数据的比例，不能解读为任意公网条件下保证恢复 20% 随机丢包。
- 安卓输入集成：原生 C++ 生成 scrcpy 触摸控制包，经现有音频/控制服务传到 M1 测试安卓。安卓实收 SOURCE_TOUCHSCREEN=4098，以及 DOWN、POINTER_DOWN、MOVE、POINTER_UP、UP；双指阶段 count=2。见 ../../docs/evidence/gemini-review-20260930/native-android-touch.json。
- 以上不是 Moonlight 主机握手、手机 UDP 视频、异地 V50 或公网抗丢包验收；不得据此宣称已获得稳定 60/120 FPS。

## 复现

```sh
python3 experiments/moonlight-v2/build_native.py
```

需要 CMake、Apple Clang、OpenSSL。构建脚本可使用既有项目外工具环境 `~/.cache/huoguo-v2-tools/bin/cmake`；源码缓存默认为 `~/.cache/huoguo-v2-sources`，可通过 HUOGUO_V2_CACHE 改变。脚本严格核对上游及子模块提交，并拒绝覆盖修改过的依赖。

安卓输入验证需要已构建并安装本仓库 diagnostic-source 到测试模拟器；不使用生产账号。

```sh
./gradlew :diagnostic-source:assembleRelease
adb -s emulator-5556 install -r diagnostic-source/build/outputs/apk/release/diagnostic-source-release.apk
python3 experiments/moonlight-v2/verify_android_touch.py \
  --output docs/evidence/native-android-touch.json
```

输入验证暂时打开诊断场景，结束后返回上一界面；需要现有 540x1200 测试实例，脚本不调整分辨率。其运行依赖仍位于外部 `~/Documents/ChatGPT/others/android-remote/m1-compare/hardware/`（venv、macos-h264、scrcpy-audio-control），可用 `--runtime` 明确指定。源文件来自当前工程目录。单独运行 `touch_probe --emit` 的坐标协议目前固定 540x1200，生产适配器本身支持尺寸更新。

## 下一阶段验收

1. 接入具备安卓输入后端的兼容主机，确认多点触摸能力标志；不支持时明确报错，不能改成控制 Mac 鼠标。
2. 比较模拟器现有 gRPC 抓屏与窗口级 ScreenCaptureKit/IOSurface 捕获，记录源端唯一帧率、捕获/转换/编码耗时和拷贝量。仅写 C/Rust 不能修复源端供帧不足。
3. 实现完整视频 UDP/RTP 与音频 Opus 链路，使用媒体时间戳和真实播放头同步，去掉不必要的隐藏排队。
4. 先在既有 Headscale/Tailscale 上验证 UDP 直连；明确区分 direct 和 DERP，不能把 UDP 直连和 TCP 内层串流混为一谈。
5. 再验证国内 UDP/QUIC DATAGRAM 中转、鉴权及断网重连。新实时媒体线路以 P2P UDP 优先、国内 UDP 中继为回退；原 NPS TCP 服务保留作独立对照和旧客户端兼容，不作为新 UDP 媒体的自动回退。不能把 QUIC reliable stream 当成不可靠实时 datagram。
6. 手机连接 M1 刷真实 YouTube/抖音，至少记录源内容帧率、捕获帧率、收到/显示帧率、p95/p99 帧间隔、丢包/FEC/重传、音画偏差、输入延迟、功耗。V50 必须单独实测；推荐缓冲最多 80 ms。

采用 Moonlight/Sunshine 派生代码需遵守其 GPL 许可证，保留版权说明并按许可提供对应源码。网易产品未在本目录被反编译或复制。

同日后续实验完成真实M1/一加15的14轮UDP ingress、关键帧突发和100ms有界恢复对照，见[最新结果](../../docs/udp-burst-and-ingress-results-20261001.md)。短窗口编码约束缓解了整帧预算断供；剩余中短停顿仍存在，未达到稳定60/120FPS或公网验收，正式App线路未因此自动替换。
