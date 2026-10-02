"""Offline tests only: no root execution, UID changes or production artifacts."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from scripts.security import isolated_uid_canary as canary


class CanaryPlanTest(unittest.TestCase):
    def test_default_and_prepare_only_never_create_bind_or_drop(self):
        for arguments in (['canary'], ['canary', '--prepare-only']):
            with patch.object(canary.sys, 'argv', arguments), \
                 patch.object(canary, 'execute_canaries') as execute, \
                 patch.object(canary, 'open_directory') as directory, \
                 patch.object(canary.os, 'setuid') as setuid, \
                 patch.object(canary.socket, 'socket') as socket, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                canary.main()
                self.assertEqual(json.loads(output.getvalue())['mode'], 'plan')
                execute.assert_not_called()
                directory.assert_not_called()
                setuid.assert_not_called()
                socket.assert_not_called()

    def test_run_requires_explicit_profile_sha(self):
        with patch.object(canary.sys, 'argv', ['canary', '--run']), \
             patch.object(canary, 'execute_canaries') as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                canary.main()
            execute.assert_not_called()

    def test_nonroot_run_refuses_before_reading_artifacts_or_making_fixtures(self):
        with patch.object(canary.sys, 'platform', 'darwin'), \
             patch.object(canary.os, 'geteuid', return_value=501), \
             patch.object(canary, 'trusted_bytes') as read, \
             patch.object(canary, 'Fixtures') as fixtures:
            with self.assertRaises(PermissionError):
                canary.execute_canaries('0' * 64)
            read.assert_not_called()
            fixtures.assert_not_called()


class CanaryStagingValidationTest(unittest.TestCase):
    def setUp(self):
        self.profile = b'(deny network-outbound (require-not (require-any (remote tcp "localhost:18131") (remote udp "localhost:53"))))'
        self.state = {'schema': 1, 'root': str(canary.ROOT), 'phase': 'staged',
                      'identities': {'vm': {'name': canary.VM_NAME, 'uid': 600,
                                           'gid': 600, 'home': str(canary.VM_HOME)}}}
        self.user = SimpleNamespace(pw_uid=600, pw_gid=600, pw_dir=str(canary.VM_HOME),
                                    pw_shell='/usr/bin/false')
        self.groups = [600]
        self.metadata = [SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o40755),
                         SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o40755),
                         SimpleNamespace(st_uid=600, st_gid=600, st_mode=0o40700),
                         SimpleNamespace(st_uid=600, st_gid=600, st_mode=0o40700)]
        mocks = (
            patch.object(canary.sys, 'platform', 'darwin'),
            patch.object(canary.os, 'geteuid', return_value=0),
            patch.object(canary, 'open_directory', return_value=9876),
            patch.object(canary.os, 'fstat', side_effect=lambda fd: self.metadata.pop(0)),
            patch.object(canary.os, 'close'),
            patch.object(canary, 'trusted_bytes', side_effect=self.artifact),
            patch.object(canary.pwd, 'getpwnam', side_effect=lambda name: self.user),
            patch.object(canary.grp, 'getgrnam', side_effect=lambda name:
                SimpleNamespace(gr_gid=80 if name == 'admin' else 600, gr_mem=[])),
            patch.object(canary.os, 'getgrouplist', side_effect=lambda name, group: self.groups),
        )
        for item in mocks:
            item.start()
            self.addCleanup(item.stop)

    def artifact(self, path, limit):
        return self.profile if path == canary.PROFILE else json.dumps(self.state).encode()

    def validate(self):
        return canary.validate_staging(hashlib.sha256(self.profile).hexdigest())

    def test_fixed_uid_identity_digest_and_directory_readbacks_are_required(self):
        result = self.validate()
        self.assertEqual((result['uid'], result['gid']), (600, 600))
        self.assertTrue(result['identity_readback_verified'])

    def test_profile_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            canary.validate_staging('0' * 64)

    def test_journal_cannot_request_uid_zero_or_other_home(self):
        self.state['identities']['vm']['uid'] = 0
        with self.assertRaisesRegex(ValueError, 'fixed staged UID600'):
            self.validate()

    def test_nonnormal_shell_or_nested_admin_membership_fails(self):
        self.user.pw_shell = '/bin/zsh'
        with self.assertRaisesRegex(ValueError, 'inconsistent or privileged'):
            self.validate()

    def test_nested_admin_membership_fails(self):
        self.groups = [600, 80]
        with self.assertRaisesRegex(ValueError, 'inconsistent or privileged'):
            self.validate()

    def test_world_writable_staging_or_home_fails(self):
        self.metadata[0].st_mode = 0o40777
        with self.assertRaisesRegex(ValueError, 'protected'):
            self.validate()

    def test_public_dns_allowlist_is_not_accepted(self):
        self.profile = self.profile.replace(b'localhost:53', b'*:53')
        with self.assertRaisesRegex(ValueError, 'fixed guarded proxy/DNS'):
            self.validate()


class CanaryPrivilegeDropTest(unittest.TestCase):
    def test_supplementary_groups_gid_uid_order_and_readback(self):
        recorder = Mock()
        with patch.object(canary.os, 'setgroups') as groups, \
             patch.object(canary.os, 'setgid') as gid, \
             patch.object(canary.os, 'setuid') as uid, \
             patch.object(canary.os, 'umask') as mask, \
             patch.object(canary.os, 'getuid', return_value=600), \
             patch.object(canary.os, 'geteuid', return_value=600), \
             patch.object(canary.os, 'getgid', return_value=600), \
             patch.object(canary.os, 'getegid', return_value=600), \
             patch.object(canary, 'kernel_groups', return_value=[600]):
            for name, item in (('groups', groups), ('gid', gid), ('uid', uid), ('mask', mask)):
                recorder.attach_mock(item, name)
            canary.drop_privileges()
            self.assertEqual(recorder.mock_calls, [call.groups([]), call.gid(600),
                                                   call.uid(600), call.mask(0o077)])

    def test_remaining_privileged_group_is_rejected(self):
        with patch.object(canary.os, 'setgroups'), patch.object(canary.os, 'setgid'), \
             patch.object(canary.os, 'setuid'), patch.object(canary.os, 'umask'), \
             patch.object(canary.os, 'getuid', return_value=600), \
             patch.object(canary.os, 'geteuid', return_value=600), \
             patch.object(canary.os, 'getgid', return_value=600), \
             patch.object(canary.os, 'getegid', return_value=600), \
             patch.object(canary, 'kernel_groups', return_value=[80]):
            with self.assertRaises(PermissionError):
                canary.drop_privileges()

    def test_child_closes_fds_drops_before_exec_and_does_not_inherit_secrets(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b'owned', stderr=b'')
        recorder = Mock()
        with patch.object(canary.subprocess, 'run', return_value=completed) as run, \
             patch.object(canary, 'prepare_darwin_group_reader') as prepare:
            recorder.attach_mock(prepare, 'prepare')
            recorder.attach_mock(run, 'spawn')
            result = canary.child(['/bin/cat', '/fresh-owned-fixture'], sandboxed=True)
        self.assertEqual([item[0] for item in recorder.mock_calls], ['prepare', 'spawn'])
        arguments, = run.call_args.args
        options = run.call_args.kwargs
        self.assertEqual(arguments[:3], ['/usr/bin/sandbox-exec', '-f', str(canary.PROFILE)])
        self.assertIs(options['preexec_fn'], canary.drop_privileges)
        self.assertTrue(options['close_fds'])
        self.assertNotIn('pass_fds', options)
        self.assertEqual(set(options['env']), {'HOME', 'TMPDIR', 'PATH', 'LANG', 'LC_ALL'})
        self.assertTrue(result['identity_verified_before_exec'])

    def test_sdk_or_arbitrary_program_is_never_launched(self):
        for executable in ('/bin/ls', '/opt/homebrew/bin/python3', str(canary.SDK / 'emulator/emulator')):
            with patch.object(canary.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    canary.child([executable], sandboxed=True)
                run.assert_not_called()

    def test_python_directory_groups_do_not_replace_kernel_credential_check(self):
        with patch.object(canary.os, 'setgroups'), patch.object(canary.os, 'setgid'), \
             patch.object(canary.os, 'setuid'), patch.object(canary.os, 'umask'), \
             patch.object(canary.os, 'getuid', return_value=600), \
             patch.object(canary.os, 'geteuid', return_value=600), \
             patch.object(canary.os, 'getgid', return_value=600), \
             patch.object(canary.os, 'getegid', return_value=600), \
             patch.object(canary.os, 'getgroups', return_value=[600, 12, 80]) as directory_groups, \
             patch.object(canary, 'kernel_groups', return_value=[600]) as kernel_groups:
            canary.drop_privileges()
        directory_groups.assert_not_called()
        kernel_groups.assert_called_once_with()

    def test_any_real_non600_kernel_group_still_fails(self):
        for actual in ([0], [600, 80], [600, 12]):
            with patch.object(canary.os, 'setgroups'), patch.object(canary.os, 'setgid'), \
                 patch.object(canary.os, 'setuid'), patch.object(canary.os, 'umask'), \
                 patch.object(canary.os, 'getuid', return_value=600), \
                 patch.object(canary.os, 'geteuid', return_value=600), \
                 patch.object(canary.os, 'getgid', return_value=600), \
                 patch.object(canary.os, 'getegid', return_value=600), \
                 patch.object(canary, 'kernel_groups', return_value=actual):
                with self.assertRaises(PermissionError):
                    canary.drop_privileges()


class DarwinKernelGroupsTest(unittest.TestCase):
    def test_library_and_exact_legacy_symbol_load_before_fork(self):
        library = Mock()
        with patch.object(canary, '_KERNEL_GETGROUPS', None), \
             patch.object(canary, '_KERNEL_LIBC', None), \
             patch.object(canary.sys, 'platform', 'darwin'), \
             patch.object(canary.ctypes, 'CDLL', return_value=library) as load:
            canary.prepare_darwin_group_reader()
            canary.prepare_darwin_group_reader()
            load.assert_called_once_with('/usr/lib/libSystem.B.dylib', use_errno=True)
            self.assertIs(canary._KERNEL_GETGROUPS, library.getgroups)
            self.assertEqual(library.getgroups.restype, canary.ctypes.c_int)

    def test_bounded_legacy_query_reads_exact_process_groups(self):
        def query(count, values):
            if count == 0:
                return 1
            values[0] = 600
            return 1
        with patch.object(canary, '_KERNEL_GETGROUPS', side_effect=query) as read:
            self.assertEqual(canary.kernel_groups(), [600])
            self.assertEqual(read.call_count, 2)

    def test_uninitialized_reader_is_not_loaded_in_preexec(self):
        with patch.object(canary, '_KERNEL_GETGROUPS', None), \
             patch.object(canary.ctypes, 'CDLL') as load:
            with self.assertRaises(RuntimeError):
                canary.kernel_groups()
            load.assert_not_called()

    def test_zero_or_over_budget_group_count_fails_before_allocation(self):
        for count in (0, canary.MAX_KERNEL_GROUPS + 1):
            with patch.object(canary, '_KERNEL_GETGROUPS', return_value=count) as read:
                with self.assertRaises(ValueError):
                    canary.kernel_groups()
                self.assertEqual(read.call_count, 1)

    def test_query_errno_and_second_read_errno_fail_closed(self):
        for returned in ([-1], [1, -1]):
            with patch.object(canary, '_KERNEL_GETGROUPS', side_effect=returned), \
                 patch.object(canary.ctypes, 'get_errno', return_value=errno.EPERM):
                with self.assertRaises(OSError) as raised:
                    canary.kernel_groups()
                self.assertEqual(raised.exception.errno, errno.EPERM)

    def test_changed_group_count_fails_closed(self):
        with patch.object(canary, '_KERNEL_GETGROUPS', side_effect=[1, 0]):
            with self.assertRaises(ValueError):
                canary.kernel_groups()


class CanaryFreshFixtureTest(unittest.TestCase):
    def test_offline_full_workflow_keeps_all_case_layers_and_cleans_only_its_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home, sdk = root / 'guest', root / 'sdk'
            (home / 'tmp').mkdir(parents=True)
            sdk.mkdir()
            immutable_preexisting = sdk / 'preexisting-owned-fixture'
            immutable_preexisting.write_bytes(b'preserve fixture')

            def fake_file_case(name, command, sandboxed, expected_stdout=None, expected_denied=False):
                if command[0] == '/bin/sh' and not expected_denied:
                    path = Path(command[-2])
                    self.assertTrue(path.is_relative_to(root) or path.is_relative_to(Path('/private/var/tmp')))
                    path.write_bytes(command[-1].encode())
                return {'case': name, 'passed': True, 'sandboxed': sandboxed,
                        'expected_denied': expected_denied}

            def fake_network_case(name, *args, **kwargs):
                return {'case': name, 'passed': True, 'listener_closed': True}

            with patch.object(canary, 'VM_HOME', home), patch.object(canary, 'SDK', sdk), \
                 patch.object(canary, 'validate_staging', return_value={
                     'uid': 600, 'gid': 600, 'profile_sha256': '0' * 64,
                     'identity_readback_verified': True}), \
                 patch.object(canary.os, 'fchown'), patch.object(canary.os, 'fchmod'), \
                 patch.object(canary, 'file_case', side_effect=fake_file_case), \
                 patch.object(canary, 'network_case', side_effect=fake_network_case), \
                 patch.object(canary, 'own_lan_address', return_value='192.0.2.1'), \
                 patch.object(canary, 'drop_privileges') as drop:
                report = canary.execute_canaries('0' * 64)
            drop.assert_not_called()
            self.assertEqual({item['case'] for item in report['cases']}, canary.REQUIRED_CASES)
            self.assertEqual(len(report['cases']), 18)
            self.assertTrue(report['all_required_cases_passed'], report)
            self.assertTrue(report['cleanup_complete'])
            self.assertFalse(report['host_isolation_accepted'])
            self.assertFalse(report['vm_launched'])
            self.assertEqual(immutable_preexisting.read_bytes(), b'preserve fixture')
            self.assertEqual(list((home / 'tmp').iterdir()), [])
            self.assertEqual(list(sdk.iterdir()), [immutable_preexisting])

    def test_secure_directory_open_refuses_actual_symlink_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / 'outside').mkdir()
            (base / 'alias').symlink_to(base / 'outside', target_is_directory=True)
            with self.assertRaises(OSError):
                canary.open_directory(base / 'alias')

    def test_cleanup_only_removes_exact_created_inodes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            fixture = base / 'fixture'
            fixture.write_bytes(b'owned original')
            registry = canary.Fixtures()
            registry.remember(fixture)
            fixture.rename(base / 'original-inode-preserved')
            fixture.write_bytes(b'owned replacement')
            cleanup = registry.cleanup()
            self.assertFalse(cleanup[0]['removed'])
            self.assertEqual(fixture.read_bytes(), b'owned replacement')

    def test_directory_and_file_helpers_create_only_exclusively_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(canary.os, 'fchown'), patch.object(canary.os, 'fchmod'):
            base = Path(temporary).resolve()
            registry = canary.Fixtures()
            directory = registry.directory(base / 'owned-directory', 0o700)
            file = registry.file(directory / 'owned-file', b'owned fixture', 0o644)
            with self.assertRaises(FileExistsError):
                registry.file(file, b'must not overwrite', 0o644)
            self.assertEqual(file.read_bytes(), b'owned fixture')
            self.assertTrue(all(result['removed'] for result in registry.cleanup()))
            self.assertFalse(directory.exists())

    def test_busy_proxy_port_never_contacts_or_reuses_existing_service(self):
        with patch.object(canary, 'OwnedListener', side_effect=OSError(errno.EADDRINUSE, 'owned fixture busy')), \
             patch.object(canary, 'child') as child:
            result = canary.network_case('guarded_proxy_only', '127.0.0.1', '127.0.0.1',
                                         port=18131, allowed=True)
        self.assertTrue(result['inconclusive'])
        self.assertFalse(result['passed'])
        child.assert_not_called()

    def test_denial_is_inconclusive_without_unsandboxed_uid_control(self):
        listener = SimpleNamespace(port=30001, marker=b'owned', received=0,
                                   close=Mock(return_value=True), pump=Mock())
        with patch.object(canary, 'OwnedListener', return_value=listener), \
             patch.object(canary, 'child', return_value={'launched': True, 'exit_code': 1, 'stdout': b''}) as child:
            result = canary.network_case('owned_test', '127.0.0.1', '127.0.0.1')
        self.assertFalse(result['passed'])
        self.assertTrue(result['inconclusive'])
        self.assertTrue(result['listener_closed'])
        self.assertEqual(child.call_count, 1)

    def test_no_full_isolation_or_vm_acceptance_claim_is_possible_in_plan(self):
        result = canary.plan()
        self.assertFalse(result['host_isolation_accepted'])
        self.assertFalse(result['runtime_changed'])
        self.assertIn('UID600 DAC', ' '.join(result['checks']))


if __name__ == '__main__':
    unittest.main()
