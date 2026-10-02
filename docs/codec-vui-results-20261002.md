# 2026-10-02：CONFIG-only SPS/VUI 手机解码机制对照

本轮支持缺少 SPS/VUI bitstream restriction 可在这部手机上引起额外 decoder holding 的组件机制：相同 media bytes/flags/PTS 下，input→ready 中位数从约90ms降到约23ms，差值约4个60FPS帧间隔，未补充 immediate 的周期长空档也消失。这里的 input→ready 包括codec缓冲、线程调度及输出被观察时间，不是纯硬件引擎耗时。

**这不是现行 UDP 的新修复。** 现有 `run_phone_udp.py` 已显式传 `sps_low_delay=True`；`hardware_stream.py` 只对 CONFIG 调用 `patch_apple_baseline_config`，media AU/PTS/flags原样转发。最初 codec-file fixture 直接来自 native encoder，绕过了这层，所以未patch对照不能当现行UDP性能。

**没有由此证明真实UDP稳定60FPS、80/100ms最终最优、音画同步或V50验收。** 本复核只读已有数据和两份私有合成 fixture，没有操作设备或修改运行代码。

## 输入与接受门槛

720×1280 synthetic testsrc2，120个unique media AU重复10周期，共1200帧/20秒，source PTS约60FPS。两组各8轮，顺序均为 scheduled80/immediate80/immediate80/scheduled80/scheduled80/scheduled100/scheduled100/scheduled80；全部base先执行，随后全部VUI。固定同一probe、同一hardware decoder、实际panel开始/结束120Hz，arrival clock且decoderReanchor=false。16轮全部包名/设置/完整运行回读通过，未见records/pending eviction或unmatched callback。

原/patch完整fixture SHA不同；独立读取私有fixture并核对完整hash、geometry header、每个media record的bytes/flags/PTS/顺序相同，只CONFIG payload变化。非SPS NAL相同由patch metadata记录，SPS profile66/level32/max_ref1保持；bitstream_restriction 0→1，max_num_reorder_frames=0、max_dec_frame_buffering=1，level隐含maxDPB=5。

- 原fixture SHA256：`5df0c3a9b44119258332055b7835892d4449f0fd5957687f059766f4fb26c1be`
- 补充fixture SHA256：`0f2ab4ea6be161abe752455a6f41a96497b07ec7f72074ce8692b9aaf5b4c11e`
- 相同media records SHA256（flags+length+payload）：`ffd9e7f0e724b958b1211ff687f09f2e48dd95eab100f2c18886f6bb2ab34943`

独立SF统一以手机 `feed_start_ns+[2,18)` 入窗，16秒；每轮时间戳完整覆盖，排除启动和最终drain。timing按received落在同窗的保留callback记录，ready计数不等于全部硬件输出。窗口边界可使固定16秒SF rate略高于60，不代表源供给超过60。

## 逐轮结果

|组/轮|release/buffer|SF FPS|SF p99/max ms|>50/>100ms|input→ready p50/p99 ms|全程late discard|
|---|---|---:|---:|---:|---:|---:|
|base/01|scheduled/80|59.3125|33.147/41.437|0/0|87.742/95.088|5|
|base/02|immediate/80|58.4375|30.416/99.464|8/0|89.455/95.988|0|
|base/03|immediate/80|58.4375|30.419/91.181|8/0|89.879/95.022|0|
|base/04|scheduled/80|59.3125|33.152/41.441|0/0|89.851/95.345|11|
|base/05|scheduled/80|59.4375|29.089/49.726|0/0|89.878/95.243|3|
|base/06|scheduled/100|59.9375|24.864/33.151|0/0|89.765/95.457|0|
|base/07|scheduled/100|59.9375|24.865/33.153|0/0|89.995/96.314|0|
|base/08|scheduled/80|59.5000|24.866/41.442|0/0|90.117/94.810|3|
|vui/01|scheduled/80|60.0625|24.862/24.865|0/0|23.288/29.409|0|
|vui/02|immediate/80|60.0000|24.864/41.440|0/0|23.359/28.904|0|
|vui/03|immediate/80|59.8125|24.861/41.433|0/0|23.276/29.407|0|
|vui/04|scheduled/80|59.9375|24.861/24.863|0/0|23.254/29.783|0|
|vui/05|scheduled/80|60.0625|24.862/41.435|0/0|23.570/28.618|0|
|vui/06|scheduled/100|60.0000|24.862/41.430|0/0|22.616/28.419|0|
|vui/07|scheduled/100|60.0000|24.861/24.864|0/0|22.644/28.256|0|
|vui/08|scheduled/80|60.0625|24.862/24.867|0/0|23.602/29.481|0|


