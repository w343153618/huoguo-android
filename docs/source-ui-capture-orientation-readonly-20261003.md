# M1 源 UI 与截图方向异常：只读鉴别 checkpoint

日期：2026-10-03。范围：检查已有图像、工程源码、历史记录与官方上游代码；本轮没有 ADB/SSH 设备命令，没有改变属性、重开播放器、启动媒体会话、新建 VM 或操作手机。正式 App 当时仍在手机前台。后续由主任务独立执行一次只读 gRPC 截图；此文档不把尚未执行的比较写成结果。

## 已有证据与边界

主任务提供的 M1 当轮读回：物理/逻辑主屏 `1080×1920`，`cmd display` 当前 mode `30.00 Hz`，rotation `0`、accelerometer rotation `1`；`debug.hwui.renderer=skiagl`、`debug.renderengine.backend=skiaglthreaded`。SurfaceFlinger GLES 字符串为 Google/Apple Emulator OpenGL Translator、Apple M1 Max、GLES 3.0/Metal。这些读回不是本子任务重新采集，也不是每帧 GPU 利用率。

本子任务只查看以下已有 PNG，均为 `1080×1920` RGBA：

| 外部已有文件 | SHA-256 |
| --- | --- |
| `/tmp/huoguo-alpha8-source-readback.png` | `e95f520ec61195a181b98ba904c71e95e8f4654af93efad85d75f2de74a1d8b8` |
| `/tmp/huoguo-alpha8-source-readback-2.png` | `8c24d05ddac2ed828e06f71a17b77083913f59d0e34a3adf283d9622452636f1` |

两张图的状态栏与底部导航方向正常，中间 YouTube 页面内容有上下翻转和垂直压缩。当前内容是 kids LIVE 动画页面，**不是 BBB**；不能把历史 BBB 的格式、内容帧率或呈现数据补到本轮。

图像支持“已有 `adb screencap` 结果重复出现局部异常”，尚不支持“物理显示整体已经翻转”。状态栏正常也使全图垂直翻转无法成为正确补救；它会把原本正常的系统 UI 一起翻转。当前没有同场景宿主窗口/光学观察，也没有当前 gRPC 原始帧。

## HWUI 与 RenderEngine 必须分开

