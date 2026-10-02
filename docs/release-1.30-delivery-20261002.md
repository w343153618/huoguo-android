# v1.30 发布和实际下载核对

2026-10-02 已发布 [给火锅的安卓 v1.30](https://github.com/w343153618/huoguo-android/releases/tag/v1.30)，版本码 31。维护源码提交 `259632aeb21eff91479beff3e89a013ebf364f27`。保留原签名，实际一加 12 覆盖安装后显示 v1.30，三个快捷按钮保持公网 M1 默认、公网 M5 备选、Tailscale M1 yilufa。

正式版本仅修正线路和更新分发，不含尚未验收的 UDP 实验。旧 M1 固定尾网地址迁移到 `100.65.0.2:15556`；任意用户自填主机／其他端口和画面参数保留。M1/M5 活跃 Headscale 统一为 `https://hs.yilufa.site`，见 [部署记录](headscale-yilufa-migration-20261002.md)。

更新分发遵守仓库规则：APK 作为私有 GitHub Release asset；新 `updates` 分支只有 `update.json` 和 `SHA256SUMS.txt`，不再将 APK 加入 Git。M1 用原有 gh 授权取不可变 tag 的 APK；M5 保留原有只读 deploy key 取 manifest，经固定 M1 公共证书校验的 HTTPS 获取 APK，并强制绑定有线 en11／备选 Wi-Fi en0，禁止代理及不绑定的退路。新 reader 已部署到 M5，备份原文件和 LaunchAgent，仅重载更新 reader，不重启 AVD／NPS／gateway。

实际核对时间：2026-10-02T05:27:56.178184+00:00

| 核对入口 | manifest | 完整 APK | 结果 |
| --- | --- | --- | --- |
| 公网 M1 `146.56.249.175:15556` | HTTP 200、v1.30/code31 | 3026514 字节 | 与候选及 Release SHA-256 一致 |
| 公网 M5 `146.56.249.175:15558` | HTTP 200、v1.30/code31 | 3026514 字节 | 与候选及 Release SHA-256 一致 |
| M1 局域网下载 `192.168.9.128:8089` | 页面为 v1.30／yilufa | 3026514 字节 | 与 Release SHA-256 一致 |

公网 GET 使用 M1 en7 实际 IPv4 绑定及两台服务各自公共证书的精确校验。没有登录账号或口令传输；这些读回不构成公网媒体流畅度的验收。原有 `phone-download/index-m1.html`、`index-m5.html`、`index.html` 文件书签已同步，不删除旧运行目录。

APK SHA-256：`717660dc9c343fc601638346b2fbda041469b10bb89a3385c82cc92db388ff44`。原升级签名 SHA-256：`0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da`。

验证层分别为：本机 release 构建及 lint（0 error/fatal，51 warning）；453 项 Python 回归；Java 地址兼容检查；GitHub 两次当前提交 CI 成功（36968815063／36968736667）；真实一加 12 安装及快捷地址点击；两台公网完整文件 readback。完整串流、多指、音画同步、东北 V50 和新 Tailnet UDP 尚不能由这些构建／下载检查替代。

本机详细证据目录：`docs/evidence/release-1.30-20261002/`，未将 APK、签名私钥或原始未审查日志加入 Git。
