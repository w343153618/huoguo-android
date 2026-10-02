# 只读播放器状态采样

`source_playback_state.py` 固定读取 `app.morphe.android.youtube` 的活跃媒体会话。只执行一次 `adb shell dumpsys media_session`，在内存解析数值，不发触控、不安装 App、不改变播放，不保存或输出 raw dump、标题、媒体 URL、账号、设备序列号或其他应用状态。

默认 ADB 外部路径为 `~/Library/Android/sdk/platform-tools/adb`，默认设备为 `emulator-5556`。从仓库根运行：

```sh
python3 scripts/probes/source_playback_state.py --serial emulator-5556
```

必要时显式指定其他 ADB 文件路径与有界超时（1–20 秒，默认 8 秒）：

```sh
python3 scripts/probes/source_playback_state.py --adb /absolute/path/to/adb --serial emulator-5556 --timeout 8
```

脚本只输出一行白名单 JSON，所有值为数字、布尔或 null：

```json
{"schema":1,"unknown":false,"matching_sessions":1,"active_sessions":1,"state_known":true,"state":3,"position_ms":12345,"updated_elapsed_ms":777,"speed":1.0,"media_fps_known":false,"media_fps":null,"command_ok":true,"error_code":0,"host_started_monotonic_ns":1000000000,"host_finished_monotonic_ns":1001000000}
```

上面是文档用的合成示例，不是真实设备数据。

`state=2` 表示媒体会话报告暂停，`state=3` 表示报告播放，`state=6` 表示报告缓冲。`position_ms` 是该会话上次报告的媒体位置，`updated_elapsed_ms` 是 Android `SystemClock.elapsedRealtime` 时钟的更新时间，`speed=1` 通常为正常速度、`speed=0` 通常为暂停。Android 的更新时间不能直接减去 Mac 的 `host_*_monotonic_ns`；采样前后在同一个 Android 时钟域内比较位置、更新时间与状态可以辅助判断是否持续播放。这些都是 App 自报的媒体状态，不证明解码画面实际呈现，也不提供媒体编码帧率。

没有目标会话、会话未活跃、多个活跃目标会话或状态语法不明确时，输出 `unknown=true`、`state_known=false`、相关值 null。未知不等于暂停。`media_fps_known` 始终为 false、`media_fps` 始终为 null，禁止把 UI／Surface 帧率当作媒体 FPS。

原始输出上限为 1MiB。仅解析固定 package、匹配 owner 边界和缩进的 `active` 与 `PlaybackState` 数字段；其他字段不进入结果。超时或读取失败会终止自己启动的 ADB 子进程，输出有限错误编号，不输出 stderr 或异常文本。

| error_code | 含义 |
| ---: | --- |
| 0 | 命令成功；仍可能没有已知媒体状态 |
| 1 | ADB 文件／进程启动失败 |
| 2 | 超过时间界限 |
| 3 | 原始输出超过内存界限 |
| 4 | ADB 命令非零退出 |
| 5 | 本机读取失败 |

格式以 [AOSP MediaSessionRecord 的 dump 实现](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/media/MediaSessionRecord.java) 和 [AOSP PlaybackState 的状态／时间定义及 toString 实现](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/master/media/java/android/media/session/PlaybackState.java) 为依据。解析显式匹配外层 `state=PlaybackState {` 后的内层状态，同时接受数值 `state=3` 和官方 `state=PLAYING(3)` 格式；后者必须名称与编号一致，否则返回 unknown。这个符号格式在 2026-10-01 重新查证 AOSP 实现后补入，纯数值解析不会将外层 `PlaybackState` 文本误作编号。如果设备 ROM 改了 dump 语法，安全返回 unknown，不能靠匹配 metadata 中的字符串猜测播放器状态。

验证层级：纯离线解析、隐私字段白名单与错误处理单元测试；没有用这份验证运行设备，也没有声称它已经取得真实播放状态。

```sh
python3 -m unittest discover -s tests -p test_source_playback_state.py
```
