#!/usr/bin/env python3
"""Render the completed evidence as a local Chinese PDF and scrolling HTML.

Uses the Codex bundled Python (reportlab/Pillow/pdfplumber); no remote content,
screenshots, credentials or binary fixtures are included in the report.
"""
import datetime,html,json,math
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,KeepTogether
from reportlab.graphics.shapes import Drawing,Rect,String,Line

ROOT=Path(__file__).resolve().parents[2]
EV=ROOT/'docs/evidence/overnight-20261002'
OUT=ROOT/'output/pdf'
FONT='/Library/Fonts/Arial Unicode.ttf'

SECTIONS=[
('01','先回答最关心的结论',[
'这轮把目标拆开验证了：真实视频能否持续供帧、Mac 是否真正调用硬件编码、降频手机能否及时解码呈现、网络是否给旧帧制造额外等待。结果支持继续使用 Apple VideoToolbox + 低延时 H.264 + 有界 UDP 媒体管线；它在真实手机上已经跑通，但不能据此声称完成了公网、蜂窝和火锅 V50 的最终验收。',
'当前最值得保留的不是某个“最快协议”的名字，而是整条管线的约束：编码之前不积压旧截图，编码之后不无限重传旧视频，手机按统一时间线呈现，音频与触控不被视频队列堵住。下面列出所有有效和无效样本，不把一次接近 60FPS 的短测试包装成永远稳定。',
'120Hz 的屏幕、120FPS 的配置上限和 120 张不同内容帧是三个概念。这轮真实播放内容通常接近 60 帧供给，更高的提交预算用于避免额外排队，不会生成不存在的 120 帧视频。因此报告把“接近 60FPS”与“稳定 120FPS”分开判断。']),
('02','实验对象、路径和保留的设置',[
'本轮只操作与你对话的 M1。虚拟安卓为 emulator-5556 / RemoteAndroid17Compare，当前 Android 17 / SDK 37，6 核、16GiB 内存、物理 1080×1920、480dpi、host GPU、Skia Vulkan。M5 未参与测试或部署。物理 1080P 用于保留 App 布局；编码输出单独比较 540×960、720×1280、1080×1920，不把两类分辨率混为一谈。',
'真实手机为已 root 的一加 12 PJD110，USB 只用于控制、安装探针和读取数值；视频、AAC 音频和触控走家中网络：M1 有线 en7 → Wi-Fi → 手机。手机实际显示模式 120Hz，用户设置的 CPU 降频和温控限制保留。本轮未升频、未关闭限频器、未关闭温控。',
'降频一加 12 只能作为弱性能代理测试机。它的硬件视频解码器、显示管线、内存带宽、无线芯片和系统调度仍与真我 V50 不同，不能通过 CPU 频率接近就证明两台手机等效。V50 的参数建议需作为起点，最终仍应以她的 App 内采样为准。',
'真实源使用蓝色 M 的 Morphe YouTube 播放公开 Big Buck Bunny，启动请求 seek60 秒，用户此前启用可用 60 帧优先。每轮独立验证播放器前后处于 PLAYING，源视频 Surface 有足够时长的呈现进度。该检查证明真实视频在工作，不单独证明精确媒体位置、逐帧内容身份或所有时刻都为同一 60FPS 文件。']),
('03','怎样测，怎样避免“看上去稳定”的误判',[
'主指标来自持续采样的 SurfaceFlinger actual-present 时间戳，使用排序、去重后的有效呈现记录。35 秒会话的手机稳态窗口固定为自身首个服务器包后 [5,30) 秒；两分钟会话用 [5,110) 秒，并保留全采集窗口。报告同时保留触及窗口边界的间隔和覆盖状态，不只挑中间最好的几秒。',
'同一手机内的完整帧接收、codec input、decoder-ready 和 SF 可以使用同一单调时钟做阶段分析。Mac 与手机的独立时钟没有精确同步，所以本报告不跨设备相减求单向延迟。Mac 的 VT submit→callback 包含线程调度，手机 input→ready 包含排队和输出被观察时间，两者均不是纯硬件执行时长。',
'Qualcomm 部分 MediaCodec callback 时间回显请求的目标时间。请求“在80ms后显示”不是证明物理面板真的在80ms后发光。callback 帧数也不等于实际显示帧数；这轮使用独立 SF 时间线复核。SF 仍不是高速摄像机测量，不足以直接给出触控到出光或声学音画同步。',
'每轮保存请求设置、实际读回、App/probe/encoder/packetizer 哈希、手机接收时长、源端状态、阶段数值和失败原因。合成文件、真实视频 LAN、既有 NPS 公网、蜂窝和远程 V50 分层记录。退出码成功而播放器静止的测试也不能进入真实视频成绩。']),
('04','硬件加速已生效，为什么仍会卡',[
'Mac 串流编码器实际读回 using_hardware=true；手机使用 Qualcomm AVC 低延时硬件 decoder。它们负责不同阶段，不能消除源播放器供帧不均、gRPC 抓图等待、CPU 线程迟到、旧截图排队、网络整帧截止或 Surface 呈现等待。只看到“硬件解码”标签，不代表所有环节都由硬件实时完成。',
'前一轮已在没有串流抓屏、没有编码、没有手机媒体接收的源端测试中复现长呈现空档。源端供帧/呈现链本身已有空档，问题不能全部归因于NPS；解码、调度与期望呈现时序的具体份额尚未完全分解。当前保留模拟器 Interactive 调度候选，源端结果较此前有改善，但 Standard→Interactive→Standard 返回样本有准备和时序混杂，不应把全部改善归因于一个设置。',
'源 YouTube 解码与远程串流编码也是两件事。既有官方源码和本地 SDK 二进制核查说明：Apple 模拟器 H.264 后端允许 VideoToolbox，但存在软件回退；当前 Apple VP9 模拟器后端使用宿主 libvpx。guest 的 goldfish codec 名称不是实际硬解保证。没有读到活跃解码 session 的 UsingHardwareAcceleratedVideoDecoder，就不能称源视频端全部苹果硬解。',
'增加 CPU 核数、内存或强制刷新率不能补出缺失的源帧。6核/16GiB已按用户要求固定；这轮没有盲目继续增大。提高性能优先解决已观察到的等待和积压，而不是用配置数字替代因果证据。']),
('05','最明确的组件瓶颈：H.264 的解码缓冲声明',[
'这轮重新做了 16 次同一合成视频文件的手机对照。原文件与修改后的文件只有 codec CONFIG 的 SPS 不同；所有媒体帧的字节、PTS、flags 和顺序保持一致，独立哈希核查通过。场景没有模拟器抓屏、UDP 网络、音频或在线内容变化，所以可以隔离这个解码组件。',
'原 Apple Baseline SPS 未包含 bitstream restriction。补充零重排、一个解码参考缓冲限制后，输入到 decoder-ready 的中位时间由约88-90ms降至约23ms，约少四个60帧时间间隔。原 immediate 路径每个循环边界的停顿也消失。这个结果支持缺失低延时声明导致此码流/decoder组合的额外 holding，不代表观察到了内部 DPB 的全部工作。',
'16轮先运行全部原流，再运行全部VUI流；热状态、动态限频和顺序仍可能混杂。相同媒体字节、配置唯一变化与约四帧的时间差提供组件机制证据，不是直接测到了内部DPB分配。',
'现行 UDP 实验路线此前已启用这项 SPS 处理，本次不是又发现并修复了线上同一个漏洞。它的意义是排除一个错误诊断：未修改的合成流不能代表现在正常 UDP 流的解码延迟。若忽略编码配置差异，就会误把旧测试流的90ms holding归咎于当前手机或缓冲设置。',
'处理范围非常窄：只接受已验证的 Apple Baseline、progressive、单参考、POC0、无HRD配置；其他配置保持原样并报告不支持。不会随意修改媒体画面、参考链、未知 profile 或错误的现有重排参数。'], 'codec'),
('06','60 / 120 的第一组真实视频对照',[
'第一组采用 60→120→120→60 的顺序。720P、8Mbps、80ms、手机120Hz等条件保持；但“FPS上限”同时影响 Python 提交预算、native expected frame rate、关键帧间隔和手机设置，因此这只是配置组合对照，不能直接当作单一变量实验。',
'这组手机稳态约57.7-59.2FPS，各轮没有超过100ms的窗口内呈现间隔。第一轮60预算在某一小段中出现约30帧旧截图等待，FIFO最长约34ms，部分旧截图出队时已有更新截图等待。120预算保留赶上源供给的余量，所观察到的等待较短。',
'第四轮60预算并没有同样程度的FIFO积压，且120两轮也没有一致更高的手机FPS。这些反例必须保留：120预算减少某次等待不等于120条件永远更顺。随后新增独立 raw-submit-fps 参数，专门测试提交预算，避免把native编码器和手机FPS一并改掉。'], 'cap'),
('07','把提交预算独立出来，再做 ABBA',[
'精准对照固定 native--fps120 和手机fps_limit120，只切换 Python FrameRateBudget 的60/120值。gRPC截图请求不受这个参数限速。worker握手和手机读回必须一起通过；缺失、类型错误或数值不一致时，报告不允许把该轮标记为有效。',
'这个实验回答的是：接近60帧的源，在宿主短暂抖动以后，是否因为提交预算没有余量而积压旧截图。它不改变视频内容FPS，也不证明可以持续输出120张不同帧。原始截图可以在编码前采用严格年龄或最新帧策略；一旦编码成有依赖的P帧，就不能随意丢弃后仍继续喂坏参考链。',
'本次提交预算结果与第一组配置组合结果分别列出。不同批次 probe 哈希有版本区别，报告不跨批次拼接成一个完全相同的A/B。顺序热状态、背景负载和源播放变化依然限制因果强度；实际显示收益必须与FIFO等待一起看。'], 'budget'),
('08','分辨率：降低输出负担，但保留正常安卓布局',[
'分辨率与码率按实用组合正反测试：540×960/4Mbps、720×1280/8Mbps、1080×1920/12Mbps。guest物理尺寸始终1080×1920，不通过降低安卓布局尺寸来换取指标。该组合比较的是实际使用档位，尺寸与码率同时变化，不能据此单独算分辨率的因果效果。',
'选择较小的编码输出通常减少抓图、缩放、编码、数据传输与手机呈现的工作量，但如果瓶颈在源YouTube的解码/交付，单独降低串流尺寸也可能没有明显改善。结果表要同时看源供帧、手机接收和实际显示，不以编码器配置成功作为流畅性验收。',
'本轮另外补充同一720P、80ms、120提交预算下，目标码率4/8/12/12/8/4Mbps的对照，减少尺寸和码率同时变化的混杂。这里仍是目标码率：VBR实际产生量取决于内容和限制；100KB/80ms的VT短期限制及32Mbps含FEC/包头的socket预算分别读回，不能称12Mbps目标就实际持续传12Mbps。',
'1080P适合压力和画质验证。为火锅长期使用优先540P，720P作为清晰与性能的折中选项；不以本轮降频旗舰手机的1080P成绩保证V50有相同负载能力。540P、720P、1080P标签只是App的便利档位，“DVD/高清/蓝光”不是视频片源或解码格式的严格标准。'], 'resolution'),
('09','80ms 与100ms：用实际呈现决定取舍',[
'用户最新明确允许100ms。本轮不再把80ms当作不可改变的硬上限，仍避免回到已感到拖手的120ms。80/100/100/80真实UDP对照保持其它条件，随后每个条件再连续播放两分钟。判断要看长停顿、输入到输出的长尾和操作代价，而不是只看平均FPS。',
'真实对照第一轮80ms为53.24FPS、7次>100ms空档，返回80ms已恢复59.16FPS且窗口内>100ms为0。首轮源端完整窗口51.517FPS，其余三轮58.266-58.470FPS；这些不同步的窗口不能逐帧相减，但显示供给状态明显变化。本轮没有证明100ms独立产生稳定收益。',
'合成未修正SPS的原流中，100ms缓冲较80ms改善呈现，但SPS修正后两档都接近60FPS，100ms的优势不再明显。这说明缓冲能掩盖某些既有holding，却不能替代解码配置修正；不能根据旧合成流把100ms宣布为所有场景的最佳值。',
'推荐保留80ms为折中设置，100ms为流畅优先可选。100ms增加约20ms的目标等待，不代表整条远程延迟固定为100ms，也不能补齐源端没有产生的帧。用户上次保存的有效设置应保留，不因每次启动而自动改回默认。'], 'buffer-real'),
('10','长时间播放比一个漂亮瞬间更重要',[
'每个两分钟会话都启用真实源、音频、触控通道和持续数值采样。主稳态窗105秒，完整会话接收和SF覆盖另外保留；报告同时给平均呈现率、p99间隔、最大空档和超过100ms空档次数。短样本的零长停顿不能自动外推到一小时。',
'两轮原生事件环分别淘汰6082/6142条，完整固定窗的接收FPS因此未测；输入观测和独立SF仍完整。全程计数与事件保留范围分开记录，不能从保留的后半段零故障推全程无接收/FEC异常。此外原生编码器最终统计及Swift最终trace summary缺失，硬件ready回读不证明全程零drop或完整trace。',
'这些会话不是摄像机观测，也不是全国公网线路测试。若长会话不如短会话稳定，正确结论是持续性尚未解决，而不是删掉长会话或选择更高的cadence统计冒充实际整个窗口。提高视频输出或把buffer再加大，都不能保证重负载时自动恢复。'], 'long'),
('11','抗丢包：FEC 有帮助，但要承认边界',[
'UDP线路采用认证加密、小包分片、10+2 Reed-Solomon FEC、整帧截止和IDR恢复。手机只接收完整、及时且参考有效的帧，不把损坏帧硬送到MediaCodec。过期旧帧应停止占用当前播放预算，不能为了“所有包必达”无限重传。',
'本轮有限丢包在加密封包以后、实际socket发送之前，周期性丢弃每第100或50个视频数据报，分别约1%/2%；音频和触控不注入丢包。报告记录实际丢弃数、FEC恢复data shards、整帧过期、参考丢失和实际呈现。恢复的shard数量不是所有丢包被恢复的同义词，parity也会被丢弃。',
'周期丢包不是连续突发丢包、乱序、蜂窝切换、带宽竞争或路由拥塞。若该简单模式已有长停顿，就不能宣传“强抗丢包已完成”；即使通过，也只能说明这个受控模式。更好的公网方案必须有拥塞控制、及时反馈、码率回升和音频PLC/FEC，而非单纯把TCP换成UDP。'], 'loss'),
('12','声音、音画同步和原生触控的实际状态',[
'音频是独立AAC/AudioTrack工作线程，使用与视频共享的时间映射，媒体网络同样走UDP，不存在本轮音频TCP回退。记录到配置、解码输入、PCM写入和AudioTrack timestamp证明音频管线运行；它们不能证明耳朵听到的音画差已符合要求。当前音频没有FEC，音频迟到丢弃仍需在报告中保留。',
'不要仅因为视频配置80/100ms就给音频简单加同样固定sleep。后续建议以音频实际播放head/timestamp为参考，视频按共同源PTS排期并小幅修正漂移；还要考虑设备缓冲、视频解码和Surface排队。目前AudioTrack读回用于估计音频队尾和提交，视频deadline仍来自共享到达映射，尚未完成音频主时钟闭环或声学验收。精确验证需要同画面闪光/声音标记和外部录制，不能从未同步时钟拼出“零延迟”。',
'原生触控通道传输触点快照、tracking token、DOWN/UP确认和合并MOVE，在guest注入SOURCE_TOUCHSCREEN，不移动Mac鼠标。本轮最后一个会话演练一次手机OS滑动并保存实际注入计数、writer错误和结束活跃触点。单指演练不能代表十指、取消、丢包重连、键盘和用户手感全部通过。',
'音量有真实手机媒体音量、虚拟安卓媒体音量和编码/播放增益几个层次。之前“小声”不能只凭能听到就归咎于朋友没开最大；本轮没有响度计或声学对照，也没有随意放大到削波。先保持音画链路正确，再测源音量、PCM峰值/RMS、AudioTrack使用和手机媒体音量。']),
('13','云服务器、Clash 和为什么不能只看200M带宽',[
'这轮对腾讯云2号146.56.249.175做了8秒只读运行采样。2vCPU总体空闲约72.45%，未观察到steal、接口drop或足够理由停止其它业务。NPS约占单核23.52%，python3约10.08%；这些是该短窗口的读回，不代表高码率转发负载下的上限。',
'采样时15556没有活跃串流连接，因此这份云资源结果不能与LAN手机的FPS直接关联，也不能证明中转性能完全无瓶颈。所见TCP重传字段大多是累积历史计数，不能拿它们冒充本轮瞬时丢包率。云端200M能力不等于家庭上行、东北移动Wi-Fi、跨运营商路径和手机接收都能稳定用满。',
'当前既有M1物理relay到NPS的连接读回在en7，云端所见来源为国内电信出口。它证明这个既有socket的路径，不能承诺未来任何新增ICE/STUN/TURN/Tailscale公网socket都自动绕开Clash。新线路必须单独验证实际物理绑定、路由和云端来源；保留腾讯云国外来源过滤，不为测通而关闭它。',
'虚拟安卓访问YouTube的应用出口与M1到国内媒体中继的连接可分离。安卓内容访问允许借助已配置出口；媒体中继从国内物理接口直出。两条连接承担不同角色，不能因为安卓能访问国外就推断云看到外国来源，也不能用“显示国内IP”的标签替代实际socket验证。']),
('14','NPS、QUIC、KCP、Tailscale：应该如何取舍',[
'当前公网15556任务仍是TCP映射，正式App媒体仍走既有TLS/TCP。这轮加密UDP是真实实验路线，尚未接入正式登录、更新、自动重连和公网ICE。换NPC底层QUIC并不自动把App自己的TCP字节流变成允许丢弃过期帧的实时媒体协议。QUIC可靠stream仍需按序交付；DATAGRAM才具备不可靠消息语义。',
'KCP的快速ARQ在一些损失/RTT条件下可能有用，但单个可靠视频流也会为了旧数据等待。如果重传回来已超过80/100ms显示期限，更多可靠性反而可增加积压。为视频更合适的是有期限的恢复、FEC、关键帧保护和拥塞反馈；控制边沿可以可靠确认，MOVE保留最新，音频走短队列。',
'Tailscale提供加密与穿透，不会自动修复源端供帧或手机输出调度。直连WireGuard外层通常用UDP；若应用内层仍TCP，就仍有应用字节流的等待。回落DERP中继的路径也必须读回，不能把所有Tailscale连接称为UDP点对点。',
'本轮M1 Tailscale本地状态Running，当前替代一加12没有活跃tailnet IPv4。实验runner严格绑定物理LAN接口，不能把peer改成100.x就称为完成Tailnet UDP；内外层route需要独立守卫。因此本轮不伪造Tailscale、NPS P2P或手机蜂窝的UDP对比数据。']),
('15','最佳技术方向与建议配置',[
'工程方向采用原生触控 + Apple硬编 + 手机硬解 + 有界实时UDP。公网会话先做ICE/P2P直连，失败使用国内认证UDP中继，媒体不自动回退为可靠TCP。TCP/TLS可以用于登录、信令、下载和文件传输；实时视频、音频和高频触控采用独立期限策略。P2P不能保证在所有NAT中成功，国内UDP中继是必须具备的产品能力。',
'短期保留当前M1实验环境：6核/16GiB、1080P物理布局、hostGPU/skiavk、已核查低延时SPS和Apple硬编。为用户产品推荐540P/4Mbps/60FPS显示目标/自适应VBR/80ms起步；720P/8Mbps作为条件好的Wi-Fi选项。实验内部120提交预算用于余量，不应在界面承诺实际120FPS。100ms作为流畅优先选项，保存上次选择。',
'码率“自适应VBR”不是任意突发。应同时给峰值窗口、关键帧预算和socket pacing，遇到参考丢失可降码率并请求IDR，恢复后逐步回升。当前反馈是受限实验策略，不是完整GCC/成熟带宽估计器。本轮不会宣传CBR、AVBR、ABR任一名称能自动消除撕裂；必须说明硬件是否支持并读回接受值。',
'当前Android17镜像状态与Android16的版本差异不是本轮已完成的镜像A/B。没有证据证明换16就一定治好全部卡顿，也没有证据证明17必然更省CPU。保持现有实例和数据，后续如做镜像对照，应使用同源、同尺寸、同codec和同网络的独立实例。']),
('16','这轮改了什么，什么尚未产品化',[
'新建有界夜间测试编排器，保留每个失败窗口；补充独立提交预算覆盖及严格握手回读；扩展手机固定文件隔离测试的100ms、arrival clock、实际120Hz验证；生成原/修改SPS同媒体数据对照；持续保存SF、收包、解码、音频、FEC和实际运行设置。相关源码与数值证据均留在canonical工程目录。',
'正式App没有在本轮发布升级包，正式NPS映射、云过滤、Clash、账号和M5没有被替换。这是有真实手机数据的研发成果，不能告诉火锅“点更新后已经得到公网UDP”。实验包与正式包分开；既有参数、账号和虚拟机数据保留。',
'还未验收的具体事项是：真实外网UDP直连/中继及切换、蜂窝和东北V50持续播放、用户手势与多指/取消/重连、外部测量触控延迟、声学音画同步、音频丢包保护、正式App完整登录/更新/会话鉴权和长时间运行恢复。将其列为清晰的实现与验收缺口，比继续堆匿名协议名称更有价值。',
'结论仅覆盖报告中实际完成的层次。测试结果变好值得保留；没有达到的目标继续标记未达到。长报告与完整机器数值均可追溯到相应run目录，不依赖聊天里的口头描述。'])]

