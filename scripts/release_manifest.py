"""Create release metadata from a built APK; never contains authentication data."""
import argparse, hashlib, json, pathlib, re
parser = argparse.ArgumentParser()
parser.add_argument('--repository', required=True)
parser.add_argument('--tag', required=True)
parser.add_argument('--base-url', required=True)
parser.add_argument('--apk', type=pathlib.Path, required=True)
parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('update.json'))
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository): raise SystemExit('Invalid repository')
text = pathlib.Path('app/build.gradle').read_text()
version = re.search(r"versionName '([^']+)'", text).group(1)
code = int(re.search(r'versionCode (\d+)', text).group(1))
if args.tag != 'v' + version: raise SystemExit('Release tag must match versionName')
apk = args.apk.read_bytes()
metadata = dict(version_code=code, version_name=version,
    apk_url=args.base_url.rstrip('/')+'/HuoguoAndroid-v'+version+'.apk',
    sha256=hashlib.sha256(apk).hexdigest(), apk_size=len(apk),
    changelog='新增 540p 清晰度，支持 App 内检查更新；保留服务器地址和清晰度设置。')
args.output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
print('Release metadata created; package digest and size recorded')
