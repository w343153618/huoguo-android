# M1 真实视频 Mapping 诊断，2026-10-03

新诊断已在真实一加12上读回，本轮没有发生 native mapping 拒绝，因此不能把历史 mapping 丢弃归因到 cap8，也没有证据支持扩大 mapping 容量。首会话独立 SurfaceFlinger 观测接近60FPS；两次连接均暴露了初始化期间四帧 Inbox 溢出和等待下一 IDR 的问题。下一轮应优先鉴别初始化准入和 decoder ready 的顺序，保留稳态队列策略，不以增加缓冲掩盖启动问题。

## 实验范围和实际参数

原 UID501 M1 机主非隔离环境，Android17物理1080×1920；已登记一加12，保留原用户数据、签名和动态 CPU 限制。路径为同家 Wi-Fi、已登记 Tailnet 内层认证 UDP。HTTPS仅用于现有账号认证和会话描述；没有改账号、正式 M1/M5 服务、NPC、NPS或国内出口。本轮不是公网移动网络、异地 V50、朋友安全隔离或光学/声学验收。

先前 `wyw` 认证尝试因这个 M1 运行环境没有该账号，在安装和媒体启动前结束，私有失败记录保留；随后使用用户已明确授权的现有 `huoguo` 账号完成本轮，未创建临时账号或修改账号权限。这不是一次被省略的媒体失败样本。

真实源为蓝色 Morphe YouTube 的公共 BBB 视频 `aqz-KE-bpKQ`。前后 Stats for nerds 均读到 itag299、avc1、1920×1080@60，位置由1:34推进到2:25，播放器 dropped frames 均为0，total frames 262→3286。源视频的横向尺寸与竖屏虚拟显示器尺寸不同；没有通过内容哈希证明每个 SF 呈现都是独立视频内容帧。

串流读回1080×1920、4,000,000bit/s请求码率、60FPS上限、80ms缓冲、lead0、PCM队列关闭。socket pacer wait 保持开启，组装期限80ms、mapping容量8，Java Inbox维持4帧/2MiB。原生发送器 wire pacing 上限为32,000,000bit/s；这个上限不是4M视频请求码率，也不是本轮平均网络吞吐。

alpha4/code35 APK SHA `8c00a1660ccef0e84b11c98c16091fd630c5980d29704e069562b92a04c94351`，实际安装后读回匹配；JNI SHA `578347ca24008ce7b987c77f2f1356c56761ac3d9ee4e7be89b8e9130476e402`，匹配 helper SHA `35c92b41c9da9069591b70fb34ca583ae453abc2457b4e06ef1f55e47c88b220`。packetizer SHA `567231ae8165bd09c46e343f436db647210e70d7f60d74a144950c8ca84c8142`，实际两轮 final 均声明 `host_clock_gettime_CLOCK_UPTIME_RAW_us`。完整公开数值见[汇总 JSON](native-mapping-observation-real-video-20261003.json)，接口解释见[Mapping 契约](native-mapping-details-contract-20261003.md)。

## Mapping 的实际覆盖

native 默认关闭，新 Probe 显式启用，两个会话均实际读回 status1/enabled1，读取366/64次、失败0。旧计数和新分原因总数均为0；old-frame、cap8、header-mismatch三类拒绝全部0，未观测拒绝0。最大同时活跃 mapping 为1，新准入2159/270，事件总数、保留数、淘汰数、清理数均为0。coverage mask15只表示 hook 支持，并不代表三类异常已经触发或验收。本次没有异常事件可以与 SF 空档做因果关联。

JNI及数值校验总时间为74.392/3.579ms，单次最大2.309/0.337ms，平均0.203/0.056ms。这是两次真实并发会话内该读取步骤的计时，包含首次和最终读取；没有同条件开/关对照，不能据此声明完整采样开销已验收或无性能影响。

## 独立呈现与启动丢弃

| 指标 | 首会话稳态采样 | 短重连会话 |
| --- | ---: | ---: |
| 独立手机 SF cadence | 59.899FPS | 没有独立 SF 稳态采样 |
| 手机 SF 按请求窗口计数 | 1766/30.004s，58.859FPS | — |
| 手机 SF p99/最大间隔 | 24.868/66.314ms | — |
| 手机 SF >100ms空档 | 0 | — |
| App received frames | 2158 | 269 |
| App callback records | 2111 | 165 |
| decoder configure wall | 220.589ms | 112.729ms |
| Inbox overflow/清理帧 | 2次/8帧 | 2次/8帧 |
| waiting-IDR丢弃 | 33帧 | 92帧 |
| 初始 take 最大间隔 | 704.183ms | 1645.909ms |

首会话 source SF cadence59.932FPS，按30.007s窗口计数58.553FPS，最大间隔56.399ms，>100ms空档0。手机45次、源50次轮询各自保持所选 layer 的重叠链完整，无采样无效项；请求窗口末尾仍分别有354.107/598.876ms未观察范围，不能保证整个窗口每一瞬间或更长使用过程。SF与 App `System.nanoTime` 的时钟域尚未独立验证，本报告不做跨域时间戳相减或端到端延时推算。

两会话全部 callback 时间戳精确回显请求 target（2111/165），现有因果校验判为不能估计呈现时间，因此表中的真实显示证据来自独立 SF，不能拿 callback 计数冒充显示FPS。App 内 target−release 的最大值272.961/244.223ms也只是未来提交目标跨度，不是物理延时。

手机内部同一时钟的事件顺序显示：两次各两个 Inbox overflow 都在首次 configure 完成前，且全部位于初始第一秒段。之后配置帧已超过原到达80ms期限、epoch也已变化；初始 reserve guard flags11（age+stale+config），被现有 timeout 分类，polls0。实际 codec reserve 最大8.354/6.011ms。这是配置前后准入与恢复链的具体线索，不支持“初始化 timeout 就是 vendor 输入槽阻塞”的结论。首会话后续稳态没有新增 overflow，但这不能解释历史174/232ms长空档，也不能证明启动和重连已经顺滑。

CPU上限前后读回 `[1689600,1612800,960000,1017600]`→`[672000,960000,960000,902400]` kHz，中间观察也有变化；源位置、热状态没有锁定。这是单轮观察，不是受控性能改善对照。

## 音频、收尾和下一步

新增音频原因也已在真实报告出现：两会话 worker late 为13/22，其中 arrival-age 为5/6、codec-input-unavailable 为8/16；PCM target-late 为1/0。没有声学测量或同步开/关对照，不能把这些软件计数当成耳朵听到的延时或同步结论。

正常 App 离开和重认证重连完成，两次 native 自然退出0，未使用 TERM/KILL，host final 齐全，gateway退出0，短期 helper和一次性凭据输入已移除，候选端口关闭，M1正式服务保持可用。没有触控、多指或旋转验收。本轮总耗时86.695s。

下一轮先在独立候选中分离一次初始化因素：鉴别是否能在接收完整配置与 IDR 后先建立 codec ready，再允许连续视频进入现有四帧链；保持认证、80ms稳态年龄限制、取消收尾和参考依赖完整性。应分别测首次呈现与稳态 SF、重连等待、启动 overflow、waiting-IDR丢弃和 config 失败边界。当前不扩大 cap8、不增加稳态 Inbox容量、不改正式默认值，也不以本轮近60FPS推出公网/V50或正式 UDP 完成。
