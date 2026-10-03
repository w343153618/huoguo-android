# alpha.7 客户端候选：退出确认、加密保存与公网物理 Network

本轮只在独立 `codex/alpha7-physical-nps` 工作树修改源码，以 `b38587d241eaa51e713e469968603fa0febc86ab` 为基线。未修改原 alpha.6 运行树、设备、NPS、账号数据库、Clash、Tailscale 或任何监听器；没有构建、安装或发布 APK。实验默认版本为 `1.31-alpha.7` / `38`，正式默认仍 `1.31` / `32`。

## 客户端交互

- 所有范围的新账号字段默认 `huoguo`。既有 `username` / `nps_username` 的有效显式值保留，包括此前 `wyw`；无依据把既有使用者值当默认值覆写。公网服务端另由主任务显式启用既有 `wyw` / `huoguo` 机主试用 allowlist，不能把客户端默认值变更当作朋友部署验收。
- 增加实际“保存密码”和“清除已保存密码”按钮。复用原 `PasswordStore`：AES-256-GCM、AndroidKeyStore、完整端点身份及用户名 AAD、私有偏好中的 IV/密文；只点击保存才写。地址、用户名、码率等普通偏好不包含明文密码。启动连接清空界面密码框；退出／关闭重开时只为匹配端点和账号加载。
- 密码存储是当前实验包的一组明确保存账号。覆盖保存及清除只影响该包；实验包与正式包 UID/keystore 隔离。读不到或解密失败时清空字段、提示重新输入，不把异常文本或秘密写报告。
- 活跃 UDP 连接按 Back 弹出“要退出远程连接吗？”：继续使用保留当前会话；退出连接只取消弹窗捕获的对象及 generation。重复 Back 不叠弹窗，旧弹窗不能取消新连接。非活跃页面与正式媒体流程保持原行为。系统 Home／多任务仍沿用 `onStop` 取消，不宣称任意系统导航能透明变成远端 Home。
- 测试头像角标、紫色主题和资源选择另见 `alpha7-beta-visual-identity-20261003.md`。正式原头像和图标资源保持原字节。

## 公网 Network 候选

仅 `nps_owner` 选择一个非 VPN Wi-Fi／移动 `Network` lease。优先当前可用物理网络；当前默认为 VPN 时，选择底层 Wi-Fi，再考虑移动网络。缺少可用底层网络、绑定失败、所选网络消失时停止，不能重新选默认 VPN 或静默 TCP 回退。LAN／Tailnet 不使用这个绑定分支。

每个公网 HTTPS 原始 Socket 在 connect 前调用所选 Network 的 `bindSocket`，再套原受信 TLS／节点 leaf pin。认证成功后同一 lease 传入 UDP receiver，DatagramSocket 在本地 bind／READY 前绑定；入媒体前要求已有 HTTPS 绑定成功。控制和媒体都不用 process-global binding。取消可以关闭跟踪的原始 HTTPS Socket，原取消/重放/认证/时钟/参考依赖/80ms缓冲逻辑未改变。

report 的 `nps_physical_network_binding` 只包含固定数值／布尔元数据；通过原数字白名单转为数字。记录 API 成功次数、handle、选择时 transport、同一 lease；`packet_route_verified` 和 `domestic_country_verified` 固定为 false。这些并不等于逐包路径或国内出口验证。

Android VPN 若未允许应用绕行或启用 lockdown，系统可能拒绝底层绑定；这时应明确失败。尚未在 Tailscale 保持开启的真机上验收此候选，更不保证手机号 Wi-Fi 出口的国家属性。官方契约：[Network.bindSocket](https://developer.android.com/reference/android/net/Network#bindSocket(java.net.Socket))、[VPN allowBypass](https://developer.android.com/reference/android/net/VpnService.Builder#allowBypass())。

## helper 与验收入口

`LanUiAcceptance` 保持原一次性私有账号输入，删除输入后正常 HTTPS 认证；公网仅接纳这次明确授权的既有 `wyw` / `huoguo`。两轮离开均执行真实 App Back 弹窗，点实际“继续使用”按钮、验证当前对象和工作线程计数继续，再点实际“退出连接”按钮；之后照常完整报告与重认证。读回不是独立呈现 FPS。

新增可选 `run_authenticated_lan_ui.py --credential-save on`（默认 off）：当前 attempt/retiring 必须为空，实际保存→finish/重开→字段只在内存比较→实际清除→重开为空→再次保存→重开确认保留给用户。仅报告布尔值，不导出密码、密文或 keystore；失败不能算验收通过。driver 为该可选流程多给 20 秒总预算，媒体稳态窗及最多 120 秒会话未延长。

helper／driver 现在严格要求两轮退出 UI 执行读回；公网另要求 current attempt 与 receiver 的同一个 lease、HTTPS/UDP API 绑定成功。旧 alpha.6 helper/客户端没有这些字段，不得混称 alpha.7 验收。

## 本地检查与未验收边界

运行以下限定检查（总计 56 项），均为源码／inert doubles／真实纯 Java 状态机验证：

```sh
python3 -m unittest \
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

- 实际 App + UDP generated Java + helper 对现有 API37 android.jar 做 `javac` 编译（非 APK）。
- 实际 Network lease 对 inert Android API doubles 验证选择、失败、丢失及绑定计数；不连接、发送或监听。
- 实际退出 gate 验证相等但不同对象、generation、旧 token、重复 Back、16 线程唯一 token。
- 原 PasswordStore 用 inert Android／keystore provider、真实 JCE AES-GCM 验证随机 IV、无明文偏好、AAD/密文篡改、commit 失败、清除及新实例加载。它不能替代 AndroidKeyStore 或实际界面重开验收。
- aapt2 编译实际新 XML 资源（非 APK），检查正式四项字节未变。

首轮限定检查出现两个 fixture 更新缺项：退出 fixture 曾使用错误的 `target.lanUdpEntry` 源码字符串；NPS mock success 尚无新增退出/凭据/物理 Network readback。修正的是源码匹配和完整 mock 合同，未放宽真实 driver 的严格验证。独立只读审查另指出 helper 的第二次退出按钮前应重核捕获对象，已在第二次 Back 及正按钮点击前分别核对对象/generation/cancelled，释放 monitor 后才执行 UI 按钮。

下一层由主任务执行：完整 Gradle/lint、原签名构建、匹配 helper、现有账号手机正常登录、保存／重开／清除与两轮退出确认、Tailscale ON 公网路线及国内出口核验。未经这些步骤，不称已解决 VPN-on 超时、声音/触控物理延时或朋友安全隔离。