SECTIONS.append(('17','长停顿的身份链与两层节奏器对照',[
'最差短轮的356.335ms SF空档，对应手机完整AU frame299→303相隔395.533ms。中间IDR300原生只输出119/136分片，记录write_deadline；手机同frame300组装期限到期。301/302作为依赖帧未输出，303新IDR恢复。通过明确frame_id和PTS连接，避免只按附近时间猜测同一帧。',
'对应宿主序列有157.482ms gRPC返回空档，原始pipe等待83.240/108.858ms、FIFO等待62.770/66.902ms；VT执行观察仍十几毫秒。IDR300名义serialization38.689ms、native计划sleep8.357ms却实际79.193ms，Python同帧socket约79.755ms后截止。另一IDR477也出现同类期限失败。没有证据说明这是公网丢包。',
'反馈全65个区间目标仍8Mbps，下降/上升动作均0；native和socket wire budget均32Mbps。没有证据支持自适应误判降低发送预算，后续stdout blocked计数也为0。累积sleep/调度尾部耗尽80ms是直接观测，两层pacing耦合是待比较的机制候选，不能偷换成唯一原因。',
'追加double→nativeonly→nativeonly→double对照。native32Mbps和2048字节catch-up credit保留；nativeonly同时关闭第二层socket等待及SocketVideoGate guard，是组合实验，不是只改变一个sleep。只在物理LAN实验使用，正式服务和默认行为均不改变。结果没有重复优势就不自动推广到公网。'], 'pacing'))

