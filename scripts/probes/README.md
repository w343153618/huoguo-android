# macOS / 安卓模拟器隔离探针

这些工具分层验证本机画面绘制、采集和编码。正式 M5 硬件后端已部署，实测结果及边界见 [60 / 120 FPS 验收记录](../../docs/hardware-60-120fps-20260929.md)。本机通过不等于公网手机验收。

- `measure_local_encoder.py`：现有 scrcpy 软件编码；必须在 M5 主机运行，使用 `emulator-5554` 和已部署的测试源。
- `emulator_grpc_probe.py`：认证回环 gRPC 的完整 RGBA 截图流；报告帧率、帧间隔与时间戳年龄，不保存像素。
- `videotoolbox_probe.swift`：合成 NV12 输入；只验证硬件编码阶段。要求并读回硬件使用状态，报告各阶段耗时。
- `emulator_hardware_encoder.swift`：完整 RGBA 帧经 vImage 转换后，交给 VideoToolbox 硬件 H.264 编码；输出兼容现有视频帧协议，默认探针模式有时长上限；`--service` 供正式适配器使用，仍保留在途帧与写入超时限制。
- `measure_host_hardware.py`：实际 gRPC 画面到上述原型，再用 ffprobe / ffmpeg 在内存中检查码流；与单独编码探针是不同验证层。

先确认没有现有串流，使用项目 `diagnostic-source` 的合成 Activity，不采集私人界面。若正在连接，相关探针会拒绝运行。不要为测试擦除 AVD、改动原账号或开放公网 ADB / gRPC。

对已有模拟器启用 `-grpc 8554 -grpc-use-token` 时，检查实际只监听 `127.0.0.1`；生产实例的启动参数改变需要在无人在用时生效。使用 SDK 生成的 `pid_<PID>.ini` 发现文件，令牌只在进程内读取，不要复制到命令行、报告或 Git。当前 SDK 的实际目录是 `~/Library/Caches/TemporaryItems/avd/running/`。

依赖使用独立虚拟环境及安装 SDK 自己的协议文件。以下命令只准备编译和运行环境，**不会启动、重启模拟器或切换合成界面**：

```sh
probe_dir=$(mktemp -d /private/tmp/huoguo-vt-probe.XXXXXX)
python3 -m venv "$probe_dir/venv"
"$probe_dir/venv/bin/pip" install grpcio==1.84.0 grpcio-tools==1.84.0 grpcio-reflection==1.84.0
"$probe_dir/venv/bin/python" -m grpc_tools.protoc \
  -I "$HOME/Library/Android/sdk/emulator/lib" \
  --python_out="$probe_dir" --grpc_python_out="$probe_dir" \
  "$HOME/Library/Android/sdk/emulator/lib/emulator_controller.proto"
swiftc -O scripts/probes/emulator_hardware_encoder.swift -o "$probe_dir/emulator-hw-encoder"
```

在合成 Activity 已处于前台、首次全屏提示已关闭、画面物理尺寸为 540×1200 时，提供实际发现文件即可运行。按目标实例修改 serial / avd；不要把示例 PID 当成实际 PID：

```sh
PYTHONPATH="$probe_dir:scripts/probes" "$probe_dir/venv/bin/python" \
  scripts/probes/measure_host_hardware.py \
  --discovery "$HOME/Library/Caches/TemporaryItems/avd/running/pid_<实际PID>.ini" \
  --encoder "$probe_dir/emulator-hw-encoder" \
  --serial emulator-5554 --avd phone17-root --duration 10 --fps 30
```

当前测试还需要 Homebrew 的 `/opt/homebrew/bin/ffmpeg` 和 `ffprobe`。输出是性能 JSON，不会使用登录口令；压缩视频只在内存中进行解码校验。测试结束正常 BACK / HOME 退出合成 Activity，取得对应 `DiagnosticSource` 生命周期日志，再确认没有测试串流残留。只读临时模拟器应关闭。

实验中的 `source_scene_fps=30` 是合成源目标，不等于实测绘制帧率。`timestamp_age_ms` 是宿主时钟和模拟器时间戳的估算差，不能解释为手机端延迟。VideoToolbox 属性若设置不受支持，应保留错误码；不能仅因为读取值为 0 就声称已启用该优化。

`measure_source_rendering.py` 支持显式 30 / 60 / 120 FPS 合成源，对比 Android 帧阶段、实际绘制和硬件输出；正常手机自动检测仍用原固定 30 FPS 场景。`measure_hardware_service.py` 验证正式适配器的 H.264、AAC、真实触摸和动态码率；`--gateway` 只使用已有账号和本机 TLS，口令从测试进程环境读取。`--bitrate` 支持 0.5–12 Mbps，报告平均 FPS、5 秒窗口、帧间隔及音频时钟估计，不保存画面。
