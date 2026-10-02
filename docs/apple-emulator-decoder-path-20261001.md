# Apple Emulator 的源视频解码路径

日期：2026-10-01。此记录只核查官方源码和已安装 SDK 二进制，没有修改环境变量、播放器、AVD 或服务。源视频解码、Mac 串流编码和手机串流解码是三个独立阶段。

## 本机证据与限制

- SDK Emulator `37.1.11`，build `15917651`；检查的二进制为外部 SDK 路径 `~/Library/Android/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64`。
- 二进制 SHA-256：`eaa97a970b81f81640db73ed79e19ee173323653a0a1203217e1199d078011f3`。
- 当前运行进程的定向环境读回已确认 `ANDROID_EMU_MEDIA_DECODER_VTB=1`，因此 VTB 已启用，不能把“没有开启苹果解码开关”作为本轮根因或修复。
- 当前 YouTube UI 已观察到 1080p60 基线，后来手动选择720p60，并观察前后读回。这证明播放器所选档位；不单独证明每帧都以60FPS实际呈现，也不证明正在使用哪条宿主解码后端。
- `media.metrics` 有 `c2.goldfish.h264.decoder` / AVC 和 `c2.goldfish.vp9.decoder` / VP9 的1920×1080历史记录。历史记录可保留已发生的配置，不能认定它们同时活跃，也不能证明本次720p60的实际decoder、硬件使用状态或逐帧耗时。

下面的选择分支已与实际 binary 的有界函数反汇编交叉检查。官方 QEMU 参考 commit 为 `ae9d18d2b6261179fbd57fffec720a04f7bfb053`；公开commit不是已安装release的精确构建commit，不能据此声称整个binary逐字匹配。旧 `platform/hardware/google/aemu/main` 曾采用 env等于1才开VT的规则，当前binary和QEMU来源采用“除非env等于0，否则默认允许”，不能混用这两份实现。

## H.264：允许 VideoToolbox，但仍有软件回退

Guest `c2.goldfish.h264.decoder` 通过 `MediaH264Decoder` / `GoldfishMediaTransport` 向宿主传递压缩数据及解码操作。`RENDER_BY_HOST_GPU` 是输出／渲染方式，不是硬件解码保证。[Guest桥接源码](https://android.googlesource.com/device/generic/goldfish-opengl/+/5cd042c8586a38b408f390fcef979d58ace3da23/system/codecs/c2/decoders/avcdec/MediaH264Decoder.cpp)

宿主 Apple 分支默认构造 `MediaVideoToolBoxVideoHelper`，除非 `ANDROID_EMU_MEDIA_DECODER_VTB` 为 `0`。实际binary的 `MediaH264DecoderGeneric::initH264ContextInternal` 在 `0x100b47108` 比较ASCII `0x30`，等于0则跳过VT创建；否则在 `0x100b47180` 调用VT helper构造函数。[宿主选择源码，161–195行](https://android.googlesource.com/platform/external/qemu/+/ae9d18d2b6261179fbd57fffec720a04f7bfb053/android/emu/media/src/android/emulation/MediaH264DecoderGeneric.cpp)

helper传给 `VTDecompressionSessionCreate` 的属性为 `EnableHardwareAcceleratedVideoDecoder=true`，并非 `RequireHardwareAcceleratedVideoDecoder`。源码明确允许VT自身的软件实现；实际binary的 `recreateDecompressionSession` 在 `0x100b53e08` 同样读取Enable属性。所以VT session创建成功也不等于实际硬件使用状态已核实。[VT helper源码，507–548行](https://android.googlesource.com/platform/external/qemu/+/ae9d18d2b6261179fbd57fffec720a04f7bfb053/android/emu/media/src/android/emulation/MediaVideoToolBoxVideoHelper.cpp)

另一个回退发生在模拟器内：`try_decode` 检查helper的 `good()`；失败则删除该helper、构造 `MediaFfmpegVideoHelper(264, …)`，重放保存的参考链并继续软件解码。因此当前环境启用VT也无法排除某个具体码流的回退。[回退源码，205–209、278–303行](https://android.googlesource.com/platform/external/qemu/+/ae9d18d2b6261179fbd57fffec720a04f7bfb053/android/emu/media/src/android/emulation/MediaH264DecoderGeneric.cpp)

## VP9：当前 Apple 实现为宿主 libvpx

Guest的 `goldfish_vpx_impl.cpp` 使用共享内存桥接向宿主发 `InitContext` / `DecodeImage`，所以guest函数名称不能用于判断解码工作在guest CPU或苹果硬件上。[Guest VPX桥接源码](https://android.googlesource.com/device/generic/goldfish-opengl/+/5cd042c8586a38b408f390fcef979d58ace3da23/system/codecs/c2/decoders/vpxdec/goldfish_vpx_impl.cpp)

宿主 Apple 分支没有VT VP9 decoder选择：CUDA和GPU texture条件均为false，继而使用 `MediaVpxVideoHelper`。实际binary的 `MediaVpxDecoderGeneric::initVpxContext` 在 `0x100b4fcd0` 直接构造该helper，`createAndInitSoftVideoHelper` 在 `0x100b4fd40` 也相同。[VPX选择源码，68–96、114–163行](https://android.googlesource.com/platform/external/qemu/+/ae9d18d2b6261179fbd57fffec720a04f7bfb053/android/emu/media/src/android/emulation/MediaVpxDecoderGeneric.cpp)

helper使用 `vpx_codec_vp9_dx_algo` 和 `vpx_codec_decode`，线程数最高4。因此在这条路径中，增加虚拟CPU核数不会自动把decoder本身变为更多线程或Apple硬解。这不是对Apple所有VP9能力的概括，而是当前emulator后端的实现事实。[libvpx helper源码，44–75行](https://android.googlesource.com/platform/external/qemu/+/ae9d18d2b6261179fbd57fffec720a04f7bfb053/android/emu/media/src/android/emulation/MediaVpxVideoHelper.cpp)

## 下一项可区分的测量

先保留1080p60／720p60真实视频的相同内容、串流采集cap、手机Surface策略和SF采样，比较源视频SF新呈现、gRPC截图生成PTS／返回时间和编码阶段事件的共同稳态窗口。720p改善若重复出现，只能先归到“源播放档位相关”；必须进一步核对当前活跃codec，才能归到特定解码器。

要验证H.264实际硬解，未来应在确切活跃VT session中读回 `UsingHardwareAcceleratedVideoDecoder` 并记录失败／fallback事件；现有历史codec名称和VT初始化日志不能代替这项测量。要验证VP9负载，应把当前活跃codec身份与宿主libvpx decode阶段时间关联，而非只看过去的metrics。对活跃进程做调用栈采样只能提供后端活动线索，还应记录采样开销，不能当作无扰动的逐帧耗时。

若可选到同尺寸、同真实60FPS的AVC与VP9版本，再做codec对照。不能为了选AVC而实际降至30FPS，然后宣称60FPS远程链路已经解决。播放器content FPS、源Surface呈现、截图供帧、硬件串流编码和手机实际呈现需要分别报告。
