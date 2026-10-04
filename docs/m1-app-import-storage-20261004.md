# M5 应用迁移到 M1 与数据存储扩容

按用户最新要求，后续研究、探索和优化均以 M1 为主。M5 保留备用测试选项。M5 缺失于 M1 的16个第三方软件包已经全部安装，并核对实际版本；原有 M1 软件没有被替换。应用名称来自实际 APK 元数据。

| 软件 | 包名 | 实际版本号 | 启动入口 |
| --- | --- | --- | --- |
| 讯飞输入法 | com.iflytek.inputmethod | 15952 | 有 |
| 剪映 | com.lemon.lv | 216101600 | 有 |
| 汽水音乐 | com.luna.music | 100211030 | 有 |
| 红果免费短剧 | com.phoenix.read | 73932 | 有 |
| 爱奇艺 | com.qiyi.video | 800170955 | 有 |
| 快手 | com.smile.gifmaker | 50423 | 有 |
| 抖音 | com.ss.android.ugc.aweme.mobile | 320206 | 有 |
| 腾讯视频 | com.tencent.qqlive | 32321 | 有 |
| 豌豆荚 | com.wandoujia.phoenix2 | 803110002 | 有 |
| 优酷视频 | com.youku.phone | 920 | 有 |
| TikTok | com.zhiliaoapp.musically | 2024700030 | 有 |
| FanqieHook | dev.operit.fanqiehook | 28 | 组件，无独立桌面入口 |
| KernelSU | me.weishu.kernelsu | 32601 | 有 |
| Vector | org.matrix.vector.manager | 3080 | 有 |
| NewPipe | org.schabi.newpipe | 1015 | 有 |
| 哔哩哔哩 | tv.danmaku.bili | 9130500 | 有 |

所有 APK 均从 M5 已安装路径只读提取；下载字节数、SHA-256、原始签名和包名/版本核对通过。没有复制 M5 的账号、登录会话、应用数据库、root 授权、hooks 或模块状态。FanqieHook 没有独立桌面入口；它及 KernelSU/Vector 的安装不代表框架或模块已启用。

M1 原有10GiB数据分区接近满，导致剩余软件安装被 Android 的存储检查拒绝。本轮保留数据及加密密钥层，先在虚拟机离线时创建受限完整备份、核对关键文件，再扩展原映像到32GiB。写入文件系统前比较旧逻辑内容相同，正常卸载数据分区并核对244个剩余进程的挂载视图均不再挂载该设备，随后完成离线检查、扩容及后检查。没有强制/懒卸载，也没有清除用户数据。

原启动任务及其配置恢复，实际启动后数据文件系统为8,388,608个4KiB块；安装完成后剩余21,122,488KiB，约20.1GiB。RAM仍8GiB，CPU仍6核，物理1080×1920/density480/30.000002Hz，HWUI仍skiavk。32GiB指数据存储空间，不是内存。旧10GiB测试记录保留原含义。

此前有界候选在卸载资格阶段失败并恢复备份；最终占用定位到系统服务，包括 Thread 的 ot-daemon。最终候选正常卸载成功，离线操作返回0，临时虚拟机自然退出0，正常任务重新启动及旧软件版本核对通过。启动中的早期ADB offline属于轮询记录，不能当最终扩容失败。

TikTok47.0.3已实际启动，前台进程及像素确认通过。首次注册页的Skip进入生日设置页；没有代填生日、创建账号或迁移登录态。当前只能称安装与启动通过，不能称视频播放、手机远程或蜂窝UDP验收。其他软件的启动入口核对也不等于逐个视频/登录验收。

此为明确授权的安装/存储维护：固定排他UDP预约保护原worker，维护步骤核对原gateway身份及四类媒体进程空闲，同时检查正式TCP连接。该TCP准入检查非原子，也没有伪称取得认证实验的503凭证或媒体租约。原gateway、M5、正式NPS及其他NPC均无信号或重启；一加手机未操作。

APK、磁盘、受限备份、原始日志与截图不提交Git。公开数字证据见[m1-app-import-storage-results-20261004.json](m1-app-import-storage-results-20261004.json)。迁移重启使旧source PID/UID/start/格式/播放位置见证失效；后续M1性能窗口须重新取得现场资格。新M1默认选项是源码变更，公开App版本与更新manifest本轮未改变。
