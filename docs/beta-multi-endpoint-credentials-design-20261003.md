# 下一版实验客户端：按端点保存加密账号的最小设计

状态：设计／fixture 计划，未实现新存储、构建 APK、读真实密码、操作手机、服务或发布。设计位于独立 `codex/alpha7-physical-nps` 工作树。已发布 alpha.7 的 source `3387b56` 与本树三项相关源码字节一致：`PasswordStore`、`Endpoint`、`AuthenticatedLanUdpUi`。此次设计不修改它们，正式 v1.31 的资源及单条 PasswordStore 保持原样。

## 要解决的具体问题

目前实验包复用单条 PasswordStore。保存公网 M1 的 `146.56.249.175:49556` 后，再保存公网 M5 的 `146.56.249.175:49558`，会覆盖前一组密文。现有账号普通偏好 `nps_username` 又为两节点共用，故只换密文存储仍不足以记住“各自最后明确保存的一组”。

下一版实验包按完整 HTTPS 控制端点保存：M1 和 M5 即使 IP 相同，端口不同，也属于不同条目。每个端点最多一组最近明确保存的用户名＋密码，总共最多 8 个端点。实际用户名从已验证的对应加密记录恢复；手动修改用户名时保持手动值，不能被自动恢复循环改回。

这里的端点是登录控制端点，不是 UDP 媒体端口。公网 M1/49556、M5/49558、Tailnet/45560、合法 LAN/45560 分别独立。改 IP 创建新端点，不把新 IP 猜成原服务器，也不根据主机显示名转移密码。

## 源码与存储边界

新增类仅放 `app/src/udp/java/local/remoteandroid/direct/`，例如 `ExperimentalCredentialStore`；正式源码中的 PasswordStore 不修改。实验版仍使用独立包 UID，因此不能读取、清除或迁移正式包的 prefs/keystore。

候选新 prefs：`experimental_credentials_v2`；候选 key alias：`huoguo.beta.saved-password.v2`。原实验包 alpha.7 的 `saved_password`／`huoguo.saved-password.v1` 只作为有限迁移来源。新加密密钥与旧密钥分开，不能用“为每一条调用原 PasswordStore.clear”实现清除，这会删除其共享 alias。

密文仍 AES-256-GCM、128-bit tag、随机 IV，key 在 AndroidKeyStore。输入／解密的 UTF-8 必须合法，不允许把不成对 surrogate 或损坏 UTF-8 默默替换成不同凭据。v2 的 AAD 为固定版本前缀＋明确字节长度编码的完整 `Endpoint.identity(...)`＋用户名 UTF-8 原字节。不得退化为 IP、host-only、node label 或用户名以外的模糊匹配。legacy 解密只能使用 v1 原始完整端点＋原始用户名的原 AAD，不尝试 host-only、空用户名或其他账号猜测。

新 prefs 一个 `document` 字符串保存版本、有限迁移状态及最多 8 个密文记录，整体单次 `.commit()`；不把多条记录分散写成逐步成功的多次 commit。最大文档 UTF-8 64KiB；每条端点 256 ASCII 字符内、用户名 128 UTF-16 字符／512 UTF-8 字节内、密码沿用 1024 UTF-16 字符并限制 4096 UTF-8 字节。IV 长度、ciphertext/tag/base64 大小都先验证后解码。8 组上限与最坏 UTF-8/base64 长度相容，不能用不够的 32KiB 上限却声称支持原密码长度。

条目用至多 8 项数组、按 canonical endpoint 的精确字符串比较；不依赖哈希碰撞安全来确定目标，也不引入 LRU 或每次 load 的隐藏写入。重复端点、未知版本／字段、类型错误、超限、缺 IV/tag、损坏 JSON 都应明确失败，不能静默丢掉其它条目或清空 store。认证失败的密文不得填入密码框。

所有新 store 实例共用一个静态 store mutex；解析、选择、加解密、迁移、commit 及删除条目串行化。该 mutex 与 App 的 attempt/gate 锁无关；不在 attempt monitor 内进行 crypto、prefs 或 keystore 调用。不把账号、密码、IV、密文、AAD 或 key alias 值加到性能 report。仅允许固定状态码／布尔值和数量（0..8）。

## 最小操作契约

