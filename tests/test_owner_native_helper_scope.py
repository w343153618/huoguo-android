"""Synthetic Android metadata and actual host driver pipes; no phone mutation."""
from pathlib import Path
import os
import shlex
import stat
import sys
import unittest
from unittest.mock import patch

from scripts.probes import owner_native_helper_scope as scope
from scripts.probes.owner_native_gateway_environment import select
from scripts.probes.owner_native_gateway_probes import Commands, ProbeError
from scripts.probes import owner_native_gateway_coordinator as coordinator
from tests import test_owner_native_gateway_coordinator as driver_fixtures
from tests.test_owner_native_phone_probes import window


STAGE = '/data/local/tmp/huoguo-native-helper-' + 'a' * 24
HELPER_PATH = '/data/app/~~fixture/owned_helper/base.apk'
HOME = Path('/inert/home')
ADB = HOME / 'Library/Android/sdk/platform-tools/adb'
ENV = select(HOME, ADB, Path('/private/inert/state'), Path('/private/inert/evidence'),
    dict(DIRECT_AUTH_FILE='/inert/auth', DIRECT_CERT='/inert/cert', DIRECT_KEY='/inert/key', DIRECT_VIDEO_BACKEND='videotoolbox'))
DIR = scope.Node(1, 42, 2000, 2000, stat.S_IFDIR | 0o700, 2, 4096)
FILE = scope.Node(1, 43, 2000, 2000, stat.S_IFREG | 0o600, 1, 86419)
INSTALLED = scope.Node(1, 55, 1000, 1000, stat.S_IFREG | 0o644, 1, 86419)
DIGEST = window().plan.helper_sha256


def node(tag, value):
    return tag + ' ' + ' '.join(format(v, 'x') if i == 4 else str(v) for i, v in enumerate(vars(value).values()))


def receipt(*, directory=DIR, apk=FILE, sha=DIGEST):
    if directory is None: return 'HGHS1\nabsent\nEND\n'
    rows = ['HGHS1', node('dir', directory)]
    if apk is None: rows += ['empty']
    else:
        rows += ['entry owned.apk', node('file', apk), 'sha ' + sha, node('file_end', apk), 'entry_end owned.apk']
    return '\n'.join(rows + [node('dir_end', directory), 'END']) + '\n'


class Reads:
    def __init__(self):
        self.started = self.reaped = 0; self.pending = []; self.calls = []
        self.raw = receipt(); self.missing = False; self.uid = '0'
        self.installed = INSTALLED; self.changed_inode = False; self.changed_path = False
        self.bad_sha = False; self.paths = self.stats = 0

    def run(self, argv, **kw):
        self.calls.append((argv, kw)); self.started += 1; self.reaped += 1
        if self.missing: raise ProbeError('native_probe_command_unverified')
        words = argv[3:]
        if words == ('get-state',): return 'device\n'
        if words == ('shell', 'pm', 'path', '--user', '0', scope.HELPER):
            self.paths += 1
            return 'package:' + ('/data/app/changed/base.apk' if self.changed_path and self.paths > 1 else HELPER_PATH) + '\n'
        if len(words) != 2 or words[0] != 'shell': raise AssertionError('unexpected')
        root = shlex.split(shlex.split(words[1])[2])
        if root == ['id', '-u']: return self.uid + '\n'
        if root == ['sh', '-c', scope.scope_command(STAGE)]: return self.raw
        if root[:2] == ['stat', '-c']:
            self.stats += 1
            value = self.installed
            if self.changed_inode and self.stats > 1:
                value = scope.Node(value.device, 99, value.uid, value.gid, value.mode, value.links, value.size)
            return node('apk', value) + '\n'
        if root == ['sha256sum', HELPER_PATH]:
            return ('0' * 64 if self.bad_sha else DIGEST) + '  ' + HELPER_PATH + '\n'
        raise AssertionError('unexpected root command')


