"""Local/fake-network regression checks; no GitHub, device or live service mutations."""
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_release
import pull_updates


class UpdateDeliveryTest(unittest.TestCase):
    APK = b'local unit-test artifact, not an installable APK'

    def metadata(self, *, legacy=False):
        value = dict(version_name='1.30', version_code=31,
                     apk_url='https://146.56.249.175:15556/updates/HuoguoAndroid-v1.30.apk',
                     apk_size=len(self.APK), sha256=hashlib.sha256(self.APK).hexdigest(), changelog='test')
        if not legacy:
            value['release_tag'] = 'v1.30'
        return value

    def test_manifest_binds_immutable_tag_url_and_digest(self):
        metadata = self.metadata()
        self.assertEqual(pull_updates.validate_metadata(metadata, 'https://146.56.249.175:15556/updates/'),
                         'HuoguoAndroid-v1.30.apk')
        for field, value in [('release_tag', 'v1.29'), ('release_tag', '--delete'), ('release_tag', None),
                             ('version_name', '../1.30'), ('version_code', True), ('apk_size', 0),
                             ('apk_size', pull_updates.MAX_APK_SIZE + 1), ('sha256', 'a' * 63),
                             ('apk_url', metadata['apk_url'] + '?redirect=other')]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(RuntimeError):
                    pull_updates.validate_metadata(dict(metadata, **{field: value}),
                                                   'https://146.56.249.175:15556/updates')
        with self.assertRaises(RuntimeError):
            pull_updates.validate_apk(self.APK + b'x', metadata)
        with self.assertRaisesRegex(RuntimeError, 'HTTPS'):
            pull_updates.validate_metadata(dict(metadata, apk_url=metadata['apk_url'].replace('https:', 'http:')),
                                           'http://146.56.249.175:15556/updates')

    def test_new_git_tree_has_only_metadata_not_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / 'HuoguoAndroid.apk').write_bytes(self.APK)
            folder = publish_release.create_metadata_channel(directory / 'channel', self.metadata(),
                hashlib.sha256(self.APK).hexdigest() + '  HuoguoAndroid.apk\n', 'owner/project', 'v1.30')
            tracked = subprocess.run(['git', '-C', str(folder), 'ls-tree', '-r', '--name-only', 'HEAD'],
                                     check=True, capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(tracked, ['SHA256SUMS.txt', 'update.json'])
            self.assertFalse((folder / 'HuoguoAndroid.apk').exists())
            self.assertEqual(json.loads((folder / 'update.json').read_text())['release_tag'], 'v1.30')

    def test_gh_download_is_exact_asset_in_private_temporary_directory(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            folder = pathlib.Path(command[command.index('--dir') + 1])
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            (folder / pull_updates.RELEASE_ASSET).write_bytes(self.APK)
            return types.SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(pull_updates.subprocess, 'run', side_effect=run):
            self.assertEqual(pull_updates.download_release_asset(self.metadata(), temporary), self.APK)
            self.assertEqual(list(pathlib.Path(temporary).iterdir()), [])
        self.assertEqual(commands[0][:7], ['gh', 'release', 'download', 'v1.30', '--repo',
                                           'w343153618/huoguo-android', '--pattern'])
        self.assertEqual(commands[0][7], 'HuoguoAndroid.apk')

    def test_gh_corrupt_asset_is_rejected_and_temporary_file_removed(self):
        def run(command, **kwargs):
            folder = pathlib.Path(command[command.index('--dir') + 1])
            (folder / pull_updates.RELEASE_ASSET).write_bytes(b'x' * len(self.APK))
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(pull_updates.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'digest/size'):
                pull_updates.download_release_asset(self.metadata(), temporary)
            self.assertEqual(list(pathlib.Path(temporary).iterdir()), [])

    def test_new_release_never_falls_back_to_git_apk(self):
        git = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'UPDATE_SOURCE_CERT'):
            pull_updates.fetch_apk(self.metadata(), git, None, False, {})
        git.assert_not_called()
        with mock.patch.object(pull_updates, 'download_release_asset', return_value=self.APK) as gh:
            self.assertEqual(pull_updates.fetch_apk(self.metadata(), git, '/cache', True, {}), self.APK)
            gh.assert_called_once_with(self.metadata(), '/cache')
        git.assert_not_called()
        with mock.patch.object(pull_updates, 'download_pinned_apk', return_value=self.APK) as physical:
            self.assertEqual(pull_updates.fetch_apk(self.metadata(), git, '/cache', False,
                {'UPDATE_SOURCE_CERT': '/public-cert.pem', 'UPDATE_SOURCE_INTERFACES': 'en11,en0'}), self.APK)
            physical.assert_called_once_with(self.metadata(), '/public-cert.pem', ('en11', 'en0'))
        git.assert_not_called()

    def test_published_legacy_blob_is_read_only_compatible(self):
        git = mock.Mock(return_value=self.APK)
        self.assertEqual(pull_updates.fetch_apk(self.metadata(legacy=True), git, None, False, {}), self.APK)
        git.assert_called_once_with('show', 'FETCH_HEAD:HuoguoAndroid.apk')

    def https_mocks(self, payload=None, status=200, content_length=None):
        payload = self.APK if payload is None else payload
        der = b'unit-test public certificate DER'
        context, secure, connection = mock.Mock(), mock.Mock(), mock.Mock()
        context.wrap_socket.return_value = secure
        secure.getpeercert.return_value = der
        response = mock.Mock()
        response.status = status
        response.getheader.return_value = str(len(payload)) if content_length is None else content_length
        response.read.side_effect = io.BytesIO(payload).read
        connection.getresponse.return_value = response
        return context, hashlib.sha256(der).digest(), secure, connection, response

    def test_https_binds_before_connect_and_ignores_proxy_environment(self):
        context, pin, secure, connection, response = self.https_mocks()
        raw = mock.Mock()
        with mock.patch.object(pull_updates.sys, 'platform', 'darwin'), \
             mock.patch.object(pull_updates, 'pinned_context', return_value=(context, pin)), \
             mock.patch.object(pull_updates, 'physical_ipv4', return_value='192.168.9.99'), \
             mock.patch.object(pull_updates.socket, 'if_nametoindex', return_value=11), \
             mock.patch.object(pull_updates.socket, 'socket', return_value=raw), \
             mock.patch.object(pull_updates.http.client, 'HTTPConnection', return_value=connection), \
             mock.patch.dict(os.environ, {'HTTPS_PROXY': 'http://foreign.invalid:7890', 'ALL_PROXY': 'socks5://foreign.invalid:7890'}):
            self.assertEqual(pull_updates.download_pinned_apk(self.metadata(), '/public.pem'), self.APK)
        raw.assert_has_calls([mock.call.setsockopt(pull_updates.socket.IPPROTO_IP, 25, 11),
                              mock.call.bind(('192.168.9.99', 0)),
                              mock.call.connect(('146.56.249.175', 15556))])
        context.wrap_socket.assert_called_once_with(raw, server_hostname='146.56.249.175')
        self.assertEqual(connection.request.call_args.args, ('GET', '/updates/HuoguoAndroid-v1.30.apk'))
        self.assertNotIn('Authorization', connection.request.call_args.kwargs['headers'])
        connection.close.assert_called_once()
        response.close.assert_called_once()
        secure.close.assert_called_once()
        raw.close.assert_called_once()

    def test_bound_failure_retries_only_next_allowed_physical_interface(self):
        context, pin, secure, connection, response = self.https_mocks()
        raw1, raw2 = mock.Mock(), mock.Mock()
        raw1.setsockopt.side_effect = OSError('unit-test bound interface unavailable')
        with mock.patch.object(pull_updates.sys, 'platform', 'darwin'), \
             mock.patch.object(pull_updates, 'pinned_context', return_value=(context, pin)), \
             mock.patch.object(pull_updates, 'physical_ipv4', side_effect=['192.168.9.99', '192.168.9.126']), \
             mock.patch.object(pull_updates.socket, 'if_nametoindex', side_effect=[11, 1]), \
             mock.patch.object(pull_updates.socket, 'socket', side_effect=[raw1, raw2]) as sockets, \
             mock.patch.object(pull_updates.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual(pull_updates.download_pinned_apk(self.metadata(), '/public.pem'), self.APK)
        self.assertEqual(sockets.call_count, 2)
        raw1.connect.assert_not_called()
        raw1.close.assert_called_once()
        raw2.assert_has_calls([mock.call.setsockopt(pull_updates.socket.IPPROTO_IP, 25, 1),
                               mock.call.bind(('192.168.9.126', 0)),
                               mock.call.connect(('146.56.249.175', 15556))])

    def test_https_rejects_certificate_redirect_and_excess_data(self):
        for scenario in ['certificate', 'redirect', 'length', 'extra']:
            with self.subTest(scenario=scenario):
                context, pin, secure, connection, response = self.https_mocks()
                if scenario == 'certificate':
                    secure.getpeercert.return_value = b'wrong certificate'
                elif scenario == 'redirect':
                    response.status = 302
                elif scenario == 'length':
                    response.getheader.return_value = '999999999'
                else:
                    response.getheader.return_value = None
                    response.read.side_effect = io.BytesIO(self.APK + b'x').read
                with mock.patch.object(pull_updates.sys, 'platform', 'darwin'), \
                     mock.patch.object(pull_updates, 'pinned_context', return_value=(context, pin)), \
                     mock.patch.object(pull_updates, 'physical_ipv4', return_value='192.168.9.99'), \
                     mock.patch.object(pull_updates.socket, 'if_nametoindex', return_value=11), \
                     mock.patch.object(pull_updates.socket, 'socket', return_value=mock.Mock()), \
                     mock.patch.object(pull_updates.http.client, 'HTTPConnection', return_value=connection) as http:
                    with self.assertRaisesRegex(RuntimeError, 'Pinned physical update download failed'):
                        pull_updates.download_pinned_apk(self.metadata(), '/public.pem', ('en11',))
                if scenario == 'certificate':
                    http.assert_not_called()
                    secure.close.assert_called_once()
                elif scenario in ['redirect', 'length']:
                    response.read.assert_not_called()
                if scenario == 'redirect':
                    self.assertEqual(connection.request.call_count, 1)

    def test_invalid_virtual_interface_never_opens_socket(self):
        with mock.patch.object(pull_updates.sys, 'platform', 'darwin'), \
             mock.patch.object(pull_updates.socket, 'socket') as sockets:
            with self.assertRaisesRegex(RuntimeError, 'physical en'):
                pull_updates.download_pinned_apk(self.metadata(), '/public.pem', ('utun4',))
            sockets.assert_not_called()

    def test_real_pem_public_pin_parses_without_private_material(self):
        context, pin = pull_updates.pinned_context(ROOT / 'app/src/main/res/raw/server_cert.pem')
        self.assertEqual(len(pin), 32)
        self.assertEqual(context.verify_mode, pull_updates.ssl.CERT_REQUIRED)
        self.assertFalse(context.check_hostname)

    def test_failed_new_asset_leaves_current_manifest_and_apk_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            updates = root / 'updates'
            updates.mkdir()
            previous = dict(self.metadata(legacy=True), version_name='1.29', version_code=30,
                            apk_url='https://146.56.249.175:15556/updates/HuoguoAndroid-v1.29.apk')
            original = json.dumps(previous).encode()
            (updates / 'update.json').write_bytes(original)
            (updates / 'HuoguoAndroid-v1.29.apk').write_bytes(self.APK)
            def run(command, **kwargs):
                value = json.dumps(self.metadata()).encode() if command[-1] == 'FETCH_HEAD:update.json' else b''
                return types.SimpleNamespace(returncode=0, stdout=value)
            with mock.patch.dict(os.environ, {'DIRECT_STATE_DIR': temporary, 'UPDATE_USE_GH': '1'}), \
                 mock.patch.object(pull_updates.subprocess, 'run', side_effect=run), \
                 mock.patch.object(pull_updates, 'fetch_apk', side_effect=RuntimeError('rejected unit-test artifact')):
                with self.assertRaisesRegex(RuntimeError, 'rejected unit-test'):
                    pull_updates.main()
            self.assertEqual((updates / 'update.json').read_bytes(), original)
            self.assertEqual((updates / 'HuoguoAndroid-v1.29.apk').read_bytes(), self.APK)
            self.assertFalse((updates / 'HuoguoAndroid-v1.30.apk').exists())


if __name__ == '__main__':
    unittest.main()