| 操作 | 成功后的效果 | 必须保留的边界 |
|---|---|---|
| `savedFor(endpoint)` | 仅该端点成功解密后返回一组用户名＋密码到内存 | 无匹配返回 absent；损坏不能假装 absent 后覆写所有数据；不返回 raw record |
| `passwordFor(endpoint,user)` | 仅精确账号匹配才返回密码到内存 | 用户手动编辑账号不被改回；不同账号返回 absent |
| `save(endpoint,user,secret)` | 原端点替换该一条，或空位新增一条 | 8 组已满仍可替换；第 9 个新端点显式失败，原 8 条不变；commit 失败不报成功 |
| `clearCurrent(endpoint)` | 只删除所选服务器的一组，当前密码框清空 | 其它端点和共享 v2 key 不删除；编辑中的用户名不同也不能把此按钮误解释成清除其它服务器 |
| `clearAll()` | 当前实验包全部已保存记录清空，并明确阻止 legacy 复活 | 独立“清除所有已保存密码”按钮及确认弹窗；正式包不改，网络会话不重置 |

首次最小实现可保留空 store 的 v2 key：没有密码密文后它不能用于恢复密码，也避免跨 clearAll/save 的删除密钥竞态。需要 crypto erasure 时可另加严格同锁的 key 删除与失败状态，不能把清密文 commit 成功和 key 删除成功混为一谈。`clearCurrent` 永远不能删除 v2 key。

## legacy 单条迁移及崩溃边界

迁移只处理本实验包现有 v1 的一组，无新增账号，不读服务器的 auth.json。有效迁移在新 store 首次使用之前进行，同 store mutex 串行化：

1. 读有限 v1 metadata、IV/ciphertext；先验证完整端点确实属于当前 App 固定公网／登记 Tailnet／合法 LAN 范围，以及用户名和长度。endpoint 不 canonical 或范围无效不猜测修复。
2. 检查原 v1 alias 是否存在再调用原 PasswordStore 的精确加载，避免“读不到旧 key”时生成新 key 然后冒充迁移。key invalidated、AAD/tag 失败或损坏时不导入、不删除原密文，给固定状态提示重新输入；不打印异常正文。合法新 store 可持久化有限 `legacy_invalid_retained` 状态而保留旧密文，此状态禁止自动重试／fallback，但不禁止用户明确重新保存；不能用读取异常默默清空 v2。
3. v2 若已存在该 endpoint 的有效明确保存条目，该条目优先，不能用旧 v1 覆盖它，也不把旧账号密码误迁移到其它 endpoint。损坏 v2 条目仍是错误，不靠旧 v1 默默覆盖。
4. 成功解密后用 v2 key/AAD 重新加密；在一个 v2 commit 中同时写条目和 `legacy_handled` 状态。v2 commit 失败保留旧 v1，不能先 clear legacy。
5. 验证新记录可按该 endpoint/user 解密后，才清理旧 v1 的密文／旧 alias。清理前重核旧记录仍与最初有限 snapshot 相同；不同旧记录不能被误删。清理失败保留仅加密的旧副本，记有限 `cleanup_pending` 并允许重试；v2 仍是唯一读来源。

迁移状态与记录同文档 commit。标记为 handled 后，不再在读不到某个 endpoint 时尝试 v1 fallback，所以迁移后的 `clearCurrent` 不会在下次启动重新导入旧密码。明确 clearCurrent 若对应仍待清理的同一 v1 来源，继续清理该来源；clearAll 明确清除本实验包的旧副本并写 handled-empty。clearCurrent 的空条目及阻止重导入的状态必须同一次 commit，不能先删除后再异步补 marker。clear/commit 失败不能假装完成。保存其它服务器或者容量已满不授权清除旧资料。

损坏新文档应 fail closed，而不是当成“第一次安装”重跑迁移。只有用户明确确认 clearAll 才允许将损坏的新文档重置为 handled-empty；它是有目标的清除操作，不能藏在 load/save 失败恢复里。新版本不能保证下行回滚的旧 alpha.7 会读取 v2；旧 beta 回滚可能需要重新输入保存，这仅影响手机本地记住密码，不影响服务器账号或虚拟机数据。

## UI 恢复规则

