"""Offline dependency/toolchain safety checks; no downloads, CMake or APK build."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT/'experiments/moonlight-v2/transport/android-udp/build.py'
spec = importlib.util.spec_from_file_location('udp_native_build_boundary', BUILD)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DependencyChecks(unittest.TestCase):
    def setUp(self):
        self.pins = builder.load_lock()

    def test_tracked_manifest_is_only_fixed_public_native_dependencies(self):
        self.assertEqual([d['name'] for d in self.pins['dependencies']], ['moonlight-common-c', 'nanors'])
        self.assertEqual(self.pins['dependencies'][0]['commit'], 'f900dd4767759c7b9d0e93bcea666b55c69ea62f')
        self.assertEqual(self.pins['dependencies'][1]['commit'], 'b1e3c22ca0cdc0bb83e3cd6ed1a2fc77869ed99a')
        self.assertEqual(self.pins['android']['ndk_version'], '29.0.14206865')
        self.assertNotIn('upstreams.json', BUILD.read_text())

    def test_lock_rejects_arbitrary_repository_paths_extra_fields_and_malformed_pins(self):
        mutations = [
            lambda p: p.update(schema=True),
            lambda p: p['dependencies'][0].update(repository='https://outside.invalid/core'),
            lambda p: p['dependencies'][1].update(path='../nanors'),
            lambda p: p['dependencies'][1].update(commit='latest'),
            lambda p: p['dependencies'][1].update(credential='fixture-private-data'),
            lambda p: p.update(dependencies=list(reversed(p['dependencies']))),
            lambda p: p['android'].update(abi='x86_64'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory)/'lock.json'
            for mutation in mutations:
                value = copy.deepcopy(self.pins)
                mutation(value)
                lock.write_text(json.dumps(value))
                with self.assertRaises(builder.BuildBoundaryError) as error:
                    builder.load_lock(lock)
                self.assertNotIn('fixture-private-data', str(error.exception))

    def git(self, *, dirty=None, wrong_head=None, wrong_link=False):
        def git(*command):
            checkout = Path(command[2])
            dependency = self.pins['dependencies'][1 if checkout.name == 'nanors' else 0]
            if command[3:] == ('rev-parse', 'HEAD'):
                return '0' * 40 if dependency['name'] == wrong_head else dependency['commit']
            if command[3] == 'status':
                return ' M fixture-private-file' if dependency['name'] == dirty else ''
            if command[3:] == ('ls-tree', 'HEAD', 'nanors'):
                sha = '0' * 40 if wrong_link else self.pins['dependencies'][1]['commit']
                return '160000 commit ' + sha + '\tnanors'
            self.fail('unexpected Git command')
        return git

    def test_clean_pins_and_parent_gitlink_are_required_without_any_fetch(self):
        builder.verify_dependencies('/tmp/fixture-moonlight-core', self.pins, self.git())
        for options in ({'dirty': 'moonlight-common-c'}, {'dirty': 'nanors'},
                        {'wrong_head': 'moonlight-common-c'}, {'wrong_head': 'nanors'},
                        {'wrong_link': True}):
            with self.subTest(options=options), self.assertRaises(builder.BuildBoundaryError) as error:
                builder.verify_dependencies('/tmp/fixture-moonlight-core', self.pins, self.git(**options))
            self.assertNotIn('fixture-private-file', str(error.exception))

    def test_bootstrap_refuses_existing_empty_dirty_or_symlink_paths_without_command(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'existing'
            target.mkdir()
            command = Mock()
            with self.assertRaises(builder.BuildBoundaryError):
                builder.bootstrap_dependencies(target, self.pins, command=command)
            (target/'keep-user-data').write_text('fixture')
            with self.assertRaises(builder.BuildBoundaryError):
                builder.bootstrap_dependencies(target, self.pins, command=command)
            dangling = Path(directory)/'dangling'
            dangling.symlink_to(Path(directory)/'missing')
            with self.assertRaises(builder.BuildBoundaryError):
                builder.bootstrap_dependencies(dangling, self.pins, command=command)
            command.assert_not_called()
            self.assertEqual((target/'keep-user-data').read_text(), 'fixture')

    def test_explicit_bootstrap_uses_exact_commit_fetches_and_does_not_build(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'new-core'
            command, verify = Mock(), Mock()
            self.assertEqual(builder.bootstrap_dependencies(target, self.pins, command, verify), target)
            self.assertTrue(target.is_dir())
            calls = [call.args for call in command.call_args_list]
            self.assertEqual(len(calls), 8)
            for dependency, start in zip(self.pins['dependencies'], (0, 4)):
                checkout = target/dependency['path']
                self.assertEqual(calls[start], ('git', 'init', checkout))
                self.assertEqual(calls[start+1][-2:], ('origin', dependency['repository']))
                self.assertEqual(calls[start+2][-2:], ('origin', dependency['commit']))
                self.assertIn('--no-recurse-submodules', calls[start+2])
                self.assertEqual(calls[start+3][-2:], ('--detach', dependency['commit']))
            verify.assert_called_once_with(target, self.pins)

    def test_actual_local_gitlink_with_independent_nested_checkout_is_clean_then_rejects_drift(self):
        # This exercises Git's real gitlink semantics using only fresh local
        # fixture repositories. No remote, owner Git identity or signing key.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'fixture-core'
            nanors = source/'nanors'
            def git(path, *args):
                return subprocess.run(['git', '-C', str(path), '-c', 'core.hooksPath=/dev/null',
                    '-c', 'commit.gpgsign=false', '-c', 'user.name=Build boundary fixture',
                    '-c', 'user.email=fixture@localhost', *args], check=True,
                    capture_output=True, text=True).stdout.strip()
            source.mkdir()
            git(source, 'init')
            (source/'fixture-core.txt').write_text('public fixture\n')
            git(source, 'add', 'fixture-core.txt')
            nanors.mkdir()
            git(nanors, 'init')
            (nanors/'fixture-nanors.txt').write_text('public fixture\n')
            git(nanors, 'add', 'fixture-nanors.txt')
            git(nanors, 'commit', '-m', 'Local nanors fixture')
            nano_sha = git(nanors, 'rev-parse', 'HEAD')
            git(source, 'update-index', '--add', '--cacheinfo', '160000,' + nano_sha + ',nanors')
            git(source, 'commit', '-m', 'Local core fixture with pinned gitlink')
            pins = copy.deepcopy(self.pins)
            pins['dependencies'][0]['commit'] = git(source, 'rev-parse', 'HEAD')
            pins['dependencies'][1]['commit'] = nano_sha
            builder.verify_dependencies(source, pins)
            (nanors/'fixture-nanors.txt').write_text('dirty fixture\n')
            with self.assertRaises(builder.BuildBoundaryError):
                builder.verify_dependencies(source, pins)

    def test_failed_explicit_bootstrap_keeps_new_directory_and_cannot_reset_it_on_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'new-failed-checkout'
            command = Mock(side_effect=subprocess.CalledProcessError(1, 'fixture-download'))
            with self.assertRaises(subprocess.CalledProcessError):
                builder.bootstrap_dependencies(target, self.pins, command, Mock())
            self.assertTrue(target.is_dir())
            with self.assertRaises(builder.BuildBoundaryError):
                builder.bootstrap_dependencies(target, self.pins, command, Mock())
            self.assertEqual(command.call_count, 1)

    def test_check_only_main_never_configures_builds_or_downloads(self):
        with patch.object(builder, 'verify_dependencies') as verify, \
                patch.object(builder, 'run') as command, \
                patch.object(builder, 'bootstrap_dependencies') as bootstrap, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            builder.main(['--source', '/tmp/offline-fixture', '--check-only'])
        verify.assert_called_once()
        command.assert_not_called()
        bootstrap.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['scope'], 'dependency_checks_only_no_build_or_device')


class ToolchainChecks(unittest.TestCase):
    def setUp(self):
        self.android = builder.load_lock()['android']

    def ndk(self, path, revision='29.0.14206865'):
        path.mkdir(parents=True)
        (path/'source.properties').write_text('Pkg.Revision = ' + revision + '\n')
        (path/'build/cmake').mkdir(parents=True)
        (path/'build/cmake/android.toolchain.cmake').touch()
        return path.resolve()

    def test_linux_android_home_ndk_root_and_explicit_path_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            sdk = directory/'linux-sdk'
            from_sdk = self.ndk(sdk/'ndk'/self.android['ndk_version'])
            from_env = self.ndk(directory/'environment-ndk')
            explicit = self.ndk(directory/'explicit-ndk')
            self.assertEqual(builder.resolve_ndk(None, self.android, {'ANDROID_HOME': str(sdk)}, platform='linux'), from_sdk)
            env = {'ANDROID_HOME': str(sdk), 'ANDROID_NDK_ROOT': str(from_env)}
            self.assertEqual(builder.resolve_ndk(None, self.android, env, platform='linux'), from_env)
            self.assertEqual(builder.resolve_ndk(explicit, self.android, env, platform='linux'), explicit)

    def test_linux_sdk_root_and_mac_existing_default_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            linux = self.ndk(home/'Android/Sdk/ndk'/self.android['ndk_version'])
            mac = self.ndk(home/'Library/Android/sdk/ndk'/self.android['ndk_version'])
            self.assertEqual(builder.resolve_ndk(None, self.android, {}, home, 'linux'), linux)
            self.assertEqual(builder.resolve_ndk(None, self.android, {}, home, 'darwin'), mac)
            self.assertEqual(builder.resolve_ndk(None, self.android, {'ANDROID_SDK_ROOT': str(home/'Android/Sdk')}, home, 'linux'), linux)

    def test_wrong_revision_or_missing_toolchain_never_falls_back_to_another_ndk(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            wrong = self.ndk(directory/'wrong', revision='28.0.0')
            with self.assertRaises(builder.BuildBoundaryError):
                builder.resolve_ndk(wrong, self.android, {})
            valid = self.ndk(directory/'valid')
            (valid/'build/cmake/android.toolchain.cmake').unlink()
            with self.assertRaises(builder.BuildBoundaryError):
                builder.resolve_ndk(valid, self.android, {})

    def test_source_environment_override_does_not_mutate_legacy_cache(self):
        self.assertEqual(builder.default_source({'HUOGUO_UDP_SOURCE': '/tmp/isolated-ci/core'}), Path('/tmp/isolated-ci/core'))
        self.assertEqual(builder.default_source({}), Path.home()/'.cache/huoguo-v2-sources/moonlight-common-c')


if __name__ == '__main__':
    unittest.main()
