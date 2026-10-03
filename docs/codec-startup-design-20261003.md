# Codec 启动准入设计 checkpoint，2026-10-03

本轮只做源审查，没有改 codec 源码、APK、手机或媒体服务。真实 alpha4 两次连接的 configure 用时112.729/220.589ms，均超过完整AU原到达后80ms期限；四帧 Inbox 两次溢出发生在 configure 完成前。该顺序解释了初始化准入风险，但不是稳态 vendor codec 槽阻塞的结论。

当前 `UdpVideoProbe.java` 第147–159行一收到完整config+IDR便允许后续P帧排队；第711–717行先更新共享播放clock，再同步configure；第762行仍按原到达时间保留80ms截止。`MainActivity.java` 第197–208行可能等待Surface并建立codec。不能通过“configure后把旧receivedNs改成now”、允许过期IDR或扩大Inbox来绕开该期限。

范围最小的机主显式候选是仅启动时 `WAIT_BOOTSTRAP → PREPARING → WAIT_FRESH_IDR → STREAMING`：完整且当时未过期的config+IDR只用于获知真实geometry并建立decoder，**其旧AU从不送codec、不作为播放clock视频锚**；准备期间不开放P链，所有舍弃单独计数。decoder ready后请求新的完整config+IDR，只有原到达年龄小于80ms、匹配准备geometry且generation/epoch有效的新IDR成功送入codec，才沿用现有依赖恢复规则开放后续链。稳态4帧/2MiB/80ms、原参考依赖、认证、取消和clock策略不变；重连从新runner重走有界启动。

现有主机 `udp_lan_worker.py` 第203–204、237–245行已处理认证KEYFRAME，但限频500ms且不保证请求即有IDR；packetizer第139行给每个IDR带缓存config。所以client-only鉴别可以复用已有KEYFRAME，不需要改NPS。必须在RX线程消费一次decoder-ready信号，记录/有界重开请求预算，避免configure前已经用完请求机会；没有新IDR则明确失败，不继续送P或退TCP。单靠“ready时丢P、再播放原IDR”不安全：丢掉的P可能已被后续P引用。当前HTTPS描述只给max_size，没有真实geometry，不能先猜分辨率、在READY之前直接configure。

第一候选需fixture覆盖：默认流程不变；P/configless-IDR拒绝；prepare耗时超过80ms旧AU不入codec；准备期间收到多P不溢出既有FIFO；fresh IDR原始receivedNs/PTS保留；迟到IDR和不匹配geometry拒绝；configure失败/取消/旧generation不能开放新链；送config后AU失败仍等待IDR；fresh IDR与并发P到达的epoch原子性；先后两次连接状态独立；有界IDR重试无无限循环。真实比较分别记录auth→首包、bootstrap→configure ready、ready→fresh IDR、fresh IDR→input/output、首帧与重连呈现、startup/steady溢出和waiting-IDR，不把codec callback target回显当物理呈现。若client-only等待新IDR的500ms限频成为主导，再研究显式codec-ready协议与host暂停/新IDR协作，不能提前认定该方案必然缩短首帧。
