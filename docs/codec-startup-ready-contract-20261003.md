# 显式 codec-ready 启动候选，2026-10-03

本候选只进入独立实验 App `1.31-alpha.5/code36`，默认关闭，勾选只作用于本次连接且不保存。正式 `v1.30` 的媒体、默认值和发布渠道不变。M1 使用原 UID501 机主环境，未经宿主隔离；M5 火锅日常服务不参与此实验。

## 要辨别的因素

alpha4 两次真实连接中，codec configure 112.729/220.589 ms 完成前各有两次四帧 Inbox 溢出。准备 decoder 时持续让 P 帧进入 FIFO，可能使启动恢复链过期。它不证明稳态 vendor codec 槽阻塞，也不能解释之前所有长空档。

显式 ON 顺序为 `WAIT_BOOTSTRAP → PREPARING → WAIT_FRESH_IDR → CHAIN_PENDING → STREAMING`。第一个未过期的完整 config+IDR 只提供实际 geometry，调用原 configure；其旧 AU 不送 codec、不进入视频播放时钟锚点。准备期间不开放 P 链。ready 后等待新的完整 config+IDR：原 received 时间不改、仍须小于80 ms，PTS 大于 bootstrap，geometry 匹配，generation/epoch 有效。只有 config 与 fresh AU 均提交成功，才沿既有依赖规则开放链。初始化不扩大稳态4帧／2 MiB，也不修改参考恢复、音频或共享 clock。

bootstrap 等待上限5秒，prepare 截止从选中 bootstrap 原到达起2.5秒，ready 后 fresh IDR 上限2.5秒。认证 KEYFRAME 最多三次，使用原 RX nonce sender；主机已有500 ms限频，可能使首帧等待变长，因此不预先把该候选当性能改进。`requests_reserved` 是请求预算预留；实际发出看 `keyframe_feedback_requests`，均不等于已收到 IDR。

## 源码与离线检查

Gate不持有 codec、socket 或帧字节。Inbox monitor内原子核对fresh选择、epoch与成功提交；configure与send不持该锁。取消和旧 generation不能复活链；旧 IDR送config后AU失败仍回到等待fresh，溢出后的新完整IDR重新选择身份。OFF保留原close后drain IDR→P规则，不受enabled Gate限制。

真实纯Java状态机及实际提取Inbox fixtures覆盖默认OFF、准备期间突发、年龄/geometry/PTS、失败/截止、epoch替换、取消/旧generation、重连和close-drain。实际parseSession fixture另核当前App端口与LanUdpContract及服务端默认一致。这些不使用codec、UDP socket或真实Android线程，不是手机时延验收。

当前独立入口 `45560/TCP` 仅用于受信HTTPS认证与撤销，实时媒体/触控为 `45963/UDP`。历史探针和冻结报告的旧端口不重写。首次alpha5尝试中Probe仍检查旧App端口15963，手机拒绝描述，host READY0/native未启动；失败证据保留在 `docs/evidence/codec-startup-ready-20261003`，不能作为OFF性能样本。修复后APK、matching helper和源码重新钉定，新轮证据在 `docs/evidence/codec-startup-ready-followup-20261003`。

## 验收与边界

App数值摘要 actual开关是0/1；ON结束后要求`phase_before_close=5`、failure0、`0 < ready ≤ fresh_received ≤ committed`及freshPTS>bootstrapPTS。OFF要求disabled且没有新启动commit。这是实际Gate执行读回，不只回显请求。

同APK off/on/on/off，固定1080竖屏、4 Mbit/s VBR、60cap、80 ms、lead0、PCM queue OFF但AAC启用、stage diagnostics ON及native政策。每轮两次正常HTTPS认证和App离开／重连。源为蓝M YouTube公开BBB，逐轮前后读取实际itag299/avc1/1920×1080@60；源位置及手机CPU上限／温度只是观察，未锁定。

首次configure／ready到fresh／commit、启动overflow和waiting-IDR、稳态独立SurfaceFlinger节拍／长空档分开报告。numeric摘要没有精确首input或首callback时，不补造该数据。codec callback回显target不当呈现；SF时钟与phone System.nanoTime未验证，相关事件只能作为候选关联，不作因果或光学时延。有限poll的未知尾部分列。同家Wi-Fi Tailnet内层UDP不是异地公网、V50、P2P逐包、声学音画或物理触控验收。

准备期间正常取消的线程join／vendor调用上界，以及音频可能早于fresh视频的启动同步，另外进行有界真机验收；本候选不声称已经解决音画不同步。
