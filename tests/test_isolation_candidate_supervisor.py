"""Candidate supervisor contracts with owned files and mocked process/socket IO.

No root authentication, real account/port53 bind, VM launch, PF, service or
credential operation occurs. These tests do not constitute candidate boot.
"""
import json
import errno
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.security import isolation_candidate_supervisor as supervisor


def fixture_plan():
    return {'identities': {role: {'uid': uid, 'gid': uid, 'home': '/owned/' + role}
                            for role, (_, uid) in supervisor.ROLES.items()},
            'environments': {'vm': {'HOME': '/owned/vm'}, 'egress': {'HOME': '/owned/egress'}},
            'commands': {'web': ['OWNED-WEB'], 'dns': ['OWNED-DNS', '<audited-ipv4-udp-fd>',
                         '<audited-ipv6-udp-fd>'], 'guest': ['OWNED-GUEST']}}


def cleanup_owned_group_fixture(leader, *, group_cleanup_verified, primary_error):
    """Never re-probe a verified group; always close the owned stdout pipe."""
    cleanup_error = None
    try:
        if not group_cleanup_verified:
            supervisor._signal_recorded_group(leader.pid, os.getuid(), signal.SIGKILL)
            records = [(leader.pid, os.getuid(), leader)]
            if supervisor._wait_recorded_groups(records, 2):
                raise supervisor.Refused('candidate_group_cleanup_unverified')
        leader.wait(timeout=2)
    except BaseException as error:
        cleanup_error = error
    finally:
        try:
            if leader.stdout is not None:
                leader.stdout.close()
        except BaseException as error:
            cleanup_error = cleanup_error or error
    if cleanup_error is not None:
        if primary_error is None:
            raise cleanup_error
        primary_error.owned_fixture_cleanup_failed = True
        if hasattr(primary_error, 'add_note'):
            primary_error.add_note('Owned fixture cleanup also failed; original failure retained and stdout closed.')


class SupervisorFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owned-supervisor-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def test_regular_file_rejects_symlink_hardlink_fifo_and_size(self):
        owned = self.base/'owned'
        owned.write_bytes(b'owned fixture')
        self.assertEqual(supervisor._read_regular(owned, owners={os.getuid()}), b'owned fixture')
        with self.assertRaises(supervisor.Refused):
            supervisor._read_regular(owned, owners={os.getuid()}, limit=2)
        symlink = self.base/'link'; symlink.symlink_to(owned)
        fifo = self.base/'fifo'; os.mkfifo(fifo)
        for path in (symlink, fifo):
            with self.assertRaises((OSError, supervisor.Refused)):
                supervisor._read_regular(path, owners={os.getuid()})
        hardlink = self.base/'hard'; hardlink.hardlink_to(owned)
        with self.assertRaises(supervisor.Refused):
            supervisor._read_regular(owned, owners={os.getuid()})

    def test_inventory_digest_changes_when_sdk_bytes_change(self):
        sdk = self.base/'sdk'; sdk.mkdir()
        binary = sdk/'emulator'; binary.write_bytes(b'owned SDK fixture A')
        before = supervisor.inventory(sdk, owners={os.getuid()})
        binary.write_bytes(b'owned SDK fixture B')
        after = supervisor.inventory(sdk, owners={os.getuid()})
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertEqual(before['entries'], 2)

    def test_internal_relative_runtime_link_pinned_external_link_rejected(self):
        runtime = self.base/'runtime'; runtime.mkdir()
        (runtime/'file').write_bytes(b'owned runtime fixture')
        (runtime/'internal').symlink_to('file')
        self.assertEqual(supervisor.inventory(runtime, owners={os.getuid()})['entries'], 3)
        outside = self.base/'outside'; outside.write_bytes(b'owned outside fixture')
        (runtime/'external').symlink_to('../outside')
        with self.assertRaisesRegex(supervisor.Refused, 'out_of_tree'):
            supervisor.inventory(runtime, owners={os.getuid()})

    def test_immutable_input_owner_and_write_permissions_enforced(self):
        for uid, mode in ((602, stat.S_IFREG | 0o644), (0, stat.S_IFREG | 0o666),
                          (0, stat.S_IFREG | 0o664)):
            with self.subTest(uid=uid, mode=mode), self.assertRaises(supervisor.Refused):
                supervisor._ownership(SimpleNamespace(st_uid=uid, st_mode=mode), {0})
        supervisor._ownership(SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644), {0})

    def test_profile_write_is_exclusive_and_conflicting_existing_file_preserved(self):
        root = self.base/'root'; (root/'profiles').mkdir(parents=True)
        plan = {'egress_profile': 'OWNED PROFILE\n',
                'pins': {'egress_profile_sha256': supervisor.digest(b'OWNED PROFILE\n')}}
        with patch.object(supervisor, 'ROOT', root), patch.object(supervisor, 'TRUSTED_ROOT_UID', os.getuid()):
            supervisor._materialize_profile(plan)
            supervisor._materialize_profile(plan)
            file = root/'profiles/egress.sb'
            file.write_bytes(b'preserve owned differing fixture')
            with self.assertRaisesRegex(supervisor.Refused, 'existing_guard_profile_differs'):
                supervisor._materialize_profile(plan)
        self.assertEqual(file.read_bytes(), b'preserve owned differing fixture')

    def test_log_is_exclusive_append_0600_and_does_not_follow_an_existing_leaf(self):
        logs = self.base/'logs'; logs.mkdir(mode=0o700)
        real_fstat = os.fstat
        def owned_info(fd):
            info = real_fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                                   st_uid=602, st_gid=602)
        def parent_fd(role):
            return os.open(logs, os.O_RDONLY | os.O_DIRECTORY)
        with patch.object(supervisor.os, 'geteuid', return_value=0), \
                patch.object(supervisor, '_open_role_log_directory', side_effect=parent_fd), \
                patch.object(supervisor.secrets, 'token_hex', return_value='ownednonce'), \
                patch.object(supervisor.os, 'fchown') as chown, \
                patch.object(supervisor.os, 'fstat', side_effect=owned_info):
            fd, path = supervisor.create_role_log('egress', 'web')
            try:
                self.assertTrue(path.endswith('/homes/egress/logs/candidate-web-ownednonce.log'))
                self.assertEqual(stat.S_IMODE(real_fstat(fd).st_mode), 0o600)
                chown.assert_called_once_with(fd, 602, 602)
                os.write(fd, b'owned diagnostic fixture')
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b'-appended')
            finally:
                os.close(fd)
            with self.assertRaises(FileExistsError):
                supervisor.create_role_log('egress', 'web')
            file = logs/'candidate-web-ownednonce.log'
            self.assertEqual(file.read_bytes(), b'owned diagnostic fixture-appended')
            file.unlink()
            outside = self.base/'outside-log'; outside.write_bytes(b'preserve outside')
            file.symlink_to(outside)
            with self.assertRaises(OSError):
                supervisor.create_role_log('egress', 'web')
            self.assertEqual(outside.read_bytes(), b'preserve outside')

    def test_log_directory_walk_rejects_service_owned_symlink_parent(self):
        root = self.base/'root'
        (root/'homes').mkdir(parents=True)
        outside = self.base/'outside'; (outside/'logs').mkdir(parents=True)
        (root/'homes/egress').symlink_to(outside, target_is_directory=True)
        with patch.object(supervisor, 'ROOT', root), patch.object(supervisor, '_ownership'), \
                self.assertRaises(OSError):
            supervisor._open_role_log_directory('egress')

    def test_log_privilege_and_role_are_fixed(self):
        for role, key in (('media', 'guest'), ('egress', '../outside')):
            with patch.object(supervisor.os, 'geteuid', return_value=0), \
                    self.assertRaises(supervisor.Refused):
                supervisor.create_role_log(role, key)
        with patch.object(supervisor.os, 'geteuid', return_value=501), \
                self.assertRaises(supervisor.Refused):
            supervisor.create_role_log('egress', 'web')


