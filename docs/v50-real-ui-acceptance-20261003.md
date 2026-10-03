# V50 均衡按钮的有界真实 UI 验收入口

本次只增加 helper／driver 源码及离线 fixture，没有构建 APK、安装或操作手机／服务。基线 `de3139f`；实际 App 的30／60档位、NPS 3600秒会话契约由主线独立修改。

`run_authenticated_lan_ui.py --v50-profile on` 会让 `LanUiAcceptance.prepareUi` 实际点击“真我 V50 · 一键均衡优化”，两次正常登录／退出／重连都点击。缺少按钮即失败，不通过手动设置 profile spinner 冒充按钮验收。此模式采用按钮的540P／4 Mbps VBR／30fps／80ms起点，PCM队列、codec启动实验和stage诊断关闭，Surface lead为0；与额外PCM／startup／lead实验并用时明确拒绝。`--rate-index` 在此模式由按钮的4M覆盖，报告记录有效4M而不是命令行旧值。

默认 `--v50-profile off` 保留原有1080P、默认12M、80ms和owner诊断行为，选择实际文字“60 FPS”，兼容历史60／120／30或新版30／60顺序；不会依赖fps索引。旧helper的OFF报告保持兼容，ON不能缺少新增证明字段。

完成原有一次性私有输入和原签名helper安装准备后，可使用：

```sh
python3 scripts/probes/run_authenticated_lan_ui.py \
  --phone "$TEST_PHONE" --network-scope nps_owner --node m5 \
  --media-only --phone-only-sampler --v50-profile on \
  --steady-seconds 20 --output /private/tmp/huoguo-v50-ui-evidence
```

凭据沿原路径由已授权机主私有输入供给，helper在HTTPS前删除输入；此入口没有新增账号、导出凭据或读取宿主保存的密码。M5手机单独采样不查询本机同串号guest。现有正式会话保护、当前attempt ownership和正常退出确认均保留。

报告的`first_/second_actual_fps_limit`和`actual_buffer_ms`来自当前receiver的解析后`appSession`；`actual_video_width/height`来自已配置真实视频帧的receiver尺寸。ON要求两轮真实按钮点击、30／80和不超过960的实际长边，OFF新增报告要求60／80；它们是协商／解码尺寸证据，不是物理屏幕FPS。按钮点击次数、实际UDP公网元组、独立SF和收尾结果仍分别记录，不把NPS外层路径、国内来源、真实V50性能或宿主隔离写成已通过。

App请求3600秒会话不影响helper时限：稳态只20～30秒，随后正常离开并做短重连；driver仍为120秒加稳态增量、可选凭据UI增量的原总预算。失败收尾沿原有受限helper／实验App路径处理，不等待一小时。