Goldfish 上游 `init.ranchu.rc` 明确将 HWUI 默认设为 `skiagl`，RenderEngine 默认设为 `skiaglthreaded`，并列出 GL/Vulkan 的非线程与线程选项。因此当前值与公开模拟器默认契约一致，不能仅凭名字判定 Android 17/emulator 37 不兼容。[Goldfish init 上游](https://android.googlesource.com/device/generic/goldfish/+/refs/heads/main/init.ranchu.rc)

HWUI 是 App 绘制管线。官方 `Properties.cpp` 中 `getRenderPipelineType()` 将首次选择存入静态 `sRenderPipelineType`，后续 `peekRenderPipelineType()` 返回已锁定类型；运行中不能用普通属性写入强制转换已选的管线。因此只写 `debug.hwui.renderer=skiavk` 可设置以后尚未初始化的管线目标，**不代表当前播放器已切换**。新进程仍应按 PID 与 `gfxinfo` 的实际 Pipeline 核对，不能只看 `getprop`。[HWUI Properties 上游](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/libs/hwui/Properties.cpp)

该属性是 guest 全局目标，会影响之后初始化的其他 App，不是仅作用于 YouTube；恢复旧目标虽比重启播放器干扰小，仍有配置变更含义。当前仅记录方案，没有执行。即便播放器自然退出重开，也应重新读取 Pipeline；进程来源与初始化时机不能由属性值替代。

RenderEngine 是 SurfaceFlinger 合成管线。上游 `chooseRenderEngineType()` 把 `skiaglthreaded` 解释为线程 GL，在 `SurfaceFlinger::init()` 创建引擎时使用。显式 Vulkan 分支与默认能力探测分支不同；枚举可接受不等于本镜像已经验证兼容。仅改属性字符串不能证明运行中的引擎已切换。[SurfaceFlinger 选择与初始化上游](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/SurfaceFlinger.cpp)

以上检查的是 2026-10-03 官方上游 main 与本地 SDK proto，未把它们的源码版本与安装 Android 17 镜像逐文件匹配。不能宣称源码调研已经证明该镜像的驱动 bug，或把同为 Skia 的 App HWUI 与 RenderEngine 当作必须同步改动的一项配置。本轮保留 RenderEngine 默认 GL，也不做刷新率/renderer 广矩阵。

## 相关工程历史与当前应用位置

[2026-10-01 源因果记录](source-causal-results-20261001.md) 第 76–78 行已记录同一 M1 冷启动后 HWUI `skiagl`、App `Skia (OpenGL)` 与局部翻转/错位，恢复既有目标 `skiavk` 后**只重开播放器**，读回 `Skia (Vulkan)` 且页面正常。该轮是可参考的历史关联；不能追认当前 kids LIVE 的同一根因，也不能把该单轮表现称为全部卡顿修复。

本轮 canonical 源读取时 HEAD 为 `4520a57c23e6f268278fb98d0123280c78e51fb6`：

- [performance_profile.py](../performance_profile.py) 第 12–38 行通过 `DIRECT_HWUI_RENDERER` 写 HWUI 属性，从未写 RenderEngine 后端；按 boot ID 记已应用。`DIRECT_REFRESH_RATE` 当前只接纳 `60/120`，不能把 `30` 塞入该旧接口来补这轮物理 30 Hz；物理 mode 的既有部署是另一条契约，本任务不改它。
- [hardware_stream.py](../hardware_stream.py) 第 525–527、641–653 行使用宿主 EmulatorController `streamScreenshot(RGBA8888, display=0)`，按返回尺寸与完整字节数处理。它不是 `adb screencap` 的命令入口。
- [measure_source_rendering.py](../scripts/probes/measure_source_rendering.py) 第 110–118、140–146 行会改属性并 force-stop/start 测试 App；它不是本轮被动观察工具，不运行。

## 最小不改变 UI 的路径比较

第一步只取**一帧**现有 emulator 的 `getScreenshot`，不启动 `hardware_stream.py`、新 encoder、scrcpy 音频/触控或第二条媒体会话。后者完整脚本第 528 行起还会部署 guest 控制进程，不能为“取一帧”顺带运行整个串流入口。

本地 SDK 既有 `/Users/wyw/Library/Android/sdk/emulator/lib/emulator_controller.proto` 第 280–300 与 1387–1427 行已核对：`getScreenshot` 支持 `RGBA8888`、display `0`；输入 width/height `0` 表示不缩放，输出包含实际尺寸和 orientation。无显示/无新帧有明确空图或错误边界。公开同源 proto 也记录此契约。[EmulatorController 官方 proto](https://android.googlesource.com/platform/tools/base/+/refs/heads/mirror-goog-studio-main/emulator/proto/emulator_controller.proto)

建议一次请求的有界条件：

1. 使用当前运行 M1 AVD 的既有受信本地 discovery 与认证；只连 `127.0.0.1` 的已登记 emulator gRPC。读取现有 token 仅供 metadata，不能打印、报告或提交。
2. `getScreenshot(ImageFormat(format=RGBA8888, display=0, width=0, height=0), timeout=2s)`；不调用 stream，不调用旋转、显示配置或输入 API。
3. 返回尺寸需有界，完整 RGBA 必须 `len==width×height×4`；记录非秘密尺寸、orientation、采样时间和文件 hash。空图/未发布新帧明确标失败，不用旧帧冒充本轮结果。
4. 保留原始字节，不做翻转、裁剪或比例补偿后再比较。RGBA 本地导出仅是便于观看，需同时保留原始 hash/格式，避免把 PNG 转换器当源画面证据。
5. 重点比同页面静态标题/控件方向与系统栏，两个 API 采样时间不同，运动视频像素不一致不等于渲染差异。
6. 单帧捕获也会产生开销；放在性能计数窗口之外。它没有 UI 修改，但不能声称零 CPU/GPU 影响。

宿主 API 与 guest `screencap` 的入口不同，具有鉴别价值；两者仍可能共享源 buffer/合成/驱动，**不是完全独立物理显示观测**。

| 同场景结果 | 支持的下一步判断 | 仍不能证明 |
| --- | --- | --- |
| gRPC 正常、重复 adb 异常 | 优先查 guest 截图/离屏合成/编码读回分支，勿先改播放器 renderer | 具体驱动调用或零影响的修复已经找到 |
| 两者均局部异常 | 优先查源 App layer、共享合成或 renderer；保留历史 HWUI 关联作为单因素候选 | 宿主窗口的物理显示一定异常，或 GL/30 Hz 已是因果根因 |
| 两者均正常、远端才异常 | 查 encoder 输入、尺寸/stride/transform 与客户端显示路径 | 用宿主截图替代手机端真实呈现验收 |
| 任一 API 空图/超时/尺寸不符 | 保留失败分类，等待真实有效帧后再比较 | 把空图/旧帧解释为播放器已经恢复 |

`screencap` 上游先调用 `ScreenshotClient::captureDisplay`，PNG 与 raw 都取同一捕获 buffer；PNG 会用真实 stride 压缩，raw 按 stride 输出每行。因而 PNG/raw 只能进一步缩小编码/读回问题，不能作为两个独立合成后端的 A/B。若后续需要 raw，须按本镜像实际格式读取头部；当前上游包含 w/h/format/colorspace 四个 uint32，不能盲用旧 12 字节头。[screencap 上游](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/screencap/screencap.cpp)

## 下一层单因素候选与停止边界

先完成上述一帧比较与不 reset 的 `gfxinfo` Pipeline 读回。保持手机稳定版焦点、现有 session、源 App 前台内容与 RenderEngine 不动。

若两条捕获都异常，待该主机正式/实验会话全部自然空闲，才考虑恢复历史目标 **HWUI `skiavk`，只重开源播放器** 的受控复验：记录原属性、源 PID、boot ID、实际 Pipeline、内容/格式/页面位置和两个捕获结果；失败可按原属性回滚并在允许的空闲窗口重开同一播放器。不混刷新率、分辨率、码率或 RenderEngine 变更。仅恢复属性、不重启播放器的形式可以预置下一次初始化目标，但当轮不能当布局修复验收。

独立新 App 进程可测试同一 HWUI API 的 GL/Vulkan 绘制能力，但并不能在不动当前 YouTube UI 的条件下复制其特定 layer/视频 Surface 问题。前台展示测试 Activity 会改 focus 与源供给；离屏 fixture 又少了实际合成路径。当前不构建、不安装、不运行这种替代品，不把它当同 UI 因果比较。

只有捕获路径/源 layer 证据明确指向 SurfaceFlinger RenderEngine 才值得设计后端候选；那涉及启动期选择与 guest/SF 生命周期，必须另行空闲保护、备份、回滚和实际镜像能力检查。当前没有支持显式覆盖 `debug.renderengine.backend` 的因果证据，保留官方默认值是范围最小的选择。

本 checkpoint 仅完成 source/evidence/doc 层，不包含新截图 API 实测、播放器重开、硬件呈现、手机真实远程、物理/声学延时或性能验收。
