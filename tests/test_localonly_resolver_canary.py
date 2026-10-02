"""Offline + owned inert subprocess tests; no DNS registration, UID drop or root."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.security import localonly_resolver_canary as canary


HELP = '\n'.join((
    'dns-sd -lo                          (Run dns-sd cmd using local only interface)',
    'dns-sd -P <Name> <Type> <Domain> <Port> <Host> <IP> [<TXT>...] (Register Proxy)',
    'dns-sd -Q <name> <rrtype> <rrclass>         (Generic query for any record type)',
    'dns-sd -t <seconds>                                      (Exit after <seconds>)'))


class PlanAndCommandTests(unittest.TestCase):
    def setUp(self):
        self.record = canary.Record('a' * 32, 'b' * 64)

    def test_default_and_prepare_only_do_not_spawn_or_change_identity(self):
        for arguments in (['canary'], ['canary', '--prepare-only']):
            with patch.object(canary.sys, 'argv', arguments), \
                 patch.object(canary, 'execute') as execute, \
                 patch.object(canary.subprocess, 'Popen') as spawn, \
                 patch.object(canary.subprocess, 'run') as run, \
                 patch.object(canary.os, 'setuid') as drop, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                canary.main()
                report = json.loads(output.getvalue())
                self.assertEqual(report['mode'], 'describe')
                self.assertFalse(report['actual_uid600_completed'])
                self.assertFalse(report['production_changed'])
                self.assertFalse(report['isolation_accepted'])
                execute.assert_not_called()
                spawn.assert_not_called()
                run.assert_not_called()
                drop.assert_not_called()

    def test_run_without_both_pins_refuses_before_execution(self):
        with patch.object(canary.sys, 'argv', ['canary', '--run']), \
             patch.object(canary, 'execute') as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                canary.main()
            execute.assert_not_called()

    def test_nonroot_refuses_before_any_artifact_or_help_read(self):
        with patch.object(canary.sys, 'platform', 'darwin'), \
             patch.object(canary.os, 'getuid', return_value=501), \
             patch.object(canary, 'trusted_file') as read, \
             patch.object(canary.subprocess, 'run') as run:
            with self.assertRaises(PermissionError):
                canary.execute('a' * 64, 'b' * 64)
            read.assert_not_called()
            run.assert_not_called()

    def test_root_source_tree_execution_still_refuses(self):
        with patch.object(canary.sys, 'platform', 'darwin'), \
             patch.object(canary.os, 'getuid', return_value=0), \
             patch.object(canary.os, 'geteuid', return_value=0), \
             patch.object(canary, 'trusted_file') as read:
            with self.assertRaisesRegex(ValueError, 'private pinned-copy'):
                canary.validate_run('a' * 64, 'b' * 64)
            read.assert_not_called()

    def test_exact_fresh_queries_have_no_marker_or_browse_or_broad_lookup(self):
        command = self.record.query_command()
        self.assertEqual(command, [canary.DNS_SD, '-lo', '-t', '3', '-Q',
                                   self.record.fullname, 'TXT', 'IN'])
        for forbidden in ('-B', '-G', '-E', '-F', '-L', '-fmc', '-includep2p', self.record.txt):
            self.assertNotIn(forbidden, command)
        sandboxed = self.record.query_command(True)
        self.assertEqual(sandboxed[:3], [canary.SANDBOX, '-f', str(canary.PROFILE)])
        self.assertEqual(sandboxed[3:], command)

    def test_proxy_registration_localonly_and_numeric_loopback_address(self):
        command = self.record.register_command()
        self.assertEqual(command[:5], [canary.DNS_SD, '-lo', '-t', '25', '-P'])
        self.assertEqual(command[7:11], ['local.', '9', 'huoguo-' + 'a' * 32 + '.local.', '127.0.0.1'])
        self.assertEqual(command[-1], self.record.txt)
        self.assertLess(len(self.record.name), 64)
        self.assertLess(len(self.record.kind.split('.')[0]) - 1, 16)

    def test_record_input_cannot_inject_names_flags_or_shell_text(self):
        for nonce, marker in (('a' * 31, 'b' * 64), ('../../', 'b' * 64),
                              ('a' * 32, '-B _ssh._tcp'), ('A' * 32, 'b' * 64)):
            with self.assertRaises(ValueError):
                canary.Record(nonce, marker)

    def test_unsupported_or_ambiguous_help_refuses_localonly(self):
        self.assertTrue(canary.localonly_help_supported(HELP))
        for invalid in (HELP.replace('-lo', '-p2p'), HELP.replace('local only', 'any'),
                        HELP.replace('<rrclass>', ''), 'some text mentions -lo'):
            self.assertFalse(canary.localonly_help_supported(invalid))

    def test_unknown_localonly_help_does_not_register_or_load_group_reader(self):
        with patch.object(canary, 'validate_run', return_value=20), \
             patch.object(canary.subprocess, 'run', return_value=SimpleNamespace(stdout=b'-lo', stderr=b'')), \
             patch.object(canary, 'prepare_group_reader') as reader, \
             patch.object(canary, 'Children') as children:
            with self.assertRaisesRegex(ValueError, 'LocalOnly semantics'):
                canary.execute('a' * 64, 'b' * 64)
            reader.assert_not_called()
            children.assert_not_called()


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.record = canary.Record('a' * 32, 'b' * 64)
        encoded = self.record.txt.encode('ascii')
        payload = bytes([len(encoded)]) + encoded
        self.answer = f'12:34:56.123 Add 2 - -1 {self.record.fullname} TXT IN {len(payload)} bytes: ' + payload.hex(' ')

    def test_exact_localonly_wire_txt_is_positive(self):
        self.assertTrue(canary.exact_answer(self.answer, self.record))
        self.assertTrue(canary.exact_answer(self.answer.replace('-1 ', '4294967295 '), self.record))

    def test_name_interface_class_type_remove_and_size_must_match(self):
        for altered in (self.answer.replace(self.record.fullname, 'other.local.'),
                        self.answer.replace('-1 ', '4 '), self.answer.replace('TXT IN', 'TXT CH'),
                        self.answer.replace('TXT IN', 'A IN'), self.answer.replace('Add', 'Rmv'),
                        self.answer.replace('72 bytes:', '71 bytes:'),
                        self.answer[:-2] + 'ff'):
            self.assertFalse(canary.exact_answer(altered, self.record), altered)

    def test_marker_echo_and_registration_text_cannot_satisfy_query(self):
        for output in (self.record.txt, ' '.join(self.record.register_command()),
                       f'Got a reply for service {self.record.fullname}: Name now registered and active\n{self.record.txt}',
                       self.answer.replace('12:34:56.123', 'UnknownTime')):
            self.assertFalse(canary.exact_answer(output, self.record))

    def test_only_explicit_query_permission_errors_count(self):
        for number in (-65555, -65570, -65571):
            self.assertEqual(canary.explicit_policy_error(f'DNSServiceQueryRecord failed {number}\n'), number)
        for output in ('DNSServiceQueryRecord failed -65544', 'Query Timed Out',
                       'DNSServiceCreateConnection failed -65570', 'Error -65570',
                       'shell: DNSServiceQueryRecord failed -65570'):
            self.assertIsNone(canary.explicit_policy_error(output))

    def test_positive_controls_required_and_timeout_never_accepted(self):
        controls = [{'exact_answer': True}, {'exact_answer': True}]
        self.assertEqual(canary.classify(controls, {'exact_answer': True}), 'localonly_delegation_observed')
        self.assertEqual(canary.classify(controls, {'policy_error': -65570}), 'scoped_localonly_query_denied')
        self.assertEqual(canary.classify(controls, {}), 'inconclusive_no_explicit_policy_result')
        self.assertEqual(canary.classify([{'exact_answer': False}], {'policy_error': -65570}),
                         'inconclusive_positive_control_failed')
        self.assertEqual(canary.classify([], {'exact_answer': True}), 'inconclusive_positive_control_failed')


class IdentityAndOwnershipTests(unittest.TestCase):
    def test_kernel_group_verification_rejects_foreign_admin_group(self):
        def read_groups(size, groups):
            if groups is None:
                return 2
            groups[0], groups[1] = 600, 80
            return 2
        with patch.object(canary.os, 'setgroups') as groups, \
             patch.object(canary.os, 'setgid') as gid, \
             patch.object(canary.os, 'setuid') as uid, \
             patch.object(canary.os, 'getuid', return_value=600), \
             patch.object(canary.os, 'geteuid', return_value=600), \
             patch.object(canary.os, 'getgid', return_value=600), \
             patch.object(canary.os, 'getegid', return_value=600), \
             patch.object(canary, '_GETGROUPS', read_groups):
            with self.assertRaisesRegex(PermissionError, 'Unexpected kernel groups'):
                canary.drop_identity(600, 600)
            groups.assert_called_once_with([])
            gid.assert_called_once_with(600)
            uid.assert_called_once_with(600)

    def test_unsafe_or_missing_group_reader_is_not_softened(self):
        with patch.object(canary.os, 'setgroups'), patch.object(canary.os, 'setgid'), \
             patch.object(canary.os, 'setuid'), patch.object(canary.os, 'getuid', return_value=600), \
             patch.object(canary.os, 'geteuid', return_value=600), patch.object(canary.os, 'getgid', return_value=600), \
             patch.object(canary.os, 'getegid', return_value=600):
            for reader in (None, lambda count, target: -1, lambda count, target: 65):
                with patch.object(canary, '_GETGROUPS', reader), self.assertRaises(PermissionError):
                    canary.drop_identity(600, 600)

    def test_parent_directory_symlink_and_nonroot_owned_artifact_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            (directory / 'real').mkdir()
            (directory / 'link').symlink_to(directory / 'real', target_is_directory=True)
            (directory / 'real' / 'file').write_text('fixture')
            # Mock only ownership so this exercises the actual O_NOFOLLOW open,
            # rather than failing early because this fixture belongs to us.
            native_fstat = os.fstat
            def root_directory_info(fd):
                info = native_fstat(fd)
                return SimpleNamespace(st_uid=0, st_mode=(info.st_mode & ~0o022),
                                       st_nlink=info.st_nlink, st_size=info.st_size)
            with patch.object(canary.os, 'fstat', side_effect=root_directory_info), \
                 self.assertRaises(OSError):
                canary.trusted_file(directory / 'link' / 'file', 100)
            with self.assertRaises(ValueError):
                canary.trusted_file(directory / 'real' / 'file', 100)

    def test_group_metadata_must_match_own_recorded_uid_gid_before_signal(self):
        children = canary.Children()
        self.addCleanup(children.selector.close)
        process = SimpleNamespace(pid=9876)
        child = canary.Child(process, 600, 600, 'fresh')
        for metadata in (b'9876 9876 0 0 0 0\n', b'9876 4321 600 600 600 600\n',
                         b'9876 9876 600 600 600\n', b'bad\n', b''):
            with patch.object(children, 'group_exists', return_value=True), \
                 patch.object(canary.subprocess, 'run', return_value=SimpleNamespace(stdout=metadata, stderr=b'', returncode=0)), \
                 patch.object(canary.os, 'killpg') as kill:
                with self.assertRaises(ValueError):
                    children.verified_signal(child, signal.SIGTERM)
                kill.assert_not_called()


class OwnedProcessTests(unittest.TestCase):
    """Only inert current-user subprocesses; drop_identity always mocked out."""
    def spawn_owned(self, children, code):
        with patch.object(canary, 'drop_identity') as identity:
            child = children.spawn([sys.executable, '-c', code], os.getuid(), os.getgid(), 'owned_fixture')
        return child

    def test_owned_output_and_group_cleanup(self):
        children = canary.Children()
        try:
            child = self.spawn_owned(children, 'import time; print("fresh-marker", flush=True); time.sleep(20)')
            self.assertTrue(children.wait(child, 2, lambda output: 'fresh-marker' in output))
        finally:
            report = children.cleanup(grace=0.2)
        self.assertTrue(report['recorded_groups_gone'])
        self.assertFalse(report['errors'])
        self.assertFalse(report['detached_or_launchd_checked'])
        self.assertFalse(report['record_deregistration_independently_verified'])
        self.assertIsNotNone(child.process.poll())

    def test_owned_output_budget_overflow_is_not_silently_truncated(self):
        children = canary.Children()
        try:
            child = self.spawn_owned(children, 'import os,time; os.write(1,b"x"*70000); time.sleep(20)')
            with self.assertRaisesRegex(ValueError, 'output exceeded'):
                children.wait(child, 3)
        finally:
            report = children.cleanup(grace=0.2)
        self.assertTrue(report['recorded_groups_gone'])

    def test_owned_term_ignoring_child_escalates_to_kill(self):
        children = canary.Children()
        try:
            child = self.spawn_owned(children, 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(20)')
            self.assertTrue(children.wait(child, 2, lambda output: 'ready' in output))
        finally:
            report = children.cleanup(grace=0.15)
        self.assertTrue(report['recorded_groups_gone'])
        self.assertFalse(report['errors'])
        self.assertEqual(child.process.returncode, -signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