def esc(s):return html.escape(str(s))
def value(v,digits=2):
    if v is None:return '未测'
    return f'{v:.{digits}f}' if isinstance(v,(int,float)) else str(v)

def load_rows():
    summary=json.loads((EV/'real-video-summary.json').read_text())
    return [{'label':r['run_label'],'report':r['report_path'],'valid':r['valid'],
             'fps':r.get('display_fps'),'window_s':r.get('window',{}).get('duration_s'),
             'max_gap':r.get('gap_max_ms'),'over100':r.get('gaps_over_100'),
             'buffer':r.get('buffer_ms'),'geometry':r.get('geometry'),'bitrate':r.get('target_bitrate_bps'),
             'receive_fps':r.get('receive_fps'),'input_ready_p50_ms':r.get('input_ready_p50_ms'),
             'input_ready_p99_ms':r.get('input_ready_p99_ms'),'validation':r.get('validation'),
             'source_unchanged':r.get('validation',{}).get('source_fingerprints_unchanged'),
             'full_surface':r.get('full_surface'),'audio':r.get('audio'),'native':r.get('native')}
            for r in summary['rows']]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=load_rows();codec=json.loads((EV/'codec-analysis.json').read_text())
    pdfmetrics.registerFont(TTFont('CN',FONT))
    ink=colors.HexColor('#192B36');teal=colors.HexColor('#167F83');muted=colors.HexColor('#52717C')
    styles={
        'body':ParagraphStyle('body',fontName='CN',fontSize=10.1,leading=17.6,textColor=ink,wordWrap='CJK',spaceAfter=10),
        'h1':ParagraphStyle('h1',fontName='CN',fontSize=20,leading=28,textColor=ink,spaceAfter=18,wordWrap='CJK'),
        'h2':ParagraphStyle('h2',fontName='CN',fontSize=13,leading=20,textColor=teal,spaceBefore=14,spaceAfter=10,wordWrap='CJK'),
        'small':ParagraphStyle('small',fontName='CN',fontSize=8.1,leading=13,textColor=muted,wordWrap='CJK',spaceAfter=8),
        'cell':ParagraphStyle('cell',fontName='CN',fontSize=8.4,leading=13,textColor=ink,wordWrap='CJK'),
        'title':ParagraphStyle('title',fontName='CN',fontSize=32,leading=44,textColor=ink,wordWrap='CJK',spaceAfter=26)}
    story=[];web=[]
    def para(t,kind='body'):
        formatted=esc(t).replace('\n','<br/>')
        story.append(Paragraph(formatted,styles[kind]));web.append(f'<p class="{kind}">{formatted}</p>')
    def table(headers,data,widths=None):
        allrows=[headers]+data
        cells=[[Paragraph(esc(v),styles['cell']) for v in row] for row in allrows]
        if widths is None:widths=[(A4[0]-94)/len(headers)]*len(headers)
        t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#D9EFF0')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F2F7F8')]),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),.8,teal)]))
        story.append(t);story.append(Spacer(1,12))
        web.append('<div class="table-wrap"><table>'+''.join('<tr>'+''.join(f'<{("th" if i==0 else "td")}>{esc(v)}</{("th" if i==0 else "td")}>' for v in row)+'</tr>' for i,row in enumerate(allrows))+'</table></div>')
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M CST')
    para('给火锅的安卓\n真实串流深度测试报告','title')
    para('M1 × 降频一加12  |  真实YouTube  |  原生加密UDP','h2')
    para('完成日期：'+now,'small')
    para('目标：真实刷视频的稳定呈现、原生触控、低延时。证据覆盖：源端、硬编、收包、解码、SF、AAC、FEC；公网及V50另列边界。')
    valid=[r for r in rows if r['valid']]
    para(f'本轮保存 {len(rows)} 个真实视频会话，其中 {len(valid)} 个通过真实源与完整运行验证；另有16个固定文件解码对照。所有成绩使用指定窗口，不等于用户最终验收。')
    para('33个真实会话的请求播放时长合计1325秒（约22分钟），16个合成会话合计320秒。这是多轮独立实验，不是整夜连续播放。最长单次120秒，不能外推为24小时无人值守稳定。','small')
    table(['已测层次','本轮状态'],[['真实M1→手机LAN UDP','有真实视频与持续采样'],['同文件SPS/显示策略','16轮组件隔离完成'],['云服务器资源','8秒只读；15556串流当时空闲'],['新公网UDP / 蜂窝 / 远程V50','本轮未验收'],['正式升级包替换媒体路线','本轮未发布']],[170,A4[0]-264])
    story.append(PageBreak())
    para('阅读导览','h1')
    for section in SECTIONS:para(section[0]+'  '+section[1],'small')
    para('附录A  本轮逐会话数据与证据；附录B  实验边界和复现入口。','small')
    for section in SECTIONS:
        index,title,paragraphs=section[:3];group=section[3] if len(section)>3 else None
        story.append(PageBreak());web.append('<section>')
        para(index+'  '+title,'h1')
        for p in paragraphs:para(p)
        if group=='codec':
            table(['流配置','input→ready中位','独立SF表现'],[['原SPS，scheduled80','约88-90ms','约59.3-59.5FPS'],['原SPS，immediate80','约89-90ms','58.44FPS；循环边界停顿'],['低延时SPS，全部模式','约22.6-23.6ms','59.81-60.06FPS；>50ms空档0']],[160,135,A4[0]-389])
            para('准确逐轮、合并百分位及配置哈希见 docs/evidence/overnight-20261002/codec-analysis.json。所有数值只属于固定文件隔离测试。','small')
            vals=[]
            for variant in ('base','vui'):
                entry=next(x for x in codec['aggregates'] if x['variant']==variant and x['release']=='scheduled' and x['buffer_ms']==80)
                vals.append(entry['pooled_input_to_ready_ms']['p50_ms'])
            drawing=Drawing(480,107)
            for tick in (0,25,50,75,100):
                x=124+tick*3.12;drawing.add(Line(x,20,x,91,strokeColor=colors.HexColor('#DAE6E7'),strokeWidth=.5));drawing.add(String(x,6,str(tick),fontName='CN',fontSize=8,fillColor=muted))
            for i,(label,v) in enumerate(zip(('原SPS','低延时SPS'),vals)):
                y=66-i*32;drawing.add(String(4,y+5,label,fontName='CN',fontSize=10,fillColor=ink));drawing.add(Rect(124,y,v*3.12,19,fillColor=teal if i else colors.HexColor('#8296A0'),strokeColor=None));drawing.add(String(129+v*3.12,y+5,f'{v:.2f}ms',fontName='CN',fontSize=8.5,fillColor=ink))
            story.append(drawing);para('图：scheduled80同文件组件对照，合并input→ready中位数。不是物理屏幕或端到端延迟。','small')
        elif group:
            selected=[r for r in rows if (r['label'].startswith(group+'-'))]
            data=[]
            for r in selected:
                lab=r['label'].replace(group+'-','')
                data.append([lab,'通过' if r['valid'] else '排除',value(r['fps']),value(r['max_gap']),str(r['over100'])])
            if data:table(['条件/轮次','真实源','SF FPS','最大间隔ms','>100ms'],data,[130,62,76,130,A4[0]-492])
            para('FPS是手机固定窗口的唯一有效SF呈现数/窗长；不是内容独特帧数、光学面板测量或跨设备丢帧率。完整覆盖、触边gap及阶段细节见机器摘要。','small')
            if group=='resolution':
                story.append(PageBreak())
                para('同720P的目标码率补充对照','h2')
                para('这一页把六轮码率对照完整放在一起。分辨率固定720×1280，80ms缓冲、120提交预算保持；顺序为4→8→12→12→8→4Mbps。真实源逐轮验证，不是同一段码流重放。')
                selected=[r for r in rows if r['label'].startswith('bitrate-')]
                if selected:table(['码率/轮次','SF FPS','最大gap ms','>100ms'],[[r['label'],value(r['fps']),value(r['max_gap']),r['over100']] for r in selected],[156,90,140,A4[0]-480])
                para('三种目标均可出现接近60FPS的窗口，8Mbps其中一次仍有132.58ms空档。没有“码率越高帧率越稳”的单调关系。8Mbps是本轮Wi-Fi实验的折中起点；V50仍从540P/4Mbps开始，以她的实测决定是否提高。')
            if group=='budget':
                para('独立预算四轮raw queue p99：12.241 / 0.281 / 0.268 / 12.422ms。gRPC→socket p99：24.784 / 14.918 / 15.942 / 25.741ms。返回60后host尾部差异复现，但手机最后一轮60也达到59.76FPS。第三轮只有matrix脚本的可选CLI代码指纹发生变化，完整媒体管线与二进制一致；报告不将其称为所有源码完全固定。','small')
            if group=='pacing':
                para('结果：两轮nativeonly约59.56/59.60FPS，两轮double约59.64/59.80FPS；四轮都未重现期限失败。当前没有关闭socket pacing+guard的重复收益，保留原双层实验默认。不把干净窗口的阴性结果当作证明异常机制已修复。','small')
        web.append('</section>')
    story.append(PageBreak());para('附录A  全部真实视频会话','h1')
    para('35秒会话主窗为首包后5-30秒；120秒会话为5-110秒。不同组有版本、时间和供帧差异，不能随意合并成单变量因果实验。源验证失败保留为排除行。','small')
    table(['会话','秒窗','SF FPS','最大gap ms','>100ms','有效'],[[r['label'],r['window_s'],value(r['fps']),value(r['max_gap']),r['over100'],'是' if r['valid'] else '否'] for r in rows],[164,46,67,100,56,A4[0]-527])
    story.append(PageBreak());para('附录B  证据、复现与保留状态','h1')
    for t in ['canonical工程目录：/Users/wyw/Documents/Codex/others/huoguo-android。旧runtime/SDK/受限host配置仍在外部原位置，没有被迁移或删除。',
              '运行入口：scripts/probes/overnight_suite.py；stage为preflight、cap-matrix、raw-budget-matrix、codec-matrix、codec-vui-matrix、followup-suite、network-readback。已有证据目录不能覆盖重跑，须选新label或新目录。',
              '统一机器摘要：docs/evidence/overnight-20261002/real-video-summary.json；逐轮原始报告、source/phone-surface、capture-trace均在各会话目录。codec-analysis.json、cap-analysis.json、raw-budget-results文档提供专项核查。',
              '前序参考：docs/h264-sps-low-delay.md、docs/source-causal-results-20261001.md、docs/apple-emulator-decoder-path-20261001.md、docs/latency-refinement-results-20261001.md、docs/private-udp-protocol-progress-20261001.md。参考是本项目已有证据，不冒充本轮新网络测试。',
              '外部依赖：~/Library/Android/sdk；旧android-remote/m1-compare运行目录；/private/tmp的独立encoder、packetizer与受限合成fixture。APK、密钥、AVD磁盘、auth/NPC配置和原始私密轨迹不进入报告或Git。',
              '收尾健康和CPU限制读回：docs/evidence/overnight-20261002/final-health.json。手机上次设置、降频、AVD数据、正式账号及云端过滤保留。实验源码检查与PDF渲染检查分别记录，不替代真实用户验收。']:
        para(t)
    para('工具失败与未纳入成绩的尝试','h2')
    para('初次默认沙箱的ADB预检不可用，保留为preflight-sandbox-unavailable.json；改用获准的执行环境后完成live读回。首次合成fixture构造也在有效文件产生前失败，随后修复清理超时并在获准环境重试成功；没有把失败试次纳入组件成绩。首个缺少native FEC库的probe构建没有安装，后续采用带FEC的固定2395...哈希版本。源码检查72项相关离线测试通过，与33次真实会话属于不同验证层。')
    def frame(c,d):
        c.setStrokeColor(teal);c.setLineWidth(.8);c.line(47,805,A4[0]-47,805)
        c.setFont('CN',8);c.setFillColor(muted);c.drawString(47,818,'HUOGUO ANDROID  /  M1 REAL VIDEO  /  2026-10-02')
        c.drawString(47,29,'内部研发报告 - 真实测试与验收边界并列保留');c.drawRightString(A4[0]-47,29,str(d.page))
    pdf=OUT/'huoguo-android-overnight-report-20261002.pdf'
    doc=SimpleDocTemplate(str(pdf),pagesize=A4,leftMargin=47,rightMargin=47,topMargin=55,bottomMargin=49,title='给火锅的安卓 - 真实串流深度测试报告',author='huoguo-android project')
    doc.build(story,onFirstPage=frame,onLaterPages=frame)
    css='body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#192b36;background:#f1f6f7;line-height:1.9;margin:0}main{max-width:940px;margin:auto;background:white;padding:42px 30px}.title{font-size:36px;line-height:1.4}.h1{font-size:26px;color:#167f83;border-top:1px solid #cfe4e6;padding-top:26px;margin-top:40px}.h2{font-size:18px}.small{font-size:13px;color:#52717c}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th{background:#d9eff0}td,th{padding:10px;text-align:left;border-bottom:1px solid #deeaeb}tr:nth-child(even){background:#f2f7f8}@media print{body{background:white}.h1{break-before:page}main{padding:0}}'
    (OUT/'huoguo-android-overnight-report-20261002.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>给火锅的安卓 - 真实串流深度测试报告</title><style>'+css+'</style><main>'+''.join(web)+'</main></html>',encoding='utf-8')
    (EV/'report-render-input.json').write_text(json.dumps({'generated_at':now,'run_count':len(rows),'valid_real_run_count':len(valid),'sections':len(SECTIONS),'rows':rows},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'pdf':str(pdf),'real_runs':len(rows),'valid':len(valid),'sections':len(SECTIONS)},ensure_ascii=False))

if __name__=='__main__':main()
