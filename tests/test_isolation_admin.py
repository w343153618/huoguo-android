"""Privileged-staging contracts using ONLY fresh owned fixture directories.

All identity operations, cp/lsof commands, and ownership/mode changes are
mocked. These checks never create system users, launch Android, invoke sudo,
change PF, read personal data, or stage anything at the production root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.security import isolation_admin as admin


class AdminFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='huoguo-admin-owned-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'isolation'
        self.source = self.base / 'source-code'
        self.sdk = self.base / 'source-sdk'
        self.source.mkdir()
        (self.source / 'owned.py').write_text('OWNED_FIXTURE = True\n')
        for item in admin.SDK_ITEMS:
            directory = self.sdk / item
            directory.mkdir(parents=True)
            (directory / 'owned-sdk-fixture').write_bytes(b'fresh SDK fixture')
        self.users, self.groups, self.owners = {}, {}, {}
        self.extra_users, self.extra_groups = [], []
        self.actual_stat = Path.stat
        self.actual_lstat = Path.lstat

        def fixture_stat(path, *args, **kwargs):
            info = self.actual_stat(path, *args, **kwargs)
            if path.is_relative_to(self.base):
                fields = list(info)
                fields[4], fields[5] = self.owners.get(str(path), (0, 0))
                return os.stat_result(fields)
            return info

        def fixture_lstat(path, *args, **kwargs):
            info = self.actual_lstat(path, *args, **kwargs)
            if path.is_relative_to(self.base):
                fields = list(info)
                fields[4], fields[5] = self.owners.get(str(path), (0, 0))
                return os.stat_result(fields)
            return info

        def remember_owner(path, uid, gid, *args, **kwargs):
            self.owners[str(path)] = (uid, gid)

        self.command = Mock(side_effect=self.fixture_command)
        self.subprocess = Mock(return_value=subprocess.CompletedProcess(
            ['/usr/sbin/lsof'], 1, stdout='', stderr=''))
        mocks = (
            patch.object(admin, 'ROOT', self.root),
            patch.object(admin, 'command', self.command),
            patch.object(admin.subprocess, 'run', self.subprocess),
            patch.object(admin.os, 'chown', side_effect=remember_owner),
            patch.object(admin.os, 'lchown', side_effect=remember_owner),
            patch.object(admin.os, 'fchown'),
            patch.object(admin.os, 'chmod'),
            patch.object(admin.os, 'fchmod'),
            patch.object(admin.os, 'geteuid', return_value=0),
            patch.object(Path, 'stat', fixture_stat),
            patch.object(Path, 'lstat', fixture_lstat),
            patch.object(admin.pwd, 'getpwnam', side_effect=self.user_record),
            patch.object(admin.pwd, 'getpwall', side_effect=lambda: list(self.users.values()) + self.extra_users),
            patch.object(admin.grp, 'getgrnam', side_effect=self.group_record),
            patch.object(admin.grp, 'getgrall', side_effect=lambda: list(self.groups.values()) + self.extra_groups),
        )
        for item in mocks:
            item.start()
            self.addCleanup(item.stop)

    def user_record(self, name):
        if name not in self.users:
            raise KeyError(name)
        return self.users[name]

    def group_record(self, name):
        if name == 'admin':
            return SimpleNamespace(gr_name='admin', gr_gid=80, gr_mem=[])
        if name not in self.groups:
            raise KeyError(name)
        return self.groups[name]

    def fixture_command(self, *arguments):
        if arguments == ('/bin/ps', '-axo', 'uid='):
            return ''  # Fresh candidate identities have no fixture processes.
        if arguments[0] == '/bin/cp':
            source, destination = map(Path, arguments[-2:])
            if not source.is_relative_to(self.base) or not destination.is_relative_to(self.base):
                raise AssertionError('Fixture cp must stay in the owned temporary tree')
            shutil.copytree(source, destination, symlinks=True)
            return ''
        if arguments[0] == '/usr/bin/dsmemberutil':
            return 'user is not a member of the group'
        if arguments[0] != '/usr/bin/dscl':
            raise AssertionError('Unexpected external privileged command')
        _, _, operation, node, *fields = arguments
        if operation != '-create':
            raise AssertionError('Fixture only expects bounded dscl creation')
        kind, name = node.strip('/').split('/')
        if kind == 'Groups':
            group = self.groups.setdefault(name, SimpleNamespace(gr_name=name, gr_gid=-1, gr_mem=[]))
            if fields:
                key, value = fields
                if key == 'PrimaryGroupID':
                    group.gr_gid = int(value)
        else:
            user = self.users.setdefault(name, SimpleNamespace(pw_name=name, pw_uid=-1,
                pw_gid=-1, pw_dir='', pw_shell=''))
            if fields:
                key, value = fields
                property_name = {'UniqueID': 'pw_uid', 'PrimaryGroupID': 'pw_gid',
                                 'NFSHomeDirectory': 'pw_dir', 'UserShell': 'pw_shell'}.get(key)
                if property_name:
                    setattr(user, property_name, int(value) if key in ('UniqueID', 'PrimaryGroupID') else value)
        return ''

    def staged_state(self, *, uid=600, gid=600, home=None):
        self.root.mkdir(parents=True, exist_ok=True)
        actual_home = Path(home or self.root / 'homes/vm')
        (actual_home / '.android/avd').mkdir(parents=True, exist_ok=True)
        state = {'schema': 1, 'root': str(self.root), 'phase': 'staged',
                 'identities': {'vm': {'name': admin.NAMES['vm'], 'uid': uid,
                                      'gid': gid, 'home': str(actual_home)}},
                 'avd_cloned': False, 'production_changed': False, 'pf_changed': False}
        self.users[admin.NAMES['vm']] = SimpleNamespace(pw_name=admin.NAMES['vm'],
            pw_uid=uid, pw_gid=gid, pw_dir=str(actual_home), pw_shell='/usr/bin/false')
        self.groups[admin.NAMES['vm']] = SimpleNamespace(gr_name=admin.NAMES['vm'],
            gr_gid=gid, gr_mem=[])
        (self.root / 'candidate-layout.json').write_text(json.dumps(state))
        return state

    def owned_avd(self, config=None):
        directory = self.base / 'source-avd'
        directory.mkdir()
        (directory / 'config.ini').write_text(config or (
            'AvdId=OriginalOwnedFixture\n'
            'image.sysdir.1=system-images/android-37.0/google_apis/arm64-v8a/\n'
            'disk.dataPartition.path=<temp>\n'
            'hw.cpu.ncore=6\n'
            'hw.ramSize=16384\n'))
        (directory / 'userdata-qemu.img').write_bytes(b'owned fake disk, not an AVD')
        return directory


class AdminFileBoundaryTest(AdminFixture):
    def test_reads_only_bounded_single_link_regular_files(self):
        file = self.base / 'owned-file'
        file.write_bytes(b'fresh fixture')
        self.assertEqual(admin.read_regular_file(file), b'fresh fixture')
        with self.assertRaises(ValueError):
            admin.read_regular_file(file, limit=3)
        hardlink = self.base / 'owned-hardlink'
        hardlink.hardlink_to(file)
        with self.assertRaises(ValueError):
            admin.read_regular_file(file)

    def test_fifo_and_directory_cannot_block_or_enter_module_copy(self):
        fifo = self.base / 'owned-fifo'
        os.mkfifo(fifo)
        for path in (fifo, self.source):
            with self.assertRaises((ValueError, OSError)):
                admin.read_regular_file(path)

    def test_exclusive_write_never_overwrites_or_follows_existing_target(self):
        outside = self.base / 'owned-outside'
        outside.write_bytes(b'preserve fixture')
        alias = self.base / 'owned-alias'
        alias.symlink_to(outside)
        for path in (outside, alias):
            with self.assertRaises((ValueError, OSError)):
                admin.write_new_file(path, b'must not overwrite')
        self.assertEqual(outside.read_bytes(), b'preserve fixture')

    def test_real_symlink_parent_is_rejected_before_directory_or_file_mutation(self):
        outside = self.base / 'owned-outside-parent'
        outside.mkdir()
        (outside / 'existing').write_bytes(b'owned marker')
        alias = self.base / 'owned-parent-alias'
        alias.symlink_to(outside, target_is_directory=True)
        for operation in (
            lambda: admin.ensure_real_directory(alias / 'new-dir', 0o700),
            lambda: admin.write_new_file(alias / 'new-file', b'new'),
            lambda: admin.read_regular_file(alias / 'existing'),
            lambda: admin.clone_directory(self.source, alias / 'clone'),
        ):
            with self.assertRaises((ValueError, OSError)):
                operation()
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ['existing'])

    def test_parent_swap_between_check_and_privileged_read_cannot_escape(self):
        directory = self.base / 'read-parent'
        directory.mkdir()
        (directory / 'owned.py').write_bytes(b'expected fixture')
        outside = self.base / 'read-outside'
        outside.mkdir()
        (outside / 'owned.py').write_bytes(b'outside fixture must not be read')
        target = directory / 'owned.py'
        original_check = admin.reject_symlink_parents
        swapped = False

        def swap_after_check(path):
            nonlocal swapped
            original_check(path)
            if Path(path) == target and not swapped:
                directory.rename(self.base / 'read-parent-original')
                directory.symlink_to(outside, target_is_directory=True)
                swapped = True

        with patch.object(admin, 'reject_symlink_parents', side_effect=swap_after_check):
            with self.assertRaises((ValueError, OSError)):
                admin.read_regular_file(target)
        self.assertTrue(swapped)

    def test_parent_swap_between_check_and_privileged_create_cannot_escape(self):
        directory = self.base / 'write-parent'
        directory.mkdir()
        outside = self.base / 'write-outside'
        outside.mkdir()
        target = directory / 'must-not-escape'
        original_check = admin.reject_symlink_parents
        swapped = False

        def swap_after_check(path):
            nonlocal swapped
            original_check(path)
            if Path(path) == target and not swapped:
                directory.rename(self.base / 'write-parent-original')
                directory.symlink_to(outside, target_is_directory=True)
                swapped = True

        with patch.object(admin, 'reject_symlink_parents', side_effect=swap_after_check):
            with self.assertRaises((ValueError, OSError)):
                admin.write_new_file(target, b'new fixture')
        self.assertTrue(swapped)
        self.assertFalse((outside / target.name).exists())


class AdminIdentityTest(AdminFixture):
    def test_preexisting_identity_is_never_adopted_even_if_home_and_shell_match(self):
        name = admin.NAMES['vm']
        self.users[name] = SimpleNamespace(pw_name=name, pw_uid=600, pw_gid=600,
            pw_dir=str(self.root / 'homes/vm'), pw_shell='/usr/bin/false')
        with self.assertRaises(ValueError):
            admin.account('vm')
        self.command.assert_not_called()

    def test_preexisting_group_is_never_overwritten(self):
        name = admin.NAMES['vm']
        self.groups[name] = SimpleNamespace(gr_name=name, gr_gid=0, gr_mem=[])
        with self.assertRaises(ValueError):
            admin.account('vm')
        self.command.assert_not_called()

    def test_uid_and_gid_collisions_are_both_excluded(self):
        self.extra_users.append(SimpleNamespace(pw_uid=600))
        self.extra_groups.append(SimpleNamespace(gr_gid=601))
        record = admin.account('vm')
        self.assertEqual((record['uid'], record['gid']), (602, 602))
        properties = [call.args[-2:] for call in self.command.call_args_list
                      if call.args[0] == '/usr/bin/dscl' and len(call.args) == 6]
        self.assertIn(('Password', '*'), properties)
        self.assertIn(('AuthenticationAuthority', ';DisabledUser;'), properties)
        self.assertIn(('UserShell', '/usr/bin/false'), properties)

    def test_new_identity_readback_rejects_uid_zero_or_mismatched_primary_group(self):
        original = self.user_record
        for value, field in ((0, 'pw_uid'), (80, 'pw_gid')):
            self.users.clear()
            self.groups.clear()
            def corrupt(name, field=field, value=value):
                record = original(name)
                setattr(record, field, value)
                return record
            with patch.object(admin.pwd, 'getpwnam', side_effect=corrupt):
                with self.assertRaises(ValueError):
                    admin.account('vm')

    def test_nested_administrator_membership_is_rejected(self):
        original = self.fixture_command
        def member(*arguments):
            if arguments[0] == '/usr/bin/dsmemberutil':
                return 'user is a member of the group'
            return original(*arguments)
        self.command.side_effect = member
        with self.assertRaises(ValueError):
            admin.account('vm')


class AdminProvisionTest(AdminFixture):
    def test_first_staging_records_distinct_new_identities_and_immutable_module_digest(self):
        state = admin.provision(self.source, self.sdk)
        self.assertEqual(state['phase'], 'staged')
        self.assertEqual(len({value['uid'] for value in state['identities'].values()}), 3)
        self.assertTrue(all(value['uid'] >= 600 and value['gid'] == value['uid']
                            for value in state['identities'].values()))
        self.assertFalse(state['production_changed'])
        self.assertFalse(state['pf_changed'])
        self.assertFalse(state['isolation_accepted'])
        copied = self.root / 'shared/code/owned.py'
        self.assertEqual(copied.read_bytes(), (self.source / 'owned.py').read_bytes())
        self.assertIn('owned.py', state['module_sha256'])

    def test_nonempty_partial_root_is_not_overwritten_or_resumed_implicitly(self):
        self.root.mkdir()
        marker = self.root / 'partial-owned-fixture'
        marker.write_bytes(b'preserve partial fixture')
        with self.assertRaises(ValueError):
            admin.provision(self.source, self.sdk)
        self.command.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'preserve partial fixture')

    def test_failure_preserves_repair_journal_and_retry_does_not_overwrite(self):
        original = self.fixture_command
        copies = 0
        def fail_second_copy(*arguments):
            nonlocal copies
            if arguments[0] == '/bin/cp':
                copies += 1
                if copies == 2:
                    raise OSError('owned fixture interrupted clone')
            return original(*arguments)
        self.command.side_effect = fail_second_copy
        with self.assertRaises(OSError):
            admin.provision(self.source, self.sdk)
        marker = self.root / 'candidate-layout.json'
        state = json.loads(marker.read_bytes())
        self.assertEqual(state['phase'], 'staging_failed')
        self.assertEqual(state['failure_class'], 'OSError')
        self.assertEqual(len(state['identities']), 3)
        before = marker.read_bytes()
        self.command.reset_mock()
        with self.assertRaises(ValueError):
            admin.provision(self.source, self.sdk)
        self.command.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)

    def test_source_python_fifo_fails_without_reading_or_blocking(self):
        os.mkfifo(self.source / 'owned-fifo.py')
        with self.assertRaises(ValueError):
            admin.provision(self.source, self.sdk)
        state = json.loads((self.root / 'candidate-layout.json').read_bytes())
        self.assertEqual(state['phase'], 'staging_failed')
        self.assertFalse((self.root / 'shared/code/owned-fifo.py').exists())


class AdminCloneBoundaryTest(AdminFixture):
    def test_running_candidate_uid_cannot_race_publication(self):
        self.staged_state()
        source = self.owned_avd()
        original = self.fixture_command
        self.command.side_effect = lambda *args: '600\n' if args == ('/bin/ps', '-axo', 'uid=') else original(*args)
        with self.assertRaises(RuntimeError):
            admin.cold_clone(source)
        self.assertFalse(any(call.args[0] == '/bin/cp' for call in self.command.call_args_list))

    def test_source_becoming_open_after_copy_is_not_accepted(self):
        self.staged_state()
        source = self.owned_avd()
        self.subprocess.side_effect = (
            subprocess.CompletedProcess(['/usr/sbin/lsof'], 1, stdout='', stderr=''),
            subprocess.CompletedProcess(['/usr/sbin/lsof'], 0, stdout='123\n', stderr=''))
        with self.assertRaises(RuntimeError):
            admin.cold_clone(source)
        self.assertFalse(json.loads((self.root / 'candidate-layout.json').read_bytes())['avd_cloned'])

    def test_source_mutating_during_copy_is_not_accepted(self):
        self.staged_state()
        source = self.owned_avd()
        original = self.fixture_command
        def mutate(*args):
            result = original(*args)
            if args[0] == '/bin/cp':
                (source/'config.ini').write_text('changed during copy\n')
            return result
        self.command.side_effect = mutate
        with self.assertRaises(RuntimeError):
            admin.cold_clone(source)
        self.assertFalse(json.loads((self.root / 'candidate-layout.json').read_bytes())['avd_cloned'])

    def test_sdk_escape_symlink_and_nonordinary_object_are_rejected(self):
        tree = self.base / 'sdk-tree'
        tree.mkdir()
        outside = self.base / 'sdk-owned-outside'
        outside.write_bytes(b'outside SDK fixture')
        (tree / 'escape').symlink_to(outside)
        with self.assertRaises(ValueError):
            admin.protect_tree(tree)
        (tree / 'escape').unlink()
        os.mkfifo(tree / 'owned-fifo')
        with self.assertRaises(ValueError):
            admin.protect_tree(tree)

    def test_absolute_intree_sdk_link_is_not_treated_as_relative(self):
        tree = self.base / 'sdk-tree'
        tree.mkdir()
        target = tree / 'owned-library'
        target.write_bytes(b'owned fixture')
        (tree / 'absolute-link').symlink_to(target)
        with self.assertRaises(ValueError):
            admin.protect_tree(tree)

    def test_source_avd_with_open_file_is_not_cloned(self):
        self.staged_state()
        source = self.owned_avd()
        self.subprocess.return_value = subprocess.CompletedProcess(
            ['/usr/sbin/lsof'], 0, stdout='123\n', stderr='')
        with self.assertRaises(RuntimeError):
            admin.cold_clone(source)
        self.assertFalse(any(call.args[0] == '/bin/cp' for call in self.command.call_args_list))

    def test_copied_config_symlink_cannot_modify_external_canary(self):
        self.staged_state()
        source = self.owned_avd()
        external = self.base / 'owned-external-config'
        external.write_bytes(b'preserve nonsensitive fixture')
        (source / 'config.ini').unlink()
        (source / 'config.ini').symlink_to(external)
        with self.assertRaises(ValueError):
            admin.cold_clone(source)
        self.assertEqual(external.read_bytes(), b'preserve nonsensitive fixture')

    def test_absolute_avd_file_path_is_not_silently_retained(self):
        self.staged_state()
        source = self.owned_avd('disk.dataPartition.path=/Users/owned-fixture/fake.img\n')
        with self.assertRaises(ValueError):
            admin.cold_clone(source)
        self.assertFalse(json.loads((self.root / 'candidate-layout.json').read_bytes())['avd_cloned'])

    def test_parent_traversal_avd_file_path_cannot_escape_candidate(self):
        self.staged_state()
        source = self.owned_avd('disk.dataPartition.path=../../../../../owned-outside.img\n')
        with self.assertRaises(ValueError):
            admin.cold_clone(source)

    def test_journal_vm_identity_cannot_choose_root_or_an_external_home(self):
        for params in ({'uid': 0, 'gid': 0}, {'home': self.base / 'owned-external-home'}):
            self.staged_state(**params)
            source = self.base / ('source-avd-' + str(len(list(self.base.iterdir()))))
            source.mkdir()
            (source / 'config.ini').write_text('disk.dataPartition.path=<temp>\n')
            self.command.reset_mock()
            with self.assertRaises(ValueError):
                admin.cold_clone(source)
            self.command.assert_not_called()

    def test_derived_hardware_config_does_not_keep_old_personal_paths(self):
        self.staged_state()
        source = self.owned_avd()
        (source / 'hardware-qemu.ini').write_text('disk.dataPartition.path=/Users/owned-fixture/fake.img\n')
        result = admin.cold_clone(source)
        self.assertTrue(result['avd_cloned'])
        target = self.root / 'homes/vm/.android/avd' / (admin.AVD_NAME + '.avd')
        self.assertFalse((target / 'hardware-qemu.ini').exists())
        self.assertTrue((source / 'hardware-qemu.ini').exists())
        config = (target / 'config.ini').read_text()
        self.assertIn('hw.cpu.ncore=6\n', config)
        self.assertIn('hw.ramSize=16384\n', config)
        self.assertIn('hw.audioInput=no\n', config)
        self.assertIn('hw.camera.back=none\n', config)
        self.assertIn('hw.camera.front=none\n', config)


if __name__ == '__main__':
    unittest.main()
