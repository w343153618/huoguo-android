"""Offline/fake-gh checks; no downloads, credentials, deployment or server restart."""
import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import gateway

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_experimental_release as publisher
import pull_experimental_updates as delivery


class ExperimentalDeliveryCheck(unittest.TestCase):
    APK = b'fake offline APK fixture; not installable'
    REPOSITORY = 'owner/project'
    NAME = '1.31-alpha.1'
    TAG = 'experimental-v' + NAME

    def identity(self):
        return dict(application_id=publisher.APPLICATION_ID, application_label='给火锅的安卓 · 实验版',
                    apk_size=len(self.APK), sha256=hashlib.sha256(self.APK).hexdigest(),
                    signing_certificate_sha256=publisher.EXPECTED_SIGNER)

    def metadata(self, name=NAME, code=32):
        return publisher.manifest(self.identity(), self.REPOSITORY, 'codex/experimental-udp',
                                  'a' * 40, name, code, '仅 LAN UDP 实验，公网媒体尚未验收。')

    def test_manifest_strictly_preserves_experimental_identity_and_transport_scope(self):
        metadata = self.metadata()
        self.assertEqual(delivery.validate_metadata(metadata, self.REPOSITORY, self.TAG),
                         'HuoguoAndroidExperiment-v1.31-alpha.1.apk')
        cases = [('channel', 'stable'), ('prerelease', False), ('application_id', 'local.remoteandroid.direct'),
                 ('version_name', '1.31'), ('version_code', True), ('version_code', 0),
                 ('release_tag', 'v1.31'), ('sha256', 'a' * 63), ('apk_size', 67108865),
                 ('apk_size', 0), ('signing_certificate_sha256', 'c' * 64),
                 ('automatic_formal_update', True), ('media_transport', 'tcp'),
                 ('public_udp_acceptance', True), ('source_commit', '../arbitrary'),
                 ('source_branch', 'main'), ('apk_url', 'https://foreign.invalid/update.apk'),
                 ('schema', True), ('changelog', 'a' * 4001)]
        for field, value in cases:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                delivery.validate_metadata(dict(metadata, **{field: value}), self.REPOSITORY, self.TAG)
        with self.assertRaises(RuntimeError):
            delivery.validate_metadata(dict(metadata, token='never served'), self.REPOSITORY, self.TAG)

    def test_tag_cannot_target_formal_releases_beta_arbitrary_arguments_or_paths(self):
        for tag in ('v1.31', 'experimental-v1.31-beta.1', '../tag', '--latest',
                    'experimental-v1.31-alpha.0', 'experimental-v1.31-alpha.1\n'):
            with self.subTest(tag=tag), self.assertRaises(RuntimeError):
                delivery.checked_tag(tag)

    def test_gh_fetches_exact_prerelease_assets_in_private_staging(self):
        commands = []
        def gh(args, *, timeout):
            commands.append(args)
            if args[1] == 'view':
                return json.dumps(dict(tagName=self.TAG, isPrerelease=True, assets=[
                    dict(name=publisher.MANIFEST_ASSET, size=len(json.dumps(self.metadata()).encode())),
                    dict(name=publisher.APK_ASSET, size=len(self.APK)),
                ])).encode()
            directory = pathlib.Path(args[args.index('--dir') + 1])
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            (directory / publisher.MANIFEST_ASSET).write_text(json.dumps(self.metadata()))
            (directory / publisher.APK_ASSET).write_bytes(self.APK)
            return b''
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(delivery, 'gh', side_effect=gh):
            metadata, apk = delivery.download_release(self.TAG, self.REPOSITORY, folder)
        self.assertEqual(metadata, self.metadata())
        self.assertEqual(apk, self.APK)
        command = commands[1]
        self.assertEqual(command[:5], ['release', 'download', self.TAG, '--repo', self.REPOSITORY])
        self.assertEqual([command[i + 1] for i, value in enumerate(command) if value == '--pattern'],
                         ['experiment.json', 'HuoguoAndroidExperimental.apk'])
        self.assertNotIn('--clobber', command)

    def test_gh_formal_release_is_rejected_before_downloading(self):
        for response in (dict(tagName='v1.31', isPrerelease=True), dict(tagName=self.TAG, isPrerelease=False)):
            with tempfile.TemporaryDirectory() as folder, self.subTest(response=response), \
                 mock.patch.object(delivery, 'gh', return_value=json.dumps(response).encode()) as gh, \
                 self.assertRaises(RuntimeError):
                delivery.download_release(self.TAG, self.REPOSITORY, folder)
            self.assertEqual(gh.call_count, 1)

    def test_gh_oversized_asset_is_rejected_before_downloading(self):
        response = dict(tagName=self.TAG, isPrerelease=True, assets=[
            dict(name=publisher.MANIFEST_ASSET, size=1000),
            dict(name=publisher.APK_ASSET, size=delivery.MAX_APK_SIZE + 1),
        ])
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(delivery, 'gh', return_value=json.dumps(response).encode()) as gh, \
             self.assertRaises(RuntimeError):
            delivery.download_release(self.TAG, self.REPOSITORY, folder)
        self.assertEqual(gh.call_count, 1)

    def test_public_manifest_is_rewritten_and_atomically_committed_after_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            order = []
            atomic = delivery.atomic
            def record(parent, target, data):
                order.append(target.name)
                if target.name == 'experiment.json':
                    self.assertEqual((parent / order[0]).read_bytes(), self.APK)
                atomic(parent, target, data)
            with mock.patch.object(delivery, 'atomic', side_effect=record):
                exposed = delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY)
            name = 'HuoguoAndroidExperiment-v1.31-alpha.1.apk'
            self.assertEqual(order, [name, 'experiment.json'])
            self.assertEqual(exposed['apk_url'], delivery.PUBLIC_BASE + '/' + name)
            self.assertEqual(json.loads((directory / 'experiment.json').read_text()), exposed)
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
            self.assertFalse((directory / 'update.json').exists())
            self.assertEqual(delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY), exposed)

    def test_corrupt_asset_downgrade_and_same_version_change_leave_current_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            exposed = delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY)
            original = (directory / 'experiment.json').read_bytes()
            scenarios = [(self.metadata(), self.APK + b'x'),
                         (self.metadata('1.30-alpha.1', 31), self.APK),
                         (dict(self.metadata(), changelog='changed immutable notes'), self.APK)]
            for metadata, apk in scenarios:
                with self.subTest(metadata=metadata), self.assertRaises(RuntimeError):
                    delivery.install(directory, metadata, apk, self.REPOSITORY)
                self.assertEqual((directory / 'experiment.json').read_bytes(), original)
                self.assertEqual((directory / delivery.public_name(exposed['version_name'])).read_bytes(), self.APK)

    def test_failed_manifest_commit_keeps_previous_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY)
            original = (directory / 'experiment.json').read_bytes()
            atomic = delivery.atomic
            def failure(parent, target, data):
                if target.name == 'experiment.json':
                    raise OSError('offline simulated atomic replacement failure')
                return atomic(parent, target, data)
            with mock.patch.object(delivery, 'atomic', side_effect=failure), self.assertRaises(OSError):
                delivery.install(directory, self.metadata('1.31-alpha.2', 33), self.APK, self.REPOSITORY)
            self.assertEqual((directory / 'experiment.json').read_bytes(), original)
            self.assertEqual((directory / 'HuoguoAndroidExperiment-v1.31-alpha.2.apk').read_bytes(), self.APK)

    def test_actual_apk_mismatch_prevents_public_manifest_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder) / 'experimental'
            directory.mkdir()
            delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY)
            original = (directory / 'experiment.json').read_bytes()
            newer = self.metadata('1.31-alpha.2', 33)
            def download(tag, repository, temporary):
                (pathlib.Path(temporary) / publisher.APK_ASSET).write_bytes(self.APK)
                return newer, self.APK
            with mock.patch.object(delivery, 'download_release', side_effect=download), \
                 mock.patch.object(delivery, 'inspect_apk', return_value=dict(self.identity(), application_id='local.remoteandroid.direct')), \
                 self.assertRaises(RuntimeError):
                delivery.main(['--tag', 'experimental-v1.31-alpha.2', '--repository', self.REPOSITORY,
                               '--directory', str(directory)])
            self.assertEqual((directory / 'experiment.json').read_bytes(), original)
            self.assertFalse((directory / 'HuoguoAndroidExperiment-v1.31-alpha.2.apk').exists())
            self.assertFalse(any(path.name.startswith('.release-') for path in directory.iterdir()))

    def test_complete_fake_delivery_keeps_formal_manifest_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            formal = root / 'updates'
            formal.mkdir()
            (formal / 'update.json').write_bytes(b'current formal channel')
            directory = root / 'experimental'
            def download(tag, repository, temporary):
                self.assertEqual(pathlib.Path(temporary).stat().st_mode & 0o777, 0o700)
                (pathlib.Path(temporary) / publisher.APK_ASSET).write_bytes(self.APK)
                return self.metadata(), self.APK
            with mock.patch.object(delivery, 'download_release', side_effect=download), \
                 mock.patch.object(delivery, 'inspect_apk', return_value=self.identity()):
                exposed = delivery.main(['--tag', self.TAG, '--repository', self.REPOSITORY,
                                         '--directory', str(directory)])
            self.assertEqual(exposed['apk_url'], delivery.PUBLIC_BASE + '/' + delivery.public_name(self.NAME))
            self.assertEqual((formal / 'update.json').read_bytes(), b'current formal channel')
            self.assertFalse(any(path.name.startswith('.release-') for path in directory.iterdir()))

    def test_symlink_manifest_cannot_escape_served_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            outside = root / 'outside'
            outside.write_text('unrelated owner-only data')
            directory = root / 'served'
            directory.mkdir()
            (directory / 'experiment.json').symlink_to(outside)
            with self.assertRaises(OSError):
                delivery.install(directory, self.metadata(), self.APK, self.REPOSITORY)
            self.assertEqual(outside.read_text(), 'unrelated owner-only data')