class ScopeChecks(unittest.TestCase):
    def make(self):
        reads = Reads()
        return scope.ScopeProbes(window(), ADB, STAGE, environment=ENV, commands=reads), reads

    def test_inert_namespace_and_environment_do_not_start_commands(self):
        with patch.object(Commands, 'run') as run:
            probe = scope.ScopeProbes(window(), ADB, STAGE, environment=ENV)
            self.assertEqual(probe.commands.env, ENV)
            for path in ('/data/local/tmp', STAGE + '/child', STAGE + ';true'):
                with self.assertRaises(ValueError): scope.ScopeProbes(window(), ADB, path, environment=ENV)
            for name in ('ADB_SERVER_SOCKET', 'PYTHONPATH', 'DIRECT_OWNER'):
                with self.assertRaises(ValueError): scope.ScopeProbes(window(), ADB, STAGE, environment=dict(ENV, **{name: 'foreign'}))
            run.assert_not_called()

    def test_absent_empty_and_exact_APK_are_distinct_snapshot_states(self):
        probe, reads = self.make()
        for raw, present, file_present in ((receipt(directory=None), False, False),
                (receipt(apk=None), True, False), (receipt(), True, True)):
            reads.raw = raw; value = probe.snapshot()
            self.assertEqual(value.directory is not None, present)
            self.assertEqual(value.apk is not None, file_present)
        self.assertEqual(reads.started, reads.reaped)
        self.assertTrue(all(kw['seconds'] <= 3 and kw['bound'] == 4096 for _, kw in reads.calls))

    def test_wrong_UID_mode_symlink_or_directory_type_refuses(self):
        for changes in ({'uid': 0}, {'gid': 0}, {'mode': stat.S_IFDIR | 0o755},
                {'mode': stat.S_IFLNK | 0o700}, {'inode': 0}, {'links': 5}):
            bad = scope.Node(**(vars(DIR) | changes))
            with self.assertRaises(ProbeError): scope.parse_snapshot(receipt(directory=bad), DIGEST)

    def test_hardlinked_or_foreign_or_oversized_APK_refuses(self):
        for changes in ({'links': 2}, {'uid': 0}, {'mode': stat.S_IFLNK | 0o600},
                {'mode': stat.S_IFREG | 0o644}, {'size': 131073}, {'device': 2}):
            bad = scope.Node(**(vars(FILE) | changes))
            with self.assertRaises(ProbeError): scope.parse_snapshot(receipt(apk=bad), DIGEST)

    def test_truncated_duplicate_foreign_nonASCII_or_unbounded_receipt_refuses(self):
        good = receipt()
        for raw in ('', good[:-5], good + 'END\n', good.replace('entry owned.apk', 'entry foreign'),
                good.replace('entry_end owned.apk', 'entry_end owned.apk\nforeign'), good + '\u00e9', 'x' * 4097,
                good.replace('dir 1 42', 'dir 1 ' + str(1 << 64)), good.replace('dir 1 42', 'dir 1 -42')):
            with self.assertRaises(ProbeError): scope.parse_snapshot(raw, DIGEST)

    def test_hash_or_inode_change_in_same_snapshot_refuses(self):
        for raw in (receipt(sha='0'*64), receipt().replace('file_end 1 43', 'file_end 1 44'),
                receipt().replace('dir_end 1 42', 'dir_end 1 44'),
                receipt(apk=None).replace('dir_end 1 42', 'dir_end 1 44')):
            with self.assertRaises(ProbeError): scope.parse_snapshot(raw, DIGEST)

    def test_captured_scope_identity_preserved_across_push_but_not_replacement(self):
        first = scope.parse_snapshot(receipt(apk=None), DIGEST)
        last = scope.parse_snapshot(receipt(), DIGEST)
        self.assertTrue(scope.same_scope(first, last, file_required=False))
        for changed in (scope.Snapshot(scope.Node(**(vars(DIR) | {'inode': 99})), FILE, DIGEST),
                scope.Snapshot(None, None, None)):
            with self.assertRaises(ProbeError): scope.same_scope(first, changed, file_required=False)
        with self.assertRaises(ProbeError): scope.same_scope(last, first, file_required=True)
        with self.assertRaises(ValueError): scope.same_scope({}, last, file_required=True)

    def test_missing_device_root_unknown_and_pending_client_never_give_receipt(self):
        for change in ('missing', 'uid', 'pending'):
            probe, reads = self.make()
            setattr(reads, change, True if change == 'missing' else ('2000' if change == 'uid' else [object()]))
            with self.assertRaises(ProbeError): probe.snapshot()
            self.assertIsNone(probe.last_scope)
            if change == 'missing': self.assertEqual(len(reads.calls), 1)

    def test_installed_metadata_hash_path_and_inode_are_bracketed_not_ownership(self):
        probe, reads = self.make(); result = probe.installed()
        self.assertEqual(result, scope.InstalledHelper(HELPER_PATH, INSTALLED, DIGEST))
        self.assertEqual(reads.paths, 2); self.assertEqual(reads.stats, 2)
        self.assertFalse(hasattr(result, 'permission'))
        for change in ('bad_sha', 'changed_inode', 'changed_path'):
            probe, reads = self.make(); setattr(reads, change, True)
            with self.assertRaises(ProbeError): probe.installed()
            self.assertIsNone(probe.last_installed)

    def test_foreign_installed_UID_hardlink_or_symlink_refuses_before_hash(self):
        for change in ({'uid': 2000}, {'links': 2}, {'mode': stat.S_IFLNK | 0o644}):
            probe, reads = self.make(); reads.installed = scope.Node(**(vars(INSTALLED) | change))
            with self.assertRaises(ProbeError): probe.installed()
            self.assertFalse(any('sha256sum' in shlex.join(args) for args, _ in reads.calls))

    def test_generated_read_commands_are_fixed_and_do_not_mutate_or_dereference_explicitly(self):
        probe, reads = self.make(); probe.snapshot(); probe.installed()
        for argv, _ in reads.calls:
            self.assertEqual(argv[:3], (str(ADB), '-s', scope.PHONE))
            words = shlex.join(argv)
            for forbidden in ('rm ', 'mkdir ', 'chmod ', 'install ', 'uninstall ', 'force-stop', 'udp-test-login', 'stat -L'):
                self.assertNotIn(forbidden, words)
        program = scope.scope_command(STAGE)
        self.assertLess(program.index('[ ! -L'), program.index('ls -A'))
        self.assertGreater(program.index('sha256sum'), program.index('[ -f'))

    def test_expired_read_budget_never_reuses_old_scope(self):
        probe, reads = self.make(); probe.last_scope = scope.parse_snapshot(receipt(), DIGEST)
        with patch.object(scope.time, 'monotonic', side_effect=(1, 20)):
            with self.assertRaisesRegex(ProbeError, 'budget_expired'): probe.snapshot()
        self.assertIsNone(probe.last_scope); self.assertEqual(reads.calls, [])

    def test_read_runner_copies_explicit_environment_for_actual_host_child(self):
        env = {'NATIVE_HELPER_TEST': 'owned'}; commands = Commands(env=env); env['NATIVE_HELPER_TEST'] = 'changed'
        raw = commands.run((sys.executable, '-I', '-c', 'import os;print(os.getenv("NATIVE_HELPER_TEST"));print(os.getenv("ADB_SERVER_SOCKET"))'))
        self.assertEqual(raw, 'owned\nNone\n'); self.assertEqual(commands.started, commands.reaped)

    def test_fixed_remote_read_program_passes_actual_host_shell_syntax_only(self):
        commands = Commands(env={})
        self.assertEqual(commands.run(('/bin/sh', '-n', '-c', scope.scope_command(STAGE))), '')
        self.assertEqual(commands.started, commands.reaped)


class HeldDriverChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = driver_fixtures.CoordinatorChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def make(self, program):
        item, sock, events = self.fixture.make()
        with patch.object(coordinator.Entries, 'argv', side_effect=lambda w, k: self.fixture.fixture_argv(k, driver=program)):
            item.start(env=dict(os.environ)); item.start_driver(env=dict(os.environ))
        return item, sock, events

    def test_JSON_PID_exit_or_inert_coordinator_cannot_establish_held_child(self):
        item, sock, _ = self.fixture.make()
        for value in ({'pid': 1, 'exit': 0}, item):
            with self.assertRaises(ProbeError): scope.held_driver_completed(value)
        self.assertFalse(sock.closed)

    def test_still_running_actual_driver_blocks_gate_without_signals_or_release(self):
        item, sock, _ = self.make('import time;time.sleep(.2)')
        with self.assertRaisesRegex(ProbeError, 'exit_unknown'): scope.held_driver_completed(item)
        self.assertFalse(sock.closed); self.assertIsNone(item.driver.poll())
        self.assertEqual(item.wait_driver(2), 0)

    def test_actual_failure_exit_EOF_retained_separately_from_remote_cleanup(self):
        item, sock, _ = self.make('import sys;sys.exit(2)')
        self.assertEqual(item.wait_driver(2), 2)
        result = scope.held_driver_completed(item)
        self.assertEqual(result['actual_driver_exit'], 2); self.assertFalse(result['driver_success'])
        for name in ('remote_PM_or_helper_quiescence', 'scope_removal_or_uninstall_authorized', 'reservation_release_authorized'):
            self.assertFalse(result[name])
        self.assertFalse(sock.closed)

    def test_actual_driver_output_overflow_refuses_cleanup_gate(self):
        item, sock, _ = self.make('print("x"*70000)')
        self.assertEqual(item.wait_driver(2), 0)
        with self.assertRaisesRegex(ProbeError, 'EOF_unknown'): scope.held_driver_completed(item)
        self.assertFalse(sock.closed)

    def test_driver_exit_without_pipe_EOF_is_not_remote_cleanup_or_release(self):
        item, sock, _ = self.make('import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(2.3)"],stdout=sys.stdout,stderr=sys.stderr)')
        self.assertEqual(item.wait_driver(2), 0)
        with self.assertRaisesRegex(ProbeError, 'EOF_unknown'): scope.held_driver_completed(item)
        self.assertFalse(sock.closed)
        self.assertTrue(any(d.thread.is_alive() for d in item.driver_drains))


if __name__ == '__main__': unittest.main()
