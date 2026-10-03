# Codec-ready 启动真实视频轮与发送失速，2026-10-03

这一轮找到了一个具体的会话中断点：**M1 的 UDP send 返回 Darwin ENOBUFS（errno55），旧 sender 将它当作 fatal error，撤销了整段媒体**。手机随后13.019秒没有新 worker frame 或 codec callback。这一次长停顿不能归因给手机 decoder 槽或 Inbox容量；宿主日志完整保留了最先观察到的 send 错误、角色和errno。不过它只解释这个被撤销的会话，不解释之前全部掉帧。

另一个结果是显式 codec-ready 新顺序实际执行成功。两次 ON首连及两次ON重连均完成 ready→新完整IDR→成功commit，默认保持OFF，因为稳态仍有明显长空档，本轮没有证明整体性能改善。正式App仍v1.30，M5日常15558和已完成的NPS QUIC维护没有在本实验中改动。

## 源与条件

机主M1原UID501非隔离环境，限频一加12；同家Wi-Fi、已登记Tailnet内层认证UDP，受信HTTPS仅认证/撤销。候选高端口45560/45963，媒体没有退TCP。源为蓝M YouTube公开BBB `aqz-KE-bpKQ`；前两完成媒体轮逐轮前后实际itag299、avc1、1920×1080@60均核对，第三失败轮仅有前读回，没有强补后读回。

同alpha5/code36 APK，1080×1920竖屏串流、4M VBR、60cap、80ms、lead0、PCM queue OFF但AAC启用，wait/guard与4帧/2MiB稳态策略保持。手机CPU上限、源播放位置与温度持续观察而未锁定；不能称受控ABBA。原计划OFF/ON/ON/OFF，第三轮健康门槛失败即结束，第四轮未执行。

初次尝试在媒体之前失败：UI/gateway已用高端口45963，但Probe仍检查旧15963。手机拒绝认证描述，host READY0/native未启动。已修实际parser并增加联合App/gateway契约fixture；原失败证据保留，**不作为OFF性能样本**。

## 实际结果

| 轮次 | startup | 初始2秒诊断带 overflow / timeout | 独立手机SF cadence | 完整请求窗口帧数/时长FPS | 最大已观察空档 | 后果 |
|---|---|---:|---:|---:|---:|---|
| 1 | OFF | 2 / 1 | 59.013 | 57.693 | 232.068 ms | 正常离开、重新认证 |
| 2 | ON | 0 / 0 | 49.879 | 48.189 | 1931.246 ms | startup成功，稳态仍差 |
| 3 | ON | 0 / 0 | 56.802（活动前缀） | 34.159 | 795.759 ms（活动前缀） | host ENOBUFS撤销；之后长时间无新帧 |

初始2秒带只是固定诊断分段，不能当全部configure窗口或严格稳态分界。第1/2/3源SF cadence分别59.931/59.835/59.633，精确数值与覆盖见JSON；源没有手机同幅度停顿。第三轮SF活动前缀56.802不能掩盖整个30秒窗口34.159，更不能把末端无新帧当作有稳定60FPS。

手机连续poll尚有约0.307–0.818秒未知尾部，具体未知范围记录在JSON；最大已观察gap不包括无法量出的末端停顿。codec callback全部回显请求target，不能当真实呈现。SF与phone System.nanoTime的等价关系未验证，事件同窗只能作为候选关联，不能证明物理延时或音画同步。

ON的初连prepare→ready分别237.193/194.574ms，ready→fresh分别107.802/187.995ms，fresh→commit17.609/18.791ms；重连也分别进入STREAMING，无gate failure。原始bootstrapAU未进codec，准备期间分别明确丢20/17个连续帧。两轮ON初始诊断带无overflow/timeout是启动层的迹象，但不是稳态改善结论。

OFF全程6次Inbox溢出，其中初始带2次；ON轮2全程3次溢出与FEC参考恢复，仍有1.931秒空档。CPU/source等因素未锁定，不能把ON和OFF差异简单归因startup开关。轮3全程Inbox溢出0/timeout0，但宿主send异常导致源供给提前终止；没有证据支持扩大Inbox来解决此次终止。手机侧另外有FEC expiry/reference loss各3、dependency drop37和mapping容量拒绝8，这些不能由一次sender fatal一笔解释。手机首末呈现跨度18.028秒相对30.007秒窗口少11.979秒；这是完整窗口缺帧边界，不是光学冻结时长。

## 宿主错误与完整收尾

轮3首会话明确记录 `failure_role=udp_video`、`failure_operation=udp_socket_send`、`failure_class=OSError`、`errno=55`，错误ring未淘汰；formal busy_seen0。native输出1340/源1340，elapsed22.861秒，无预算或依赖丢，然后以EOF自然退出0；手机对应首会话持续约37.6秒，中途worker/callback计数停在1273/1269，最大无进度13.019秒。native exit0是错误后正常收尾，不能称整会话无错误。

[Apple官方send(2)说明](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/send.2.html)指出ENOBUFS可来自内部buffer不可分配或接口输出队列满，可能随buffer释放而恢复。此处无法仅由errno区分Tailnet队列、系统buffer或物理链路；能够确定的是旧应用处理把一次该错误升级成了会话撤销。下一轮只鉴别原deadline内有界处理，避免掩盖长期故障。

六个host会话final齐全，自然exit0，无TERM/KILL；完整feed没有解析错误，gateway0且quiescent，助手/一次性输入清理，高端口关闭，源pins在结束时均匹配，CPU限制未写。首次正式健康读回的M5 URLError来自误用M1-only证书；随后用App实际M5证书读回200，不作为M5服务失败。

## 产物与下一步

alpha5重新构建和lint通过，982个全仓unittest通过，正式默认构建也确认没有UDP JNI、Gate或UDP UI类。APK SHA `f5cf8bbf69f9e7fe1f3babb78700abc6f254ce41b71c75f324765d9fa945da85`，JNI `578347ca24008ce7b987c77f2f1356c56761ac3d9ee4e7be89b8e9130476e402`，matching helper `d537af1660b2162eb168a402222c6c1e410e1cf042be2bdc8728ad69c96ac3cc`。原签名保留，手机保留独立alpha5；正式APK/更新渠道不变。

继续的最小因素是 owned nonblocking sender 的ENOBUFS处理：仅明确opt-in时在原视频期限/priority10ms内限次退让，保持新nonce、认证、取消与参考guard；不能无限重试、延长80ms、吞掉其他errno或默退TCP。先实际sender fixture，再同APK/相同真实源进行真机观察；若真实轮没再遇到ENOBUFS，也只能证明持续运行，不得把未触发分支说成真实拥塞恢复验收。

精确首input/callback尚未导出、取消duringprepare的线程上界、声学音画、多指/物理延时、公网/V50/外层逐包路径仍独立验收。公开数值见[JSON](codec-startup-ready-real-video-20261003.json)，候选契约见[说明](codec-startup-ready-contract-20261003.md)。
