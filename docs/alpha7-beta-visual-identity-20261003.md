# alpha.7 实验版视觉区分

只在独立 `alpha7-physical-nps` 工作树实现；未修改原 alpha.6 工作树、手机、虚拟机、服务或 Git 发布状态。

实验版使用独立 adaptive launcher 资源：复用原提供的 `avatar_photo.png`，以矢量笔画叠加紫色“测试”角标。Android 13+ 的 themed icon 另有轮廓／文字矢量遮罩，不尝试把照片当作单色头像。矢量源不依赖字体、AI 图片或新的位图文件。

Manifest 通过 `appIcon`／`appTheme` 参数选择资源。只有 `authenticatedLanUdp` 分支加入 `src/udp/res`，并选择 `ic_launcher_experimental`／`HuoguoExperimentalTheme`；正式分支仍选择原 `ic_launcher` 和系统 `Theme.Material.Light.NoActionBar`。实验版现有“给火锅的安卓 · 实验版”桌面名称保留。

主题为淡紫窗口与紫色系统控件强调色。它影响采用 theme 属性的复选框、对话框和其他系统控件；Java 中显式写定的按钮／页面颜色并不会因为 theme 自动改变。系统版本的 edge-to-edge 规则也可能覆盖状态栏或导航栏颜色，尚未做真机视觉验收。

## 本地源码验证

`python3 -m unittest tests.test_experimental_visual_identity -v`：6 项通过。

- 原正式头像、图标 XML、配色和名称资源的 4 项字节摘要保持原值。
- Gradle 只为实验条件加入资源目录；正式图标／主题的选择保持原值。
- 新 adaptive icon 确实复用原头像并叠加独立矢量角标。
- 采样矢量角标的二次贝塞尔边缘，加上描边后仍落在 108 viewport 的 72 直径圆形安全区内；先前边缘超出约 0.10 单位，已整体上移 1 单位修正。
- 紫底与白色字形的对比度至少 4.5:1；实验主题激活该强调色。
- 使用现有 Android `aapt2 compile` 编译实际资源 XML，只产生临时 `.zip` 编译资源；没有构建 APK 或使用签名材料。

`git diff --check` 通过。这些结果属于资源／源码验证，不证明手机上最终图标、系统主题或串流体验已验收。最终 APK 构建与真机界面检查由主任务负责。

## 冻结文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `app/src/main/AndroidManifest.xml` | `4e741431307417ec0bbd5e740cc62c4744f31049f31921f55f5529e87d70c25f` |
| `app/src/udp/res/drawable/ic_experimental_test_badge.xml` | `b8fe8e7d49c6c8f17b80f130596723546a2428861f42de093dcc0b2f5cadd8f5` |
| `app/src/udp/res/drawable/ic_launcher_experimental_foreground.xml` | `bd5a85b542c643b04387ac8e3d0d837af61fe3a2ed955a90c9eaef314da784e9` |
| `app/src/udp/res/drawable/ic_launcher_experimental_monochrome.xml` | `245c9ce1be8a1d6441a7fbdfad89ec5c5a50a69842c61da27e515b3e6305df14` |
| `app/src/udp/res/mipmap-anydpi-v26/ic_launcher_experimental.xml` | `98a827e3eb2cdc2166100a417b326900d200581fed656736cc2a68a7b010c2e8` |
| `app/src/udp/res/values/experimental_theme.xml` | `3fe607617bcd7195580bc57f3f517a2e249ece29b23e3e712fece727bd6cbcd8` |
| `tests/test_experimental_visual_identity.py` | `05758b4af0ed9fb161e32f03f07664d35a55a8344371a69e09650a9332cb90ec` |

Gradle 集成由负责 Android 实现的代理完成，不在上述资源源文件的所有权范围内。MainActivity／AuthenticatedLanUdpUi 未由此资源任务修改。
