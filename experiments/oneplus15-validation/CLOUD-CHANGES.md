# 云端转发资源检查（2026-09-29）

服务器：用户指定的 146.56.249.175。云元数据返回 Tencent ap-nanjing；APNIC 返回 TENCENT-CN。2 vCPU，约 7.6 GiB 内存。

## 现状与已执行调整

- 优化前：NPS PID 3821876 约占一个核心100%；Python PID3937657也约占一个核心100%。整机CPU几乎没有空闲，内存可用约4.8GiB，未见内存瓶颈。
- Python已运行约10天，调用栈为stdin第2行 -> glob -> 文件存在性检查，只有stdin/stdout/stderr管道，无socket。所属session-3869.scope仅两个进程（脚本及父bash）。用途未完全查明，**保留进程，未认定可以删除**。
- 对该会话采用可撤销CPUQuota=10%（一个核心的10%，不是整机10%）。运行采样约9.8–10.3%。只针对本次会话，重启后不会把其他用户会话限额。
- NPS提高Nice到-5、CPUWeight到1000。drop-in：/etc/systemd/system/nps.service.d/androidremote-priority.conf。原来Nice=0，CPUWeight默认100。
- 短时开启127.0.0.1:19999的pprof，重启采样15秒后自动还原原始配置并再启动NPS，临时端口关闭。原配置副本/root/nps-before-androidremote-profile.conf权限600留在服务器，含认证配置，不上传。
- 采样累计3.11 CPU秒/15墙钟秒；累计TLS握手约66.9%，RSA签名约52.4%。比例有包含关系，不能相加；这只能说明该采样中的热点，不能证明所有高CPU均由一个特定客户端产生。
- 重启后串流时5秒CPU采样：NPS约11.4%、20.4%；另一次3秒约25.3%。整机5秒样本约26.2%忙。CPU指标按一个核心100%计算。
- BBR与fq队列原先已启用，保留；安卓映射压缩false、额外加密false、RateLimit=0，已有端到端TLS，因此未增加重复压缩。
- 未停止腾讯监控/安全组件、Headscale、Docker及未知业务服务；它们在本次CPU采样中占用较低。

## 回滚

只取消后台扫描限额：`systemctl set-property --runtime session-3869.scope CPUQuota=infinity`。

取消优先级配置：删除本次创建的`/etc/systemd/system/nps.service.d/androidremote-priority.conf`，`systemctl daemon-reload`，`systemctl set-property --runtime nps.service CPUWeight=100`。当前NPS的所有线程也需设置Nice=0；下次正常启动继承恢复后的默认值。不需要删除业务配置或客户端映射。

NPS原始配置已在性能采样结束时自动恢复，不需要再次覆盖。原配置副本不要放进Git。

## 判断边界

资源释放已经核验，但移动网络仍有长帧间隔；不能把CPU变低直接等同于手机已流畅。200Mbps购买带宽不等于每条移动数据路径都能稳定承载高码率；VBR设置8/12Mbps也不等于实际持续发送8/12Mbps。
