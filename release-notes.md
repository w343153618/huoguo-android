# 给火锅的安卓 v1.25

1. **音画同步精准校准（AudioTimestamp 纳秒硬件时钟）**：
   - 彻底解决音频与画面微小不同步（音频慢 20~40ms）的问题。
   - 底层引入 Android HAL 层的 `AudioTimestamp` 硬件呈现时间戳，精确计算音频从写入缓冲区到最终物理 DAC 扬声器振膜发声的传输耗时。
   - `PlaybackClock` 引入可配置 A/V 同步偏移，并在界面新增「音画同步校准」调节菜单（默认补偿 -25ms），确保画面 VSYNC 刷新与音频采样达到绝对帧级同步。

2. **修复 GitHub Actions CI 构建与单元测试兼容性**：
   - 解决 GitHub CI 构建报错 `Failed in 2 minutes and 41 seconds`。
   - 修复 `V50PresetCheck` 与 `AdaptiveBitrateCheck` 独立编译验证单元测试，恢复与预设签名完全一致的测试校验。
   - 本地与远端 GitHub CI 编译、Lint 及 77 项全量单元测试全部通过。

