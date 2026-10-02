"""Keep the manual download entrypoint in sync with the signed release manifest."""
import hashlib
import html
import os
import pathlib
import re
import tempfile


def render_page(metadata):
    version = str(metadata['version_name'])
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
        raise ValueError('Invalid version')
    url = str(metadata['apk_url'])
    if not url.startswith('https://'):
        raise ValueError('Download requires HTTPS')
    notes = html.escape(str(metadata.get('changelog', '暂无更新说明')))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>给火锅的安卓 · v{version}</title>
<style>body{{font-family:system-ui;margin:28px auto;max-width:640px;padding:0 20px;line-height:1.75;background:#f5fbf9;color:#285c62}}a{{color:#237973}}.button{{display:block;background:#ddf4ee;padding:16px;border-radius:24px;text-align:center;text-decoration:none;font-size:20px}}code{{background:#e6f2ef;padding:2px 6px;border-radius:4px}}pre{{white-space:pre-wrap;font-family:inherit}}small{{overflow-wrap:anywhere}}</style></head>
<body><h1>给火锅的安卓</h1><p>最新发布：<strong>v{version}</strong>。当前安装版本请看 App 首页“已安装版本”。</p>
<p><a class="button" href="{html.escape(url, quote=True)}">下载最新版 v{version} · 覆盖安装</a></p>
<p><a href="AndroidDirect-v{version}.apk">局域网下载 v{version}</a> · <a href="AndroidDirect-SHA256SUMS.txt">文件校验</a></p>
<ol><li>取消旧的安装窗口，再下载本页最新版覆盖安装；无需卸载或清除 App 数据。</li><li>安装后重新打开 App，确认首页显示“已安装版本 v{version}”。</li><li>默认公网 M1 为 <code>146.56.249.175:15556</code>，可点选公网 M5 <code>146.56.249.175:15558</code>，也可自行填写 IP 和端口，用户名 <code>huoguo</code>；填写已有密码，点“保存密码”可加密保存。</li><li>清晰度、编码模式、码率、帧率、缓冲和声音／指标勾选都在连接前设置，选择会保存。</li></ol>
<details open><summary>本次更新内容</summary><pre>{notes}</pre></details>
<p>本轮 M1 有线地址为 <code>192.168.9.128:15556</code>；地址变化时可在 App 手动修改，无需重新安装。当前虚拟安卓支持一个活动串流，新连接会断开旧连接。</p>
<p>M1 保持开机、接通电源并登录；当前测试虚拟安卓已按要求关闭自动息屏，保留运行环境。</p>
<small>版本码 {int(metadata['version_code'])} · SHA-256 {html.escape(str(metadata['sha256']))}</small>
</body></html>'''


def sync_downloads(updates, download, metadata):
    updates, download = pathlib.Path(updates), pathlib.Path(download)
    version = str(metadata['version_name'])
    page = render_page(metadata).encode()
    source = updates / ('HuoguoAndroid-v'+version+'.apk')
    apk = source.read_bytes()
    if len(apk) != metadata['apk_size'] or hashlib.sha256(apk).hexdigest() != metadata['sha256']:
        raise ValueError('Download APK digest/size mismatch')
    download.mkdir(parents=True, exist_ok=True)
    def atomic(name, data):
        target = download/name
        if target.exists() and target.read_bytes() == data:
            return
        fd, temp = tempfile.mkstemp(prefix='.incoming-', dir=download)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    name = 'AndroidDirect-v'+version+'.apk'
    atomic(name, apk)
    atomic('AndroidDirect.apk', apk)
    atomic('AndroidDirect-SHA256SUMS.txt', (metadata['sha256']+'  '+name+'\n').encode())
    atomic('index.html', page)
    atomic('index-m1.html', page)
    atomic('index-m5.html', page)  # Preserve both host download bookmarks
