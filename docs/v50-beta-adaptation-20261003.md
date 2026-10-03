# V50 测试客户端适配与 UDP 后续方向

2026-10-03。用户要求火锅使用测试分支，目标是真我 V50；随后明确 UDP 的实际体验显著优于旧 TCP，停止 TCP 媒体性能研究。此记录区分客户端准备、运行网关与真实 V50 验收。

## 已完成客户端与源码

候选 **1.31-alpha.8 / code39**，独立原签名实验包，未发布。APK SHA256 `c9dd06294e48855caf6a81c7d13190cfe37c76c071cd8e9c5d8045e3987a0de1`，8,369,953字节；原签名 SHA256 `0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`。

- 新增“真我 V50 · 一键均衡优化”：540P（标准竖屏540×960）、4 Mbps VBR、30 FPS上限、80ms缓冲。关闭额外阶段直方图诊断；认证、完整性、防重放和取消机制保留。该预设是保守起点，不是已测最佳值，也不把VBR描述为尚未实现的自适应码率。
- FPS菜单保留旧索引0=60、1=120；30新增为2。有效保存值优先于新默认，手动改后不会在重开时被恢复为预设。
- 新安装 MediaTek／Dimensity 设备默认低负载；显式V50型号文本也可提示。未知RMX号不直接认定市场型号；用户始终可手动按按钮。
- 服务端源码只接受精确整数30/60/120。30返回`display_hz=0`，客户端柔性请求至少60Hz；不强制设备有120Hz模式，不改全局Hz。60/120原契约保留。窗口或Surface请求不等于实测刷新率。
- “查看本机硬解能力”读取硬件标志及540×960@30/60的尺寸帧率声明，分别显示AVC/HEVC。当前媒体仍H.264，实际配置与解码器仍由连接报告确认。查询异步回调检查Activity生命周期和登录页代际，不向已关闭或已进入媒体的页面弹旧结果。

## 验证范围

最终1252项unittest全通过，assemble/lint通过。最初沙箱阻止测试自己的监听器／子进程，未算通过；解除限制后的中间轮遇到版本断言运行中更新，保留为无效快照，冻结后最终完整轮通过。没有修改正式服务以通过测试。

实际已授权限频一加12（PJD110）安装精确候选，执行真实按钮点击：预设各字段读回通过，手动60FPS后Activity退出／重开仍保留，原设置恢复；能力查询报告高通AVC/HEVC厂商声明。未读取密码、未启动媒体；没有用这轮证明MTK性能。辅助包和手机临时安装文件已清理，原签名、用户数据、密码、CPU限制与刷新设置保留。结构化证据见[evidence](evidence/v50-beta-adaptation-20261003/checkpoint.json)。

后端真实worker及HostHardwareSession的假句柄fixture证明30同时进入`max_fps`与`raw_submit_fps`，错读回60被拒绝；原生Swift支持1…120并把实际fps用于VT ExpectedFrameRate和duration。不需要改编码器源码，但这不是实际苹果编码30FPS验收。

## 官方资料与能力边界

本轮通过浏览器重读[真我官方寄修列表](https://www.realme.com/cn/support/repair-personal)，确有V50/V50s；[MediaTek 6100+规格](https://www.mediatek.com/products/smartphones/mediatek-dimensity-6100plus)列2×A76、6×A55、Mali-G57 MC2及最高120Hz显示，但没有提供本机AVC/HEVC解码验证，不能由此保证V50的HEVC或60FPS持续播放。

[Android hardware属性说明](https://developer.android.com/reference/android/media/MediaCodecInfo#isHardwareAccelerated())明确它由厂商提供，不保证可验证正确性；[尺寸帧率查询](https://developer.android.com/reference/android/media/MediaCodecInfo.VideoCapabilities#areSizeAndRateSupported(int,int,double))是支持声明。因此V50仍需其真实码流解码、稳定帧间隔、热状态和音画报告。

## 当前未发布原因与下一步

公开最新版仍alpha7/code38，下载清单未修改。两台运行owner网关仍冻结a776828，只接受60/120。必须先安全部署30FPS兼容并验证真实会话，不能先发布让新按钮向旧网关发失败请求。

本轮M5只读检查：当前owner UID502，qemu、crashpad和netsimd都以同UID运行；默认AVD配置为720×1280/density320，配置条目8核/8192MiB/120Hz（只是配置读回，不认定运行CPU生效）。原状态未改。独立用户或宿主／LAN隔离负向验收没有完成；`huoguo`账号归属不是macOS文件或网络边界。

AGENTS明确：**“Friend/public release still requires host and LAN isolation acceptance.”** 因此此次客户端准备不等于给火锅开放新版非隔离入口。性能继续在M1已授权机主环境推进；M5原日常服务保留，候选隔离独立完成。无新朋友／公众上线，无NPS重启、任务或国内来源规则改变。

下一轮先完成有界M1真实30媒体与保护owner会话的网关交接，再沿UDP的接收突发、Inbox参考链、codec输入和Surface输出持有定位长尾。固定真实内容与格式，单因素比较50/80ms，不先扩大队列或取消guard。软件target差不当物理触控／声学延时。停止TCP性能优化和宽矩阵，保留现有兼容入口维护与无关NPC运维。