class SupervisorReceiptTest(unittest.TestCase):
    """IO is confined to owned fixtures; root identity/ownership are mocked."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owned-trial-receipt-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'root'; self.root.mkdir(mode=0o755)
        real_fstat = os.fstat
        def root_metadata(fd):
            info = real_fstat(fd)
            return SimpleNamespace(st_uid=0, st_gid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)
        for patcher in (patch.object(supervisor, 'ROOT', self.root),
                        patch.object(supervisor.os, 'getuid', return_value=0),
                        patch.object(supervisor.os, 'geteuid', return_value=0),
                        patch.object(supervisor.os, 'fstat', side_effect=root_metadata),
                        patch.object(supervisor.os, 'fchown'),
                        patch.object(supervisor.secrets, 'token_hex', return_value='a'*24)):
            patcher.start(); self.addCleanup(patcher.stop)

    def test_new_root_ordinary_0644_receipt_is_durable_whitelisted_jsonl(self):
        with patch.object(supervisor.os, 'fsync', wraps=os.fsync) as sync:
            receipt = supervisor.TrialReceipt(); self.addCleanup(receipt.close)
            self.assertFalse(os.get_inheritable(receipt.fd))
            receipt.record('started', cleanup='not_started')
            log = str(self.root/'homes/vm/logs'/('candidate-guest-' + 'b'*24 + '.log'))
            receipt.record('role_started', key='guest', pid=123, log_path=log)
            receipt.record('result', reason='candidate_completed', cleanup='stop_returned')
            self.assertEqual(sync.call_count, 4)
        file = Path(receipt.path)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(file.parent.stat().st_mode), 0o755)
        records = [json.loads(line) for line in file.read_text().splitlines()]
        self.assertEqual([row['sequence'] for row in records], [1, 2, 3])
        self.assertEqual(records[1], {'schema': 1, 'sequence': 2, 'phase': 'role_started',
                                     'role': 'guest', 'pid': 123, 'uid': 600, 'private_log_path': log})
        self.assertEqual(records[-1]['cleanup'], 'stop_returned')
        receipt.close(); receipt.close()
        with self.assertRaisesRegex(supervisor.Refused, 'receipt_event_not_fixed'):
            receipt.record('started')

    def test_existing_leaf_never_overwritten_and_symlink_never_followed(self):
        receipt = supervisor.TrialReceipt(); receipt.close()
        file = Path(receipt.path); file.write_text('preserved owned receipt')
        with self.assertRaises(FileExistsError):
            supervisor.TrialReceipt()
        self.assertEqual(file.read_text(), 'preserved owned receipt')
        file.unlink()
        outside = self.root/'outside'; outside.write_text('preserved outside fixture')
        file.symlink_to(outside)
        with self.assertRaises(OSError):
            supervisor.TrialReceipt()
        self.assertEqual(outside.read_text(), 'preserved outside fixture')

    def test_reports_parent_symlink_or_unsafe_permissions_refused(self):
        outside = self.root/'outside'; outside.mkdir()
        reports = self.root/'reports'; reports.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            supervisor.TrialReceipt()
        reports.unlink(); reports.mkdir(mode=0o700)
        with self.assertRaisesRegex(supervisor.Refused, 'receipt_directory_not_0755'):
            supervisor.TrialReceipt()
        reports.chmod(0o777)
        with self.assertRaisesRegex(supervisor.Refused, 'world_write'):
            supervisor.TrialReceipt()
        self.assertEqual(list(reports.iterdir()), [])

    def test_privilege_parent_owner_and_file_metadata_fail_closed(self):
        with patch.object(supervisor.os, 'getuid', return_value=501), \
                patch.object(supervisor, '_open_receipt_directory') as directory:
            with self.assertRaisesRegex(supervisor.Refused, 'root_trial_receipt_required'):
                supervisor.TrialReceipt()
        directory.assert_not_called()
        with patch.object(supervisor.os, 'fstat', return_value=SimpleNamespace(st_uid=602, st_mode=stat.S_IFDIR | 0o755)):
            with self.assertRaisesRegex(supervisor.Refused, 'unexpected_owner'):
                supervisor.TrialReceipt()
        reports = self.root/'reports'; reports.mkdir(mode=0o755, exist_ok=True)
        parent = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
        with patch.object(supervisor, '_open_receipt_directory', return_value=parent), \
                patch.object(supervisor.os, 'fstat', return_value=SimpleNamespace(st_uid=602, st_gid=602,
                                         st_mode=stat.S_IFREG | 0o644, st_nlink=1)):
            with self.assertRaisesRegex(supervisor.Refused, 'receipt_not_root_ordinary'):
                supervisor.TrialReceipt()

    def test_no_arbitrary_reason_path_or_unbounded_data(self):
        receipt = supervisor.TrialReceipt(); self.addCleanup(receipt.close)
        for phase, kwargs in (('TOKEN', {}), ('failed', {'reason': 'SECRET exception original'}),
                              ('result', {'cleanup': 'SECRET'}),
                              ('role_started', {'key': 'untrusted', 'pid': 1}),
                              ('role_started', {'key': 'guest', 'pid': True}),
                              ('role_started', {'key': 'guest', 'pid': 123, 'log_path': '/Users/private/token'}),
                              ('result', {'pid': 123})):
            with self.subTest(phase=phase, kwargs=kwargs), self.assertRaises(supervisor.Refused):
                receipt.record(phase, **kwargs)
        self.assertEqual(Path(receipt.path).read_bytes(), b'')
        for _ in range(32):
            receipt.record('started')
        with self.assertRaisesRegex(supervisor.Refused, 'receipt_event_not_fixed'):
            receipt.record('started')
        self.assertNotIn('SECRET', Path(receipt.path).read_text())

    def test_failure_reason_does_not_contain_original_exception_text(self):
        for error, expected in ((supervisor.Refused('formal_session_check_failed'), 'formal_session_check_failed'),
                                (supervisor.Refused('SECRET unapproved refusal'), 'candidate_refused'),
                                (OSError('SECRET sensitive IO text'), 'candidate_os_error'),
                                (RuntimeError('SECRET'), 'candidate_internal_error'),
                                (KeyboardInterrupt('SECRET'), 'candidate_interrupted')):
            self.assertEqual(supervisor._receipt_failure_reason(error), expected)

    def test_receipt_accepts_only_numeric_group_error_metadata_and_fixed_stage(self):
        receipt = supervisor.TrialReceipt(); self.addCleanup(receipt.close)
        for metadata in ({'errno': True, 'pgid': 123, 'stage': 'probe'},
                         {'errno': 1, 'pgid': 0, 'stage': 'probe'},
                         {'errno': 1, 'pgid': 123, 'stage': 'SECRET'},
                         {'errno': 1, 'pgid': 123, 'stage': 'probe', 'argv': 'SECRET'},
                         {'errno': None, 'pgid': 123, 'stage': 'probe'}):
            with self.assertRaisesRegex(supervisor.Refused, 'receipt_group_failure_not_fixed'):
                receipt.record('failed', group_failure=metadata)
        metadata = {'errno': errno.EPERM, 'pgid': 123, 'stage': 'probe'}
        receipt.record('failed', reason='candidate_group_probe_failed', group_failure=metadata)
        record = json.loads(Path(receipt.path).read_text())
        self.assertEqual(record['group_failure'], metadata)
        self.assertNotIn('SECRET', Path(receipt.path).read_text())


class SupervisorProcessTest(unittest.TestCase):
    def test_spawn_drops_uid_groups_and_closes_all_unaudited_fds(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        with patch.object(supervisor.subprocess, 'Popen') as popen, \
                patch.object(supervisor.os, 'getuid', return_value=0), \
                patch.object(supervisor.os, 'geteuid', return_value=0), \
                patch.object(supervisor, 'assert_formal_idle'), \
                patch.object(supervisor, '_assert_runtime_identity') as identity, \
                patch.object(supervisor, 'create_role_log', return_value=(99, '/owned/private-log')), \
                patch.object(supervisor.os, 'close') as close:
            for key, uid, fds in (('web', 602, ()), ('dns', 602, (11, 12)), ('guest', 600, ())):
                candidate.spawn(key, [key], fds)
                kwargs = popen.call_args.kwargs
                self.assertEqual((kwargs['user'], kwargs['group'], kwargs['extra_groups']), (uid, uid, []))
                self.assertTrue(kwargs['close_fds'])
                self.assertEqual(kwargs['pass_fds'], fds)
                self.assertTrue(kwargs['start_new_session'])
                self.assertEqual(kwargs['umask'], 0o077)
                self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
                self.assertEqual((kwargs['stdout'], kwargs['stderr']), (99, 99))
            self.assertEqual(close.call_count, 3)
            self.assertEqual([call.args[0] for call in identity.call_args_list], ['egress', 'egress', 'vm'])

    def test_changed_identity_blocks_spawn_before_log_or_child(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        with patch.object(supervisor.os, 'getuid', return_value=0), \
                patch.object(supervisor.os, 'geteuid', return_value=0), \
                patch.object(supervisor, 'assert_formal_idle'), \
                patch.object(supervisor, '_assert_runtime_identity', side_effect=supervisor.Refused('service_is_administrator')), \
                patch.object(supervisor, 'create_role_log') as log, \
                patch.object(supervisor.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(supervisor.Refused, 'service_is_administrator'):
                candidate.spawn('guest', ['OWNED'])
        log.assert_not_called(); popen.assert_not_called()

    def test_spawn_records_fixed_child_identity_without_inheriting_receipt_fd(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        candidate.receipt = Mock(fd=88)
        child = Mock(pid=123)
        path = str(supervisor.ROOT/'homes/egress/logs'/('candidate-dns-'+'b'*24+'.log'))
        with patch.object(supervisor.subprocess, 'Popen', return_value=child) as popen, \
                patch.object(supervisor.os, 'getuid', return_value=0), \
                patch.object(supervisor.os, 'geteuid', return_value=0), \
                patch.object(supervisor, 'assert_formal_idle'), \
                patch.object(supervisor, '_assert_runtime_identity'), \
                patch.object(supervisor, 'create_role_log', return_value=(99, path)), \
                patch.object(supervisor.os, 'close'):
            candidate.spawn('dns', ['OWNED'], (11, 12))
        candidate.receipt.record.assert_called_once_with('role_started', key='dns', pid=123, log_path=path)
        self.assertEqual(popen.call_args.kwargs['pass_fds'], (11, 12))
        self.assertNotIn(88, popen.call_args.kwargs['pass_fds'])

    def test_receipt_persists_prestart_refusal_and_cleanup_result_without_error_text(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        receipt = Mock(path='/owned/nonsecret-receipt')
        with patch.object(supervisor, 'TrialReceipt', return_value=receipt), \
                patch.object(candidate, 'check_formal', side_effect=supervisor.Refused('formal_session_active_stop_candidate')), \
                patch.object(candidate, 'stop') as stop, \
                patch.object(candidate, 'spawn') as spawn:
            with self.assertRaisesRegex(supervisor.Refused, 'formal_session_active') as caught:
                candidate.run(1)
        spawn.assert_not_called(); stop.assert_called_once(); receipt.close.assert_called_once()
        self.assertEqual(caught.exception.trial_receipt_path, '/owned/nonsecret-receipt')
        self.assertEqual([call.args[0] for call in receipt.record.call_args_list],
                         ['started', 'failed', 'cleanup_started', 'cleanup_finished', 'result'])
        self.assertEqual(receipt.record.call_args.kwargs,
                         {'reason': 'formal_session_active_stop_candidate', 'cleanup': 'stop_returned'})

    def test_completed_and_failed_cleanup_receipts_are_distinct(self):
        for cleanup_error in (None, OSError('SECRET cleanup original error')):
            candidate = supervisor.CandidateSupervisor(fixture_plan())
            receipt = Mock(path='/owned/nonsecret-receipt')
            web, dns, guest = Mock(pid=111), Mock(pid=112), Mock(pid=113)
            web.poll.return_value = dns.poll.return_value = None
            ipv4, ipv6 = Mock(), Mock(); ipv4.fileno.return_value = 11; ipv6.fileno.return_value = 12
            with patch.object(supervisor, 'TrialReceipt', return_value=receipt), \
                    patch.object(candidate, 'check_formal'), \
                    patch.object(supervisor, '_materialize_profile'), \
                    patch.object(supervisor, '_assert_listener_owner', return_value=True), \
                    patch.object(supervisor, 'bind_dns_pair', return_value=(ipv4, ipv6)), \
                    patch.object(candidate, 'spawn', side_effect=[web, dns, guest]), \
                    patch.object(supervisor.time, 'monotonic', side_effect=[0, 0, 0, 2]), \
                    patch.object(candidate, 'stop', side_effect=cleanup_error) as stop:
                if cleanup_error is None:
                    result = candidate.run(1)
                    self.assertFalse(result['guest_boot_accepted'])
                    self.assertEqual(result['trial_receipt_path'], '/owned/nonsecret-receipt')
                else:
                    with self.assertRaises(OSError):
                        candidate.run(1)
            stop.assert_called_once(); receipt.close.assert_called_once()
            self.assertEqual(receipt.record.call_args.kwargs,
                             {'reason': 'candidate_completed' if cleanup_error is None else 'candidate_cleanup_failed',
                              'cleanup': 'stop_returned' if cleanup_error is None else 'stop_failed'})
            self.assertNotIn('SECRET', repr(receipt.record.call_args_list))

    def test_receipt_write_failure_cannot_skip_candidate_stop(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        receipt = Mock(path='/owned/nonsecret-receipt')
        receipt.record.side_effect = OSError('SECRET report disk failure')
        with patch.object(supervisor, 'TrialReceipt', return_value=receipt), \
                patch.object(candidate, 'stop') as stop, \
                patch.object(candidate, 'spawn') as spawn:
            with self.assertRaises(OSError):
                candidate.run(1)
        spawn.assert_not_called(); stop.assert_called_once(); receipt.close.assert_called_once()

    def test_cleanup_receipt_retains_numeric_probe_metadata_without_exception_text(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        receipt = Mock(path='/owned/nonsecret-receipt')
        failure = supervisor._group_os_failure('candidate_group_probe_failed', 123, 'probe',
                                              PermissionError(errno.EPERM, 'SECRET OS text'))
        web, dns, guest = Mock(pid=111), Mock(pid=112), Mock(pid=113)
        web.poll.return_value = dns.poll.return_value = None
        ipv4, ipv6 = Mock(), Mock(); ipv4.fileno.return_value = 11; ipv6.fileno.return_value = 12
        with patch.object(supervisor, 'TrialReceipt', return_value=receipt), \
                patch.object(candidate, 'check_formal'), \
                patch.object(supervisor, '_materialize_profile'), \
                patch.object(supervisor, '_assert_listener_owner', return_value=True), \
                patch.object(supervisor, 'bind_dns_pair', return_value=(ipv4, ipv6)), \
                patch.object(candidate, 'spawn', side_effect=[web, dns, guest]), \
                patch.object(supervisor.time, 'monotonic', side_effect=[0, 0, 0, 2]), \
                patch.object(candidate, 'stop', side_effect=failure):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed'):
                candidate.run(1)
        expected = {'errno': errno.EPERM, 'pgid': 123, 'stage': 'probe'}
        self.assertEqual(receipt.record.call_args.kwargs,
                         {'reason': 'candidate_group_probe_failed', 'cleanup': 'stop_failed', 'group_failure': expected})
        self.assertNotIn('SECRET', repr(receipt.record.call_args_list))

    def test_fixed_limits_are_applied_without_untrusted_values(self):
        with patch.object(supervisor.resource, 'setrlimit') as limit:
            supervisor._child_limits('egress')()
        calls = {call.args[0]: call.args[1] for call in limit.call_args_list}
        self.assertEqual(calls[supervisor.resource.RLIMIT_NOFILE], (128, 128))
        self.assertEqual(calls[supervisor.resource.RLIMIT_CORE], (0, 0))
        self.assertNotIn(supervisor.resource.RLIMIT_AS, calls)

    def test_dns_bind_is_dual_family_exclusive_and_partial_failure_closes_all(self):
        ipv4, ipv6 = Mock(), Mock()
        with patch.object(supervisor.socket, 'socket', side_effect=[ipv4, ipv6]):
            self.assertEqual(supervisor.bind_dns_pair(), (ipv4, ipv6))
        ipv4.bind.assert_called_once_with(('127.0.0.1', 53))
        ipv6.bind.assert_called_once_with(('::1', 53, 0, 0))
        ipv6.setsockopt.assert_called_once_with(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind.side_effect = OSError('owned fixture conflict')
        with patch.object(supervisor.socket, 'socket', side_effect=[ipv4, ipv6]), self.assertRaises(OSError):
            supervisor.bind_dns_pair()
        ipv4.close.assert_called_once(); ipv6.close.assert_called_once()

    def test_lsof_requires_exact_child_pid_for_both_families(self):
        result = subprocess.CompletedProcess([], 0, 'p123\n', '')
        with patch.object(supervisor.subprocess, 'run', return_value=result) as run:
            self.assertTrue(supervisor._assert_listener_owner(18131, 123))
        self.assertEqual(run.call_count, 2)
        for stdout in ('p456\n', 'p123\np456\n', ''):
            with patch.object(supervisor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, '')):
                self.assertFalse(supervisor._assert_listener_owner(18131, 123))

    def test_stopping_kills_only_recorded_groups_even_when_leader_exited(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        web = Mock(pid=111); web.poll.return_value = 1
        guest = Mock(pid=222); guest.poll.return_value = None
        candidate.children = [('web', web), ('guest', guest)]
        survivors = [(111, 602, web)]
        with patch.object(supervisor, '_signal_recorded_group') as kill, \
                patch.object(supervisor, '_wait_recorded_groups', side_effect=[survivors, []]) as wait:
            candidate.stop()
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(222, 600, signal.SIGTERM), (111, 602, signal.SIGTERM), (111, 602, signal.SIGKILL)])
        self.assertEqual([call.args[1] for call in wait.call_args_list], [5.0, 2.0])

    def test_group_cleanup_not_verified_is_refused_after_kill(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        child = Mock(pid=111); child.poll.return_value = 0
        candidate.children = [('web', child)]
        records = [(111, 602, child)]
        with patch.object(supervisor, '_signal_recorded_group') as kill, \
                patch.object(supervisor, '_recorded_group_exists', return_value=True), \
                patch.object(supervisor, '_wait_recorded_groups', side_effect=[records, records]):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_cleanup_unverified'):
                candidate.stop()
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(111, 602, signal.SIGTERM), (111, 602, signal.SIGKILL)])

    def test_one_unverified_group_does_not_skip_other_recorded_group_cleanup(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        web, guest = Mock(pid=111), Mock(pid=222)
        candidate.children = [('web', web), ('guest', guest)]
        web_record = [(111, 602, web)]
        with patch.object(supervisor, '_signal_recorded_group',
                          side_effect=[supervisor.Refused('candidate_group_identity_changed'), None, None]) as kill, \
                patch.object(supervisor, '_wait_recorded_groups', side_effect=[web_record, []]):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_identity_changed'):
                candidate.stop()
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(222, 600, signal.SIGTERM), (111, 602, signal.SIGTERM), (111, 602, signal.SIGKILL)])

    def test_recorded_group_cannot_be_zero_parent_group_or_duplicate(self):
        for pids in ([0], [999], [111, 111]):
            candidate = supervisor.CandidateSupervisor(fixture_plan())
            candidate.children = [('web', Mock(pid=pid)) for pid in pids]
            with patch.object(supervisor.os, 'getpgrp', return_value=999), \
                    patch.object(supervisor, '_signal_recorded_group') as kill:
                with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_not_recorded'):
                    candidate.stop()
            kill.assert_not_called()

    def test_guard_failure_never_starts_guest_and_cleans_only_new_children(self):
        plan = fixture_plan()
        candidate = supervisor.CandidateSupervisor(plan)
        web = Mock(pid=111); web.poll.return_value = 1
        with patch.object(supervisor, 'TrialReceipt'), \
                patch.object(supervisor, '_materialize_profile'), \
                patch.object(supervisor, 'assert_formal_idle'), \
                patch.object(supervisor, '_assert_listener_owner', return_value=False), \
                patch.object(candidate, 'spawn', return_value=web) as spawn, \
                patch.object(candidate, 'stop') as stop:
            with self.assertRaisesRegex(supervisor.Refused, 'guard_web_not_owned_or_ready'):
                candidate.run(1)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args.args[0], 'web')
        stop.assert_called_once()

    def test_dns_failure_does_not_launch_guest_and_closes_passed_parent_fds(self):
        plan = fixture_plan()
        candidate = supervisor.CandidateSupervisor(plan)
        web = Mock(pid=111); web.poll.return_value = None
        dns = Mock(pid=112); dns.poll.return_value = 1
        ipv4, ipv6 = Mock(), Mock(); ipv4.fileno.return_value = 11; ipv6.fileno.return_value = 12
        with patch.object(supervisor, 'TrialReceipt'), \
                patch.object(supervisor, '_materialize_profile'), \
                patch.object(supervisor, 'assert_formal_idle'), \
                patch.object(supervisor, '_assert_listener_owner', side_effect=[True, False]), \
                patch.object(supervisor, 'bind_dns_pair', return_value=(ipv4, ipv6)), \
                patch.object(candidate, 'spawn', side_effect=[web, dns]) as spawn, \
                patch.object(candidate, 'stop') as stop:
            with self.assertRaisesRegex(supervisor.Refused, 'guard_dns_not_owned_or_ready'):
                candidate.run(1)
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(spawn.call_args.args[2], (11, 12))
        self.assertNotIn('<audited-ipv4-udp-fd>', spawn.call_args.args[1])
        ipv4.close.assert_called_once(); ipv6.close.assert_called_once(); stop.assert_called_once()

    def test_formal_session_prestart_blocks_all_candidate_mutations(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        with patch.object(supervisor, 'TrialReceipt'), \
                patch.object(supervisor, 'assert_formal_idle', side_effect=supervisor.Refused('formal_session_active_stop_candidate')), \
                patch.object(supervisor, '_materialize_profile') as profile, \
                patch.object(candidate, 'spawn') as spawn, \
                patch.object(supervisor, 'bind_dns_pair') as bind:
            with self.assertRaisesRegex(supervisor.Refused, 'formal_session_active'):
                candidate.run(1)
        profile.assert_not_called(); spawn.assert_not_called(); bind.assert_not_called()
        self.assertEqual(candidate.children, [])

    def test_formal_monitor_interval_is_fixed_and_failure_cleans_candidate(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        with patch.object(supervisor, 'assert_formal_idle') as check, \
                patch.object(supervisor.time, 'monotonic', side_effect=[0, 1.9, 2, 2]):
            candidate.check_formal(force=True)
            candidate.check_formal()
            candidate.check_formal()
        self.assertEqual(check.call_count, 2)
        ipv4, ipv6 = Mock(), Mock(); ipv4.fileno.return_value = 11; ipv6.fileno.return_value = 12
        children = [Mock(pid=111), Mock(pid=112), Mock(pid=113)]
        for child in children:
            child.poll.return_value = None
        with patch.object(supervisor, 'TrialReceipt'), \
                patch.object(supervisor, '_materialize_profile'), \
                patch.object(supervisor, '_assert_listener_owner', return_value=True), \
                patch.object(supervisor, 'bind_dns_pair', return_value=(ipv4, ipv6)), \
                patch.object(candidate, 'spawn', side_effect=children), \
                patch.object(candidate, 'check_formal', side_effect=[None, supervisor.Refused('formal_session_active_stop_candidate')]), \
                patch.object(candidate, 'stop') as stop:
            with self.assertRaisesRegex(supervisor.Refused, 'formal_session_active'):
                candidate.run(1)
        stop.assert_called_once()
        ipv4.close.assert_called_once(); ipv6.close.assert_called_once()


class SupervisorIdentityAndFormalTest(unittest.TestCase):
    def record(self):
        name, uid = supervisor.ROLES['vm']
        return {'name': name, 'uid': uid, 'gid': uid, 'home': str(supervisor.ROOT/'homes/vm')}

    def test_group_name_gid_and_nested_admin_membership_checked(self):
        record = self.record()
        account = SimpleNamespace(pw_uid=600, pw_gid=600, pw_dir=record['home'], pw_shell='/usr/bin/false')
        own = SimpleNamespace(gr_name=record['name'], gr_gid=600, gr_mem=[])
        admin = SimpleNamespace(gr_name='admin', gr_gid=80, gr_mem=[])
        with patch.object(supervisor.pwd, 'getpwnam', return_value=account), \
                patch.object(supervisor.grp, 'getgrnam', side_effect=lambda name: admin if name == 'admin' else own), \
                patch.object(supervisor.os, 'getgrouplist', return_value=[600]) as groups:
            supervisor._assert_runtime_identity('vm', record)
            groups.assert_called_once_with(record['name'], 600)
            for members in ([600, 80], [600, 0]):
                groups.return_value = members
                with self.assertRaisesRegex(supervisor.Refused, 'service_is_administrator'):
                    supervisor._assert_runtime_identity('vm', record)
            for members in ([], [601], [600, True], [600, '80']):
                groups.return_value = members
                with self.assertRaisesRegex(supervisor.Refused, 'identity_membership_check_failed'):
                    supervisor._assert_runtime_identity('vm', record)
            groups.return_value = [600]
            own.gr_gid = 601
            with self.assertRaisesRegex(supervisor.Refused, 'group_mismatch'):
                supervisor._assert_runtime_identity('vm', record)
            own.gr_gid = 600; own.gr_name = 'another_group'
            with self.assertRaisesRegex(supervisor.Refused, 'group_mismatch'):
                supervisor._assert_runtime_identity('vm', record)

    def test_group_membership_lookup_failure_is_fail_closed(self):
        record = self.record()
        account = SimpleNamespace(pw_uid=600, pw_gid=600, pw_dir=record['home'], pw_shell='/usr/bin/false')
        own = SimpleNamespace(gr_name=record['name'], gr_gid=600, gr_mem=[])
        admin = SimpleNamespace(gr_name='admin', gr_gid=80, gr_mem=[])
        with patch.object(supervisor.pwd, 'getpwnam', return_value=account), \
                patch.object(supervisor.grp, 'getgrnam', side_effect=lambda name: admin if name == 'admin' else own), \
                patch.object(supervisor.os, 'getgrouplist', side_effect=OSError('owned directory failure')):
            with self.assertRaisesRegex(supervisor.Refused, 'identity_membership_check_failed'):
                supervisor._assert_runtime_identity('vm', record)

    def test_formal_lsof_is_fixed_root_readonly_and_errors_fail_closed(self):
        with patch.object(supervisor.os, 'getuid', return_value=0), \
                patch.object(supervisor.os, 'geteuid', return_value=0), \
                patch.object(supervisor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')) as run:
            supervisor.assert_formal_idle()
            self.assertEqual(run.call_args.args[0], ['/usr/sbin/lsof', '-nP', '-a', '-iTCP:15556', '-sTCP:ESTABLISHED', '-Fp'])
            self.assertEqual(run.call_args.kwargs['timeout'], 2)
            self.assertEqual(run.call_args.kwargs['cwd'], '/')
            run.return_value = subprocess.CompletedProcess([], 0, 'p111\np222\n', '')
            with self.assertRaisesRegex(supervisor.Refused, 'formal_session_active_stop_candidate'):
                supervisor.assert_formal_idle()
            for result in (subprocess.CompletedProcess([], 0, '', ''),
                           subprocess.CompletedProcess([], 0, 'malformed\n', ''),
                           subprocess.CompletedProcess([], 1, '', 'lsof failure'),
                           subprocess.CompletedProcess([], 2, '', ''),
                           subprocess.CompletedProcess([], 0, 'p1\n'*1400, '')):
                run.return_value = result
                with self.assertRaisesRegex(supervisor.Refused, 'formal_session_check_failed'):
                    supervisor.assert_formal_idle()
            for error in (OSError('owned failure'), subprocess.TimeoutExpired('owned lsof', 2)):
                run.side_effect = error
                with self.assertRaisesRegex(supervisor.Refused, 'formal_session_check_failed'):
                    supervisor.assert_formal_idle()
        with patch.object(supervisor.os, 'getuid', return_value=501), \
                patch.object(supervisor.subprocess, 'run') as run:
            with self.assertRaisesRegex(supervisor.Refused, 'root_formal_session_check_required'):
                supervisor.assert_formal_idle()
        run.assert_not_called()


class SupervisorGroupContractTest(unittest.TestCase):
    def test_eperm_is_pending_only_inside_bounded_wait_and_never_permission_to_signal(self):
        error = PermissionError(errno.EPERM, 'SECRET original OS text')
        with patch.object(supervisor.os, 'killpg', side_effect=error):
            self.assertTrue(supervisor._recorded_group_exists(123, permission_pending=True))
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed') as caught:
                supervisor._recorded_group_exists(123)
        self.assertEqual(caught.exception.group_failure, {'errno': errno.EPERM, 'pgid': 123, 'stage': 'probe'})
        with patch.object(supervisor.os, 'killpg', side_effect=error) as kill, \
                patch.object(supervisor, '_recorded_group_members_match') as members:
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed'):
                supervisor._signal_recorded_group(123, 602, signal.SIGKILL)
        kill.assert_called_once_with(123, 0); members.assert_not_called()
        leader = Mock(pid=123); leader.poll.return_value = 0
        records = [(123, 602, leader)]
        with patch.object(supervisor.os, 'killpg', side_effect=[error, ProcessLookupError(errno.ESRCH, 'owned absence')]) as kill, \
                patch.object(supervisor.time, 'monotonic', return_value=0), \
                patch.object(supervisor.time, 'sleep'):
            self.assertEqual(supervisor._wait_recorded_groups(records, 2), [])
        self.assertEqual(kill.call_count, 2)
        with patch.object(supervisor.os, 'killpg', side_effect=error), \
                patch.object(supervisor.time, 'monotonic', side_effect=[0, 2]):
            self.assertEqual(supervisor._wait_recorded_groups(records, 2), records)

    def test_persistent_permission_error_retains_numeric_failure_after_deadline(self):
        candidate = supervisor.CandidateSupervisor(fixture_plan())
        leader = Mock(pid=123); records = [(123, 602, leader)]
        candidate.children = [('web', leader)]
        with patch.object(supervisor, '_signal_recorded_group'), \
                patch.object(supervisor, '_wait_recorded_groups', side_effect=[records, records]), \
                patch.object(supervisor.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'SECRET')):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed') as caught:
                candidate.stop()
        self.assertEqual(caught.exception.group_failure, {'errno': errno.EPERM, 'pgid': 123, 'stage': 'probe'})

    def test_fixture_finally_does_not_reprobe_success_and_always_closes_stdout(self):
        leader = Mock(pid=123)
        with patch.object(supervisor, '_signal_recorded_group') as kill, \
                patch.object(supervisor, '_wait_recorded_groups') as wait:
            cleanup_owned_group_fixture(leader, group_cleanup_verified=True, primary_error=None)
        kill.assert_not_called(); wait.assert_not_called()
        leader.wait.assert_called_once_with(timeout=2); leader.stdout.close.assert_called_once()
        for primary in (None, RuntimeError('primary fixture failure')):
            leader = Mock(pid=123)
            with patch.object(supervisor, '_signal_recorded_group', side_effect=supervisor.Refused('candidate_group_probe_failed')):
                if primary is None:
                    with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed'):
                        cleanup_owned_group_fixture(leader, group_cleanup_verified=False, primary_error=primary)
                else:
                    cleanup_owned_group_fixture(leader, group_cleanup_verified=False, primary_error=primary)
                    self.assertTrue(primary.owned_fixture_cleanup_failed)
            leader.stdout.close.assert_called_once()

    def test_group_probe_errors_fail_closed_and_absence_is_explicit(self):
        with patch.object(supervisor.os, 'killpg', return_value=None) as kill:
            self.assertTrue(supervisor._recorded_group_exists(123))
        kill.assert_called_once_with(123, 0)
        with patch.object(supervisor.os, 'killpg', side_effect=ProcessLookupError):
            self.assertFalse(supervisor._recorded_group_exists(123))
        with patch.object(supervisor.os, 'killpg', side_effect=PermissionError('SECRET')):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_probe_failed'):
                supervisor._recorded_group_exists(123)

    def test_ps_selects_only_group_numeric_fields_and_rejects_mismatched_uid(self):
        with patch.object(supervisor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ' 124 123 602\n', '')) as run:
            self.assertTrue(supervisor._recorded_group_members_match(123, 602))
            self.assertEqual(run.call_args.args[0], ['/bin/ps', '-g', '123', '-o', 'pid=,pgid=,uid='])
            self.assertEqual(run.call_args.kwargs['timeout'], 1)
            run.return_value = subprocess.CompletedProcess([], 1, '', '')
            self.assertFalse(supervisor._recorded_group_members_match(123, 602))
            run.return_value = subprocess.CompletedProcess([], 0, '124 123 501\n', '')
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_identity_changed'):
                supervisor._recorded_group_members_match(123, 602)
            for result in (subprocess.CompletedProcess([], 0, '', ''),
                           subprocess.CompletedProcess([], 0, '124 999 602\n', ''),
                           subprocess.CompletedProcess([], 0, 'argv SECRET\n', ''),
                           subprocess.CompletedProcess([], 2, '', ''),
                           subprocess.CompletedProcess([], 0, '124 123 602\n', 'SECRET'),
                           subprocess.CompletedProcess([], 0, '1'*5000+' 123 602\n', '')):
                run.return_value = result
                with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_members_unverified'):
                    supervisor._recorded_group_members_match(123, 602)
            run.side_effect = subprocess.TimeoutExpired('owned ps', 1)
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_members_unverified'):
                supervisor._recorded_group_members_match(123, 602)

    def test_unverified_group_is_never_signalled_and_disappearing_group_is_safe(self):
        with patch.object(supervisor, '_recorded_group_exists', return_value=True), \
                patch.object(supervisor, '_recorded_group_members_match', side_effect=supervisor.Refused('candidate_group_identity_changed')), \
                patch.object(supervisor.os, 'killpg') as kill:
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_identity_changed'):
                supervisor._signal_recorded_group(123, 602, signal.SIGKILL)
        kill.assert_not_called()
        with patch.object(supervisor, '_recorded_group_exists', side_effect=[True, False]), \
                patch.object(supervisor, '_recorded_group_members_match', return_value=False), \
                patch.object(supervisor.os, 'killpg') as kill:
            supervisor._signal_recorded_group(123, 602, signal.SIGKILL)
        kill.assert_not_called()
        with patch.object(supervisor, '_recorded_group_exists', return_value=True), \
                patch.object(supervisor, '_recorded_group_members_match', return_value=True), \
                patch.object(supervisor.os, 'killpg', side_effect=PermissionError('SECRET')):
            with self.assertRaisesRegex(supervisor.Refused, 'candidate_group_signal_failed'):
                supervisor._signal_recorded_group(123, 602, signal.SIGKILL)

    def test_group_remains_pending_even_after_leader_has_been_reaped(self):
        leader = Mock(pid=123); leader.poll.return_value = 0
        records = [(123, 602, leader)]
        with patch.object(supervisor, '_recorded_group_exists', return_value=True), \
                patch.object(supervisor.time, 'monotonic', side_effect=[0, 5]):
            self.assertEqual(supervisor._wait_recorded_groups(records, 5), records)
        leader.poll.assert_called_once()
        with patch.object(supervisor, '_recorded_group_exists', return_value=False), \
                patch.object(supervisor.time, 'monotonic', return_value=0):
            self.assertEqual(supervisor._wait_recorded_groups(records, 2), [])


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'Owned non-root process-group fixture requires Darwin')
class SupervisorOwnedGroupTest(unittest.TestCase):
    def test_exited_group_leader_with_term_ignoring_descendant_is_killed_and_group_gone(self):
        """One fresh owned group only; no root, VM or existing service process."""
        code = '''import os,signal,time
readfd,writefd=os.pipe()
child=os.fork()
if child==0:
 os.close(readfd);signal.signal(signal.SIGTERM,signal.SIG_IGN)
 os.write(writefd,b"ready");os.close(writefd)
 while True:time.sleep(1)
else:
 os.close(writefd);os.read(readfd,5);os.close(readfd)
 os.write(1,(str(child)+"\\n").encode());os._exit(0)
'''
        leader = subprocess.Popen([sys.executable, '-I', '-B', '-c', code],
                                  start_new_session=True, close_fds=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        group_cleanup_verified = False
        try:
            self.assertTrue(select.select([leader.stdout], [], [], 2)[0], 'owned descendant ready deadline')
            descendant = int(leader.stdout.readline())
            self.assertGreater(descendant, 1)
            leader.wait(timeout=2)
            self.assertEqual(leader.returncode, 0)
            self.assertTrue(supervisor._recorded_group_exists(leader.pid))
            candidate = supervisor.CandidateSupervisor(fixture_plan())
            candidate.children = [('guest', leader)]
            # This class-core fixture substitutes its own UID; execute_plan's
            # fixed production UID600/602 validation is never invoked or weakened.
            fixture_roles = dict(supervisor.ROLES)
            fixture_roles['vm'] = ('owned_fixture_only', os.getuid())
            with patch.object(supervisor, 'ROLES', fixture_roles):
                candidate.stop()
            group_cleanup_verified = True
            self.assertFalse(supervisor._recorded_group_exists(leader.pid))
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant, 0)
        finally:
            cleanup_owned_group_fixture(leader, group_cleanup_verified=group_cleanup_verified,
                                        primary_error=sys.exc_info()[1])


@unittest.skipUnless(sys.platform == 'darwin', 'Owned Seatbelt UDP fixture requires Darwin')
class SupervisorDnsReplyPolicyTest(unittest.TestCase):
    def test_fixed_source_reply_exception_works_for_both_families_and_fresh_socket_is_denied(self):
        """No root, port53, public DNS, guest, existing listener or production change."""
        reply_filter = '(require-all (local udp "localhost:53") (remote udp "localhost:*"))'
        profile = supervisor.guard_profile((supervisor.PYTHON_PACKAGE, supervisor.OPENSSL_PACKAGE))
        self.assertIn(reply_filter, profile)
        executable = str(supervisor.SYSTEM_PYTHON/'Versions/3.9/bin/python3.9')
        child_code = '''import json,socket,sys
listener=socket.socket(fileno=int(sys.argv[1]));listener.settimeout(2)
packet,peer=listener.recvfrom(32);out={"received_query":packet==b"owned-probe"}
try:listener.sendto(b"owned-response",peer);out["reply_errno"]=0
except OSError as error:out["reply_errno"]=error.errno
fresh=socket.socket(listener.family,socket.SOCK_DGRAM)
try:fresh.sendto(b"owned-fresh",peer);out["fresh_errno"]=0
except OSError as error:out["fresh_errno"]=error.errno
fresh.close();listener.close();print(json.dumps(out))
'''
        for family, host in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
            for allow_reply in (False, True):
                with self.subTest(family=family, allow_reply=allow_reply):
                    listener = socket.socket(family, socket.SOCK_DGRAM)
                    client = socket.socket(family, socket.SOCK_DGRAM)
                    process = None
                    try:
                        if family == socket.AF_INET6:
                            for sock in (listener, client):
                                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        listener.bind((host, 0)); client.bind((host, 0)); client.settimeout(0.15)
                        self.assertNotIn(listener.getsockname()[1], (53, 18131))
                        fixture = profile if allow_reply else profile.replace(reply_filter, '')
                        # Only this fixture's local DNS endpoint changes; remote *:53 stays intact.
                        fixture = fixture.replace('localhost:53', 'localhost:'+str(listener.getsockname()[1]))
                        process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', fixture, executable,
                                                    '-I', '-B', '-c', child_code, str(listener.fileno())],
                                                   pass_fds=(listener.fileno(),), close_fds=True,
                                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                                   env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
                        client.sendto(b'owned-probe', listener.getsockname())
                        output, error = process.communicate(timeout=5)
                        self.assertEqual(process.returncode, 0, error)
                        self.assertEqual(error, '')
                        metrics = json.loads(output)
                        self.assertTrue(metrics['received_query'])
                        self.assertEqual(metrics['fresh_errno'], 1)
                        self.assertEqual(metrics['reply_errno'], 0 if allow_reply else 1)
                        received = []
                        while True:
                            try:
                                packet, _ = client.recvfrom(32)
                                received.append(packet)
                            except socket.timeout:
                                break
                        self.assertEqual(received, [b'owned-response'] if allow_reply else [])
                    finally:
                        if process is not None and process.poll() is None:
                            process.kill(); process.wait(timeout=2)
                        listener.close(); client.close()


class SupervisorPlanTest(unittest.TestCase):
    def test_argv_is_candidate_only_and_has_no_software_or_personal_home_fallback(self):
        argv = supervisor.guest_argv()
        self.assertEqual(argv[argv.index('-avd')+1], 'RemoteAndroid17Isolated')
        self.assertEqual(argv[argv.index('-ports')+1], '5566,5567')
        self.assertEqual(argv[argv.index('-grpc')+1], '8566')
        self.assertIn('-grpc-use-token', argv)
        self.assertEqual(argv[argv.index('-accel')+1], 'on')
        self.assertEqual(argv[argv.index('-gpu')+1], 'host')
        self.assertEqual(argv[argv.index('-http-proxy')+1], '127.0.0.1:18131')
        self.assertEqual(argv[argv.index('-dns-server')+1], '127.0.0.1')
        self.assertIn('-no-snapshot', argv)
        self.assertEqual(argv[argv.index('-crash-report-mode')+1], 'disabled')
        self.assertIn('-no-metrics', argv)
        self.assertIn('-no-boot-anim', argv)
        self.assertNotIn('swiftshader', ' '.join(argv))
        self.assertNotIn('/Users/', ' '.join(argv))

    def test_environment_has_no_proxy_dyld_pythonpath_or_personal_inheritance(self):
        self.assertEqual(supervisor.child_environment('vm')['ANDROID_EMU_MEDIA_DECODER_VTB'], '1')
        for role in ('vm', 'egress'):
            env = supervisor.child_environment(role)
            for denied in ('PYTHONPATH', 'DYLD_LIBRARY_PATH', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'SSH_AUTH_SOCK'):
                self.assertNotIn(denied, env)
            self.assertNotIn('/Users/', json.dumps(env))

    def test_guard_profile_only_exposes_reviewed_network_and_writable_home(self):
        profile = supervisor.guard_profile((supervisor.PYTHON_PACKAGE, supervisor.OPENSSL_PACKAGE))
        self.assertIn('(remote tcp "localhost:7897")', profile)
        self.assertIn('(remote udp "*:53")', profile)
        self.assertIn('(require-all (local udp "localhost:53") (remote udp "localhost:*"))', profile)
        self.assertNotIn('(remote udp "*:*"', profile)
        self.assertNotIn('(remote tcp "*:', profile)
        self.assertNotIn('(subpath "/opt/homebrew")', profile)
        self.assertNotIn('(subpath "/Library")', profile)
        write = profile[profile.index('(deny file-write*'):profile.index('(deny file-map-executable')]
        self.assertIn('/homes/egress', write)
        self.assertNotIn('/shared/', write)
        self.assertNotIn('Cellar', write)
        self.assertIn('NOT by this OS filter', profile)

    def test_execute_requires_root_darwin_before_spawning_anything(self):
        with patch.object(supervisor.os, 'getuid', return_value=501), \
                patch.object(supervisor.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(supervisor.Refused, 'root_darwin'):
                supervisor.execute_plan({}, '0'*64, 1)
        popen.assert_not_called()

    def test_default_cli_prepare_is_read_only_and_does_not_execute(self):
        plan = {'schema': 1, 'mode': 'prepare_only', 'blockers': ['owned-fixture-blocker']}
        with patch.object(supervisor, 'prepare_plan', return_value=plan), \
                patch.object(supervisor, 'execute_plan') as execute, \
                patch.object(supervisor, 'TrialReceipt') as receipt, \
                patch('builtins.print') as printed:
            self.assertEqual(supervisor.main([]), 0)
        execute.assert_not_called()
        receipt.assert_not_called()
        self.assertEqual(json.loads(printed.call_args.args[0]), plan)


if __name__ == '__main__':
    unittest.main()
