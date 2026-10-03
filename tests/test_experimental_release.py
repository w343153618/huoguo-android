"""Offline publication boundaries; fake APKs do not prove Android installation."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_experimental_release as release


class ExperimentalReleaseCheck(unittest.TestCase):
    NAME = '1.31-alpha.1'
    CODE = 32
    REPOSITORY = 'owner/project'
    BRANCH = 'codex/experimental-udp'
    SHA = 'a' * 40

    def badging(self, *, package=release.APPLICATION_ID, code=CODE, name=NAME, label='给火锅的安卓 · 实验版'):
        return (f"package: name='{package}' versionCode='{code}' versionName='{name}'\n"
                f"application-label:'{label}'\n")

    def certs(self, signer=release.EXPECTED_SIGNER):
        return 'Signer #1 certificate SHA-256 digest: ' + signer + '\n'

    def identity(self):
        return dict(application_id=release.APPLICATION_ID, application_label='给火锅的安卓 · 实验版',
                    apk_size=100, sha256='b' * 64, signing_certificate_sha256=release.EXPECTED_SIGNER)

    def metadata(self):
        return release.manifest(self.identity(), self.REPOSITORY, self.BRANCH, self.SHA,
                                self.NAME, self.CODE, '仅实验 LAN UDP，公网尚未验收。')

    def apk(self, folder, *, native=True, ui=True, extra_native=False):
        path = pathlib.Path(folder) / 'fake.apk'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('classes.dex', release.UDP_CLASS if ui else b'formal-only executable placeholder')
            if native:
                archive.writestr(release.UDP_NATIVE, b'fake native artifact')
            if extra_native:
                archive.writestr('lib/x86_64/libhuoguo_udp_fec.so', b'fake unexpected ABI')
        return path

    def tools(self, command, **kwargs):
        return types.SimpleNamespace(stdout=self.badging() if command[0].endswith('aapt2') else self.certs())

    def test_formal_or_unlabelled_apk_cannot_enter_experimental_channel(self):
        for changes in ({'package': 'local.remoteandroid.direct'}, {'code': 31},
                        {'name': '1.31'}, {'label': '给火锅的安卓'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                release.apk_identity(self.badging(**changes), self.certs(), self.NAME, self.CODE)
        self.assertIn('实验', release.apk_identity(self.badging(), self.certs(), self.NAME, self.CODE))

    def test_other_or_multiple_signers_are_rejected(self):
        for certificates in ('', self.certs('f' * 64),
                             self.certs() + 'Signer #2 certificate SHA-256 digest: ' + 'a' * 64):
            with self.subTest(certificates=certificates), self.assertRaisesRegex(ValueError, 'signing identity'):
                release.apk_identity(self.badging(), certificates, self.NAME, self.CODE)

    def test_experimental_versions_cannot_be_formal_tags_or_arguments(self):
        for name in ('1.31', 'v1.31-alpha.1', '../1.31-alpha.1', '--delete', '1.31-alpha.0', '1.31-alpha.1\n'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.checked_version(name, self.CODE)
        for code in (False, 0, -1, 2100000001, '32'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                release.checked_version(self.NAME, code)
        self.assertEqual(release.checked_version(self.NAME, self.CODE), 'experimental-v1.31-alpha.1')

    def test_native_library_and_real_udp_class_are_required(self):
        for options in ({'native': False}, {'ui': False}, {'extra_native': True}):
            with tempfile.TemporaryDirectory() as folder, self.subTest(options=options), \
                 mock.patch.object(release, 'run', side_effect=self.tools), self.assertRaises(ValueError):
                release.validate_apk(self.apk(folder, **options), '/fake-sdk', self.NAME, self.CODE)

    def test_copied_artifact_digest_and_identity_are_the_manifest_facts(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(release, 'run', side_effect=self.tools):
            apk = self.apk(folder)
            destination = pathlib.Path(folder) / 'frozen'
            metadata = release.prepare(destination, apk, '/fake-sdk', self.REPOSITORY,
                                       self.BRANCH, self.SHA, self.NAME, self.CODE, '实验说明')
            self.assertEqual(metadata['sha256'], hashlib.sha256(apk.read_bytes()).hexdigest())
            self.assertEqual(metadata['apk_size'], apk.stat().st_size)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            self.assertEqual((destination / release.APK_ASSET).stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((destination / release.MANIFEST_ASSET).read_text()), metadata)
            self.assertEqual((destination / 'release-notes.md').read_text(), '实验说明')
            self.assertFalse(metadata['automatic_formal_update'])
            self.assertFalse(metadata['public_udp_acceptance'])

    def test_rejected_artifact_removes_new_folder_and_never_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(release, 'run', side_effect=self.tools):
            apk = self.apk(folder, ui=False)
            destination = pathlib.Path(folder) / 'frozen'
            with self.assertRaises(ValueError):
                release.prepare(destination, apk, '/fake-sdk', self.REPOSITORY,
                                self.BRANCH, self.SHA, self.NAME, self.CODE, '实验说明')
            self.assertFalse(destination.exists())
            destination.mkdir()
            (destination / 'keep').write_text('previous frozen release')
            with self.assertRaises(FileExistsError):
                release.prepare(destination, apk, '/fake-sdk', self.REPOSITORY,
                                self.BRANCH, self.SHA, self.NAME, self.CODE, '实验说明')
            self.assertEqual((destination / 'keep').read_text(), 'previous frozen release')

    def test_public_profiles_do_not_assert_cellular_or_friend_acceptance(self):
        for version, code in [('1.31-alpha.6', 37), ('1.31-alpha.7', 38)]:
            metadata = release.manifest(self.identity(), self.REPOSITORY, self.BRANCH,
                self.SHA, version, code, 'Public owner trial only')
            self.assertEqual(metadata['public_owner_profiles'], ['m1', 'm5'])
            self.assertIn('public_NPS_owner', metadata['media_transport'])
            for key in ('public_udp_acceptance', 'public_cellular_acceptance', 'friend_isolation_acceptance'):
                self.assertIs(metadata[key], False)
        self.assertEqual(self.metadata()['public_owner_profiles'], [])

    def test_apk_output_inside_source_tree_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside the source tree'):
            release.prepare(ROOT / 'output', '/missing.apk', '/sdk', self.REPOSITORY,
                            self.BRANCH, self.SHA, self.NAME, self.CODE, '实验说明')

    def test_publication_never_pushes_formal_branch_manifest_or_clobber_assets(self):
        commands = release.publish_commands('/private/tmp/experiment', self.metadata(), self.REPOSITORY)
        git = [command for command in commands if command[0] == 'git']
        self.assertEqual(len(git), 2)
        self.assertEqual(git[0][-1], self.SHA + ':refs/heads/' + self.BRANCH)
        self.assertEqual(git[1][-1], self.SHA + ':refs/tags/experimental-v1.31-alpha.1')
        joined = '\n'.join(' '.join(command) for command in commands)
        for forbidden in ('--force', '--clobber', 'HEAD:updates', 'refs/heads/main', 'update.json', 'HuoguoAndroid.apk'):
            self.assertNotIn(forbidden, joined)
        self.assertIn('--prerelease', commands[-1])
        self.assertIn('--latest=false', commands[-1])
        self.assertIn('--verify-tag', commands[-1])
        self.assertEqual(commands[-1][-3:], ['/private/tmp/experiment/' + asset
                         for asset in (release.APK_ASSET, release.MANIFEST_ASSET, 'SHA256SUMS.txt')])

    def test_existing_release_or_partial_tag_must_not_be_overwritten(self):
        scenarios = [
            [types.SimpleNamespace(returncode=0, stdout='', stderr='')],
            [types.SimpleNamespace(returncode=1, stdout='', stderr='release not found'),
             types.SimpleNamespace(returncode=0, stdout=self.SHA + '\tref', stderr='')],
            [types.SimpleNamespace(returncode=1, stdout='', stderr='authentication error')],
            [types.SimpleNamespace(returncode=1, stdout='', stderr='release not found'),
             types.SimpleNamespace(returncode=128, stdout='', stderr='network error')],
        ]
        for responses in scenarios:
            with self.subTest(responses=responses), mock.patch.object(release.subprocess, 'run', side_effect=responses), \
                 self.assertRaises(ValueError):
                release.assert_not_published(self.REPOSITORY, 'experimental-v' + self.NAME)
        responses = [types.SimpleNamespace(returncode=1, stdout='', stderr='release not found'),
                     types.SimpleNamespace(returncode=2, stdout='', stderr='')]
        with mock.patch.object(release.subprocess, 'run', side_effect=responses):
            release.assert_not_published(self.REPOSITORY, 'experimental-v' + self.NAME)

    def test_default_preparation_does_not_contact_release_api_or_push(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(release, 'source_identity', return_value=(self.BRANCH, self.SHA)), \
             mock.patch.object(release, 'run', side_effect=self.tools) as calls, \
             mock.patch.object(release, 'assert_not_published') as check:
            apk = self.apk(folder)
            notes = pathlib.Path(folder) / 'notes.md'
            notes.write_text('实验说明')
            metadata = release.main(['--version-name', self.NAME, '--version-code', str(self.CODE),
                '--notes-file', str(notes), '--output-dir', str(pathlib.Path(folder) / 'frozen'), '--apk', str(apk)])
            self.assertEqual(metadata['channel'], 'experimental')
            check.assert_not_called()
            for call in calls.call_args_list:
                self.assertNotIn(call.args[0][0], ('git', 'gh', './gradlew'))

    def test_source_tree_requires_experimental_branch_and_clean_tracked_source(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(release, 'ROOT', pathlib.Path(folder)):
            root = pathlib.Path(folder)
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)
            git('init', '-b', self.BRANCH)
            git('config', 'user.name', 'Offline release boundary test')
            git('config', 'user.email', 'offline@example.invalid')
            for file in ('scripts/publish_experimental_release.py', 'app/build.gradle',
                         'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'):
                path = root / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('local test placeholder')
            git('add', '.')
            git('commit', '-m', 'offline fixture')
            git('remote', 'add', 'origin', 'https://github.com/' + self.REPOSITORY + '.git')
            (root / 'historical-evidence.json').write_text('untracked legacy evidence')
            branch, sha = release.source_identity(self.REPOSITORY)
            self.assertEqual(branch, self.BRANCH)
            self.assertEqual(len(sha), 40)
            (root / 'app/build.gradle').write_text('uncommitted change')
            with self.assertRaisesRegex(ValueError, 'tracked source'):
                release.source_identity(self.REPOSITORY)
            git('checkout', '--', 'app/build.gradle')
            git('branch', '-m', 'main')
            with self.assertRaisesRegex(ValueError, 'experimental-'):
                release.source_identity(self.REPOSITORY)


if __name__ == '__main__':
    unittest.main()
