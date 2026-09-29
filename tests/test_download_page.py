import hashlib,pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from download_page import render_page,sync_downloads

class DownloadPageTest(unittest.TestCase):
    def metadata(self):
        return {'version_name':'1.16','version_code':17,'apk_url':'https://example.test:15556/updates/HuoguoAndroid-v1.16.apk','sha256':hashlib.sha256(b'validated apk').hexdigest(),'apk_size':13,'changelog':'<script>unsafe</script>'}
    def test_versioned_https_link_and_escaped_notes(self):
        page=render_page(self.metadata())
        self.assertIn('下载最新版 v1.16',page)
        self.assertIn('https://example.test:15556/updates/HuoguoAndroid-v1.16.apk',page)
        self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page)
    def test_stale_page_and_alias_repaired_on_unchanged_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);updates=root/'updates';updates.mkdir();download=root/'download';download.mkdir()
            (updates/'HuoguoAndroid-v1.16.apk').write_bytes(b'validated apk')
            (download/'index.html').write_text('old v1.14')
            (download/'AndroidDirect.apk').write_bytes(b'old')
            sync_downloads(updates,download,self.metadata())
            self.assertIn('v1.16',(download/'index.html').read_text())
            self.assertEqual((download/'AndroidDirect.apk').read_bytes(),b'validated apk')
            self.assertEqual((download/'AndroidDirect-v1.16.apk').read_bytes(),b'validated apk')
            before=(download/'index.html').stat().st_mtime_ns
            sync_downloads(updates,download,self.metadata())
            self.assertEqual(before,(download/'index.html').stat().st_mtime_ns)
    def test_bad_apk_never_replaces_existing_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);updates=root/'updates';updates.mkdir();download=root/'download';download.mkdir()
            (updates/'HuoguoAndroid-v1.16.apk').write_bytes(b'wrong')
            (download/'index.html').write_text('existing page')
            with self.assertRaises(ValueError):sync_downloads(updates,download,self.metadata())
            self.assertEqual((download/'index.html').read_text(),'existing page')