- 首次打开、选择 M1/M5 范围、或地址变成另一个合法完整端点：读取其已验证条目，在 `restoringFields` 下同时恢复该用户名和密码。
- 用户手动修改用户名：保持其输入，只尝试精确 `passwordFor(endpoint,typedUser)`；不把保存的用户名重新 setText。
- 半截／无效地址：清空已加载密码，不写新加密记录，不把上一个端点密码留在框内；保留正在手填的账号。地址编辑合法化后才查询对应端点。
- 无加密条目时保留原有效普通用户名偏好作为兼容 fallback，否则默认 huoguo。原 `username` / `nps_username` 不批量覆写；程序恢复不会把一个节点的用户名写到另一个节点的条目。
- 密码只在点击“保存此服务器密码”时写；“清除此服务器密码”和“清除所有已保存密码”区别明确。保存成功提示当前服务器已保存，不暴露秘密。
- 所有 profile 恢复/清除仍在连接前进行，不改变媒体参数、认证、UDP、时钟、隔离门槛、M1/M5 服务或 Tailnet 身份。

## fixture 计划（均尚未运行新实现）

新类落地后，测试必须调用真实 store，不复制其决策逻辑：复用现有 inert Android SharedPreferences／JCE provider fixture，将 fake keystore 改为按 alias 分组的 map，实际执行 AES-GCM。无网络、真实账号或 AndroidKeyStore 读取。

| 场景 | 期望 |
|---|---|
| M1/49556 与 M5/49558 两条保存、重建实例、反复切换 | 两组各自恢复；IP 相同不能混淆端口 |
| 同 endpoint 从账号 A 明确保存为 B | 仅该 endpoint 最后一组替换，A 匹配不得取得 B 密码 |
| URL 空格／省略端口 canonical 化及非法范围 | 只使用当前 Endpoint 及 LanUdpContract 的规范端点；错误 fail closed |
| 容量 8、第 9 个新 endpoint、已满时替换旧 endpoint | 不静默淘汰；替换保持 8；第 9 个失败不改变原 8 |
| 新 IV、AAD endpoint/user 改动、ciphertext/tag 改动 | IV 不复用；不同端点／用户／篡改都不能解密 |
| metadata/JSON 类型、重复 endpoint、未知 schema、超过 64KiB、非法 UTF-8/surrogate | 不读取无界数据、不隐式清空其它记录 |
| save / clearCurrent / clearAll commit 失败 | 操作失败，内存状态和已有密文不假报成功 |
| clearCurrent M1 | M5 仍可解密；v2 alias 保留；重建后 M1 不复活 |
| clearAll | 两者均空；legacy 不复活；正式模拟 key/prefs 未删除 |
| 合法 v1 exact import、不同端点／账号、缺 key / invalidated / bad tag | 只有合法精确来源导入；错误保留旧密文，无猜测 fallback |
| 新 v2 commit 失败及 commit 后旧清理失败，重建实例 | 前者旧 v1 保留；后者 v2 读源有效并 idempotent retry，不能重复覆盖 |
| existing/newer v2 endpoint 与 v1 冲突、legacy snapshot 被换 | 新显式保存优先；不能删掉不同 legacy snapshot |
| 迁移后 clearCurrent/clearAll 再重建 | handled 标记阻止 v1 恢复；测试持有旧副本也不得 fallback |
| 两个 store 实例并发 save/clear/迁移 | 锁定真实操作；不得丢其它 endpoint 或删除新 key |
| 正式 v1.31 boundary | PasswordStore／原资源 bytes 不变；正式 source set 不包含新类／新 prefs |

UI/纯 policy fixture 另覆盖 `restoringFields`、初次恢复、切节点、账号手打、半截地址及旧显式 wyw 保留。原 single-store AES fixture 保留，不能把新 fake provider 误用成正式 key 修改。

后续真实手机验收使用现有账号、删除后正常读取的一次性私有输入；只输出布尔值。顺序：保存 M1 → 保存 M5 → finish/重开 → 逐个切换恢复 → clear M1 验证 M5 保留 → 再保存 M1。这些本地 UI 操作不用启动 M5 媒体，不影响火锅正在用的正式会话。clearAll 的真机测试只有在全部记录都是此次拥有的测试端点时执行，否则跳过并留作 offline fixture；不得为了测试清掉其它保存记录。原有签名保持，最终正常 M1 认证和 Back 流程另验，不能把保存检查当公网流畅度指标。

## 下一可行动 checkpoint

先实现小型 beta-only 实际存储＋上表有界 fixture，crypto/迁移检查通过后再接 UI 恢复；不先改版本号或发布 manifest。完整原签名 APK 和手机验收由主任务接续，源码层结果与部署层结论分别记录。无需等待 4 小时或再次询问用户。2026-10-03 后续用户要求优先真我 V50 预设，本任务停在此设计／fixture checkpoint，不占有 UDP UI／decoder 或修改 30FPS/profile 参数；该实现由下一次明确的源码分工接续。