class ExperimentalGatewayCheck(unittest.TestCase):
    APK = 'HuoguoAndroidExperiment-v1.31-alpha.1.apk'

    def request(self, path, directory):
        handler = gateway.Handler.__new__(gateway.Handler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.connection = mock.Mock()
        handler.send_response, handler.send_header, handler.end_headers = mock.Mock(), mock.Mock(), mock.Mock()
        handler.auth, handler.reply = mock.Mock(return_value=True), mock.Mock()
        handler.close_connection = False
        with mock.patch.dict(os.environ, {'DIRECT_EXPERIMENTAL_DIR': str(directory)}):
            handler.do_GET()
        return handler

    def test_public_allowlisted_manifest_and_apk_need_no_auth_and_are_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            for name, body in (('experiment.json', b'{}'), (self.APK, b'signed offline APK placeholder')):
                (root / name).write_bytes(body)
                request = self.request('/experimental/' + name, root)
                request.auth.assert_not_called()
                request.send_response.assert_called_once_with(200)
                request.send_header.assert_any_call('Content-Length', str(len(body)))
                request.send_header.assert_any_call('Cache-Control', 'no-cache')
                request.send_header.assert_any_call('X-Content-Type-Options', 'nosniff')
                self.assertEqual(request.wfile.getvalue(), body)

    def test_traversal_queries_formal_apk_and_private_files_are_not_whitelisted(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('../auth.json', '%2e%2e/auth.json', 'auth.json', 'experiment.json?cache=1',
                         'HuoguoAndroid-v1.31.apk', 'HuoguoAndroidExperiment-v1.31-beta.1.apk',
                         'HuoguoAndroidExperimental.apk', '.pull.lock', 'subdir/' + self.APK):
                with self.subTest(name=name):
                    request = self.request('/experimental/' + name, folder)
                    request.reply.assert_called_once_with(404, {'error': 'Unknown experimental asset'})
                    request.auth.assert_not_called()
                    request.send_response.assert_not_called()

    def test_missing_oversized_symlink_and_fifo_assets_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            missing = self.request('/experimental/experiment.json', root)
            self.assertEqual(missing.reply.call_args.args[0], 404)
            for name, size in (('experiment.json', 65537), (self.APK, 64 * 1024 * 1024 + 1)):
                path = root / name
                with path.open('wb') as source:
                    source.truncate(size)
                request = self.request('/experimental/' + name, root)
                self.assertEqual(request.reply.call_args.args[0], 503)
                request.send_response.assert_not_called()
                path.unlink()
            outside = root / 'unrelated-private-file'
            outside.write_bytes(b'not an experimental manifest')
            target = root / 'experiment.json'
            target.symlink_to(outside)
            request = self.request('/experimental/experiment.json', root)
            self.assertEqual(request.reply.call_args.args[0], 503)
            self.assertEqual(request.wfile.getvalue(), b'')
            target.unlink()
            os.mkfifo(target)
            request = self.request('/experimental/experiment.json', root)
            self.assertEqual(request.reply.call_args.args[0], 503)
            request.send_response.assert_not_called()

    def test_formal_update_still_reads_original_independent_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            formal, experimental = root / 'updates', root / 'experimental'
            formal.mkdir(); experimental.mkdir()
            (formal / 'update.json').write_bytes(b'formal update manifest unchanged')
            with mock.patch.dict(os.environ, {'DIRECT_UPDATE_DIR': str(formal)}):
                request = self.request('/updates/update.json', experimental)
            request.send_response.assert_called_once_with(200)
            self.assertEqual(request.wfile.getvalue(), b'formal update manifest unchanged')
            request.auth.assert_not_called()


if __name__ == '__main__':
    unittest.main()
