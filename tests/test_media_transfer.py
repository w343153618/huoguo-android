"""Security boundaries for the authenticated public-media API."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from media_transfer import MediaError,MediaStore,media_path,file_id,decode_id,upload_name,transfer_slots
from unittest.mock import patch

class MediaAccessTests(unittest.TestCase):
    def test_private_and_traversal_paths_cannot_be_encoded_into_downloads(self):
        for path in ('/data/system/accounts.png','/sdcard/DCIM/../../data/private.png','/sdcard/DCIM/./x.png','/sdcard/Pictures2/x.png','/sdcard/Download/auth.json','/sdcard/DCIM/x.png\nother'):
            with self.subTest(path=path),self.assertRaises(MediaError):decode_id(file_id(path))
    def test_photo_with_chinese_filename_round_trips(self):
        path='/sdcard/Download/火锅互传/周末合照.png'
        self.assertEqual(decode_id(file_id(path)),path)
        self.assertTrue(upload_name('%E5%91%A8%E6%9C%AB%E5%90%88%E7%85%A7.PNG').endswith('-周末合照.png'))
    def test_upload_names_cannot_be_paths_or_executable_packages(self):
        for name in ('..%2Fsecret.png','%2Ftmp%2Fx.png','x%00.png','x%0A.png','x%5Cy.png','app.apk'):
            with self.subTest(name=name),self.assertRaises(MediaError):upload_name(name)
    def test_canonical_symlink_cannot_escape_public_storage(self):
        store=MediaStore('unused','unused','unused')
        store.shell=lambda *args,**kwargs:'/storage/emulated/0' if args[-1]=='/sdcard' else '/data/user/0/private/image.png'
        with self.assertRaises(MediaError) as result:store.resolve('/sdcard/DCIM/link.png')
        self.assertEqual(result.exception.status,403)
    def test_directory_symlink_does_not_expand_allowed_roots(self):
        store=MediaStore('unused','unused','unused')
        store.shell=lambda *args,**kwargs:'/storage/emulated/0' if args[-1]=='/sdcard' else '/data/private/album/image.png'
        with self.assertRaises(MediaError):store.resolve('/sdcard/Download/火锅互传/image.png')
    def test_failed_staging_cannot_lock_out_later_transfers(self):
        store=MediaStore('unused','unused','/unused')
        handler=type('Request',(),{'headers':{'Content-Length':'1','X-File-Name':'x.png'}})()
        with patch.object(pathlib.Path,'mkdir',side_effect=OSError('No space')),self.assertRaises(OSError):store.upload(handler)
        acquired=transfer_slots.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:transfer_slots.release()
    def test_invalid_identifier_is_rejected(self):
        for value in ('','../abc','a=b','%%%','/absolute'):
            with self.subTest(value=value),self.assertRaises(MediaError):decode_id(value)
if __name__=='__main__':unittest.main()
