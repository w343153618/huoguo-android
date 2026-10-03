# alpha.7 退出回调采样顺序与首页系统栏修复

本次只改独立工作树源码；保留首轮实际手机报告及旧 APK 证据，未重测手机、构建 APK 或操作服务。基线为客户端候选 `aa0be341a44be31d7ea3c210774626290ade35b2`。

## 首轮报告的准确边界

主任务 `docs/evidence/alpha7-ux-public-20261003/phone-m1-public/ui-acceptance.json` 中四项密码 UI 数据分别为 true：实际保存、关闭重开恢复、清除后重开空值、最终保存重开保留。原有 `huoguo` M1 公网登录取得媒体，实际 HTTPS/UDP 同一非 VPN Wi-Fi lease 的绑定次数各为 1；其逐包路径和国家属性未被验证。

整轮随后在 `leaveThroughConfirmation` 失败，标签为 `exit_captured_attempt_not_cancelled`。driver 为完整成功要求没有 helper `failure_class`，因此其整体 credential readback false 不能解释成四项密码操作均失败。首轮证据不改写。

助手读的是正确的 `Attempt.cancelled` 字段。旧实现点击 AlertDialog 的实际正按钮后，立刻检查该字段；这存在具体提前断言。Android 的 [AlertController](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/com/android/internal/app/AlertController.java#149) 将按钮 listener 和 dismiss 发送到 Handler 队列，实际 listener 在后续 `ButtonHandler.handleMessage` 执行。`runOnMainSync` 只完成包含 `performClick` 的当前 runnable，不能把它等同于后续消息已经完成。

## 有限修复

- App 的退出 gate / generation / 精确对象取消代码不改。
- helper 的实际负按钮和正按钮后分别观察回调完成状态。负按钮确认原弹窗已关闭、gate 不再 pending，同时对象/generation 保持；正按钮确认捕获对象已 cancelled，且 current 不再等于它。
- 新对象／新 generation 出现不能当成旧对象已取消。保留正按钮前两次捕获对象检查。UI monitor 不跨线程调度、sleep、网络或 codec。
- 共用实际 `awaitUiCallback`：1500ms polling deadline、20ms 最大退让、最多 76 次状态检查；状态读取晚于 deadline 不能回报成功，时钟倒退或中断明确失败。Android `runOnMainSync` 调度本身可能等待主线程，完整 instrumentation 仍受原 driver 的外部总 timeout 约束；不能宣称此 API 本身是硬实时 1500ms。
- 两会话报告新增 `exit_UI_callbacks_observed`，匹配 driver 严格要求它，而不是把按钮 click 返回当成完成。

## 首页系统栏

仅 UDP login ScrollView 根添加 `WindowInsets.Type.systemBars()` padding，attach 后请求 insets；保留内部 32px 间距，不修改视频 Surface、原生触控坐标或正式页面。标题缩为“给火锅的安卓 · 测试版”、机主有界公网 UDP/120 秒说明与可选范围/断线不改 TCP 媒体一行。实现细节留在工程文档。

## 源码检查

下面 66 项全部通过，`git diff --check` 通过：

```sh
python3 -m unittest \
  tests.test_lan_ui_callback_await \
  tests.test_nps_physical_network_binding \
  tests.test_nps_physical_network_policy \
  tests.test_alpha7_udp_exit_confirmation \
  tests.test_nps_udp_app_contract \
  tests.test_lan_ui_driver_cleanup \
  tests.test_experimental_visual_identity \
  tests.test_udp_password_save_contract \
  tests.test_nps_ui_driver \
  tests.test_lan_udp_app_contract -q
```

新 fixture 从 helper 提取并执行真实 await 方法，与真实纯 Java exit gate 搭配：延后队列回调、继续使用、永不回调、deadline 后回调、新对象、倒退/停滞时钟、慢状态读取和中断。API37 javac 同时编译实际 App/UDP/helper；系统栏 fixture 只确认源码范围，尚未确认最终手机布局。

下一步是主任务构建新 alpha.7 APK／matching helper，保留旧证据，完成真实按钮继续/退出/重连与首页遮挡验收。源码检查不替代该真机结果。