## 按条件合并

SF rate按帧数/总窗口时间；p99连接各窗口内部样本重新计算，没有平均单轮p99或连接跨窗口边界。

|组|release/buffer|轮数/秒|SF rate|合并SF p99/max ms|合并input→ready p50/p99 ms|>50/>100ms|
|---|---|---:|---:|---:|---:|---:|
|base|scheduled/80|4/64|59.3906|33.148/49.726|89.271/95.301|0/0|
|base|immediate/80|2/32|58.4375|33.149/99.464|89.737/95.692|16/0|
|base|scheduled/100|2/32|59.9375|24.864/33.153|89.861/95.862|0/0|
|vui|scheduled/80|4/64|60.0312|24.862/41.435|23.431/29.587|0/0|
|vui|immediate/80|2/32|59.9062|24.864/41.440|23.322/29.279|0/0|
|vui|scheduled/100|2/32|60.0000|24.862/41.430|22.624/28.369|0/0|


## 循环与释放的解释

base immediate两轮各8个>50ms SF空档，右端均位于2/4/6/8/10/12/14/16秒循环边界后约0.10–0.12秒；VUI immediate两轮为0。这里是已知2秒循环的时间相位特征，不是把SF帧按最近时间映射到IDR，不单独证明vendor内部DPB算法。差值约66ms与额外4帧缓冲一致，CONFIG-only、同media及重复结果使该机制解释较强，仍保留顺序、动态限频和热状态混杂。

base immediate的input→ready仍约90ms，不能把它主要归为80ms future-release保持。默认lead0在Java中ready后立即提交timestamp；timed Surface下游仍可能有缓冲/背压。本probe不含8/16ms lead、async UDP inbox或音频，不能直接定位那些路径。

VUI scheduled80四轮SF约59.94–60.06，scheduled100两轮60.00；全部>50ms gap=0，没有观察到100ms额外流畅度收益。target−receive中位数约79.86/99.83ms，目标约多20ms，既不是物理显示延迟，也不能当声学音画同步。VUI immediate把目标提前到约23.95ms，SF仍约59.81–60.00；它取消定时播放保护，不能据此替代真实UDP有抖动/音频的默认策略。

本组件结果继续支持80ms为折中推荐、100ms为真实链路流畅优先可选；没有改V50默认或用户保存参数。需要结合真实UDP供给与音频证据选择，不能用未patch baseline的100ms优势推荐现行UDP。

全程每轮1200 inputs，callback+late discard均1199；没有注入EOS且drain有界，剩余记录保留，不按网络丢帧或steady SF丢帧解释。vendor callback/target echo不能当display FPS；本报告display指标仅来自独立SF，并非光学、触控或声学测量。

完整数值、边界gap和循环相位见 [codec-analysis.json](evidence/overnight-20261002/codec-analysis.json)。CONFIG控制见 [fixture720-vui-patch.json](evidence/overnight-20261002/fixture720-vui-patch.json)；原fixture见 [fixture720-60.json](evidence/overnight-20261002/fixture720-60.json)。全部报告与采集编排在 [本轮目录](evidence/overnight-20261002/)。
