"""Owned/mocked fixtures only; no candidate socket, PID, root action or secret read."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.security import candidate_capture_probe as probe


class SocketFixture:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, length):
        block = bytes(self.incoming[:length])
        del self.incoming[:length]
        return block

    def close(self):
        self.closed = True


def reply(data):
    return b'OKAY' + ('%04x' % len(data)).encode() + data


class CandidateFixture(unittest.TestCase):
    def test_plan_and_default_cli_never_open_or_spawn(self):
        with patch.object(probe.socket, 'create_connection', side_effect=AssertionError('no socket')), \
                patch.object(probe.subprocess, 'run', side_effect=AssertionError('no command')), \
                patch.object(sys, 'argv', ['probe']), redirect_stdout(io.StringIO()) as output:
            probe.main()
        result = json.loads(output.getvalue())
        self.assertFalse(result['executed'])
        self.assertFalse(result['screenshot_executable'])
        self.assertEqual(result['adb_target'], '127.0.0.1:5567')
        self.assertEqual(result['controller_uid'], 601)
        self.assertNotIn('token', result)

    def test_fixed_adb_connect_and_transport_inventory(self):
        first = SocketFixture(reply(b'connected to 127.0.0.1:5567'))
        second = SocketFixture(reply(b'127.0.0.1:5567\tdevice\n'))
        connector = Mock(side_effect=[first, second])
        client = probe.CandidateAdb(connector=connector)
        self.assertEqual(client.connect_candidate(), '127.0.0.1:5567')
        for call in connector.call_args_list:
            self.assertEqual(call.args, (('127.0.0.1', 15037),))
        self.assertEqual(first.sent, b'001bhost:connect:127.0.0.1:5567')
        self.assertEqual(second.sent, b'000chost:devices')
        self.assertTrue(first.closed and second.closed)

    def test_foreign_offline_and_ambiguous_transport_refused(self):
        for rows in (b'OTHER\tdevice\n', b'127.0.0.1:5567\toffline\n',
                     b'127.0.0.1:5567\tdevice\nemulator-5566\tdevice\n', b''):
            client = probe.CandidateAdb(connector=Mock(side_effect=[
                SocketFixture(reply(b'fixture')), SocketFixture(reply(rows))]))
            with self.assertRaises(probe.Refused):
                client.connect_candidate()

    def test_fixed_read_only_shell_commands_and_reply_bounds(self):
        for kind in ('boot', 'size', 'gles'):
            sock = SocketFixture(b'OKAYOKAYfixture')
            client = probe.CandidateAdb(connector=Mock(return_value=sock))
            self.assertEqual(client.read('127.0.0.1:5567', kind), 'fixture')
            self.assertIn(b'host:transport:127.0.0.1:5567', sock.sent)
            self.assertNotIn(b'host:start-server', sock.sent)
            self.assertTrue(sock.closed)
        client = probe.CandidateAdb(connector=Mock(side_effect=AssertionError('must not connect')))
        for serial, kind in (('emulator-5556', 'boot'), ('127.0.0.1:5567', 'reboot')):
            with self.assertRaises(probe.Refused):
                client.read(serial, kind)
        for data in (b'FAILprivate guest error', b'OKAYffff', b'OKAY0008x'):
            client = probe.CandidateAdb(connector=Mock(return_value=SocketFixture(data)))
            with self.assertRaises(probe.Refused) as error:
                client._host('host:devices')
            self.assertNotIn('private', str(error.exception))
        client = probe.CandidateAdb(connector=Mock(return_value=SocketFixture(
            b'OKAYOKAY'+b'x'*(probe.MAX_REPLY+1))))
        with self.assertRaises(probe.Refused):
            client.read('127.0.0.1:5567', 'boot')

    def test_boot_uid_duration_and_success_report_are_bounded(self):
        client = Mock()
        with patch.object(probe.os, 'getuid', return_value=501), self.assertRaises(probe.Refused):
            probe.probe_boot(client=client)
        client.assert_not_called()
        with patch.object(probe.os, 'getuid', return_value=601), \
                patch.object(probe.os, 'geteuid', return_value=601), \
                patch.object(probe, '_read_attestation', return_value={'server_pid': 10, 'vm_pid': 11}), \
                patch.object(probe, '_check_live_pids'):
            for seconds in (0, 61, float('nan'), float('inf')):
                with self.assertRaises(probe.Refused):
                    probe.probe_boot(seconds, client)
            client.connect_candidate.return_value = probe.ADB_TARGET
            client.read.side_effect = ['1', 'Physical size: 1080x1920', 'GLES: Metal ANGLE guest-private-text']
            result = probe.probe_boot(1, client)
        self.assertTrue(result['boot_completed'])
        self.assertFalse(result['capture_accepted'])
        self.assertFalse(result['hvf_accepted'])
        self.assertEqual(result['physical_size'], [1080, 1920])
        self.assertTrue(result['gles_markers']['Metal'])
        self.assertNotIn('guest-private-text', json.dumps(result))

    def test_boot_timeout_and_unexpected_property(self):
        client = Mock(connect_candidate=Mock(return_value=probe.ADB_TARGET),
                      read=Mock(return_value='0'))
        with patch.object(probe.os, 'getuid', return_value=601), \
                patch.object(probe.os, 'geteuid', return_value=601), \
                patch.object(probe, '_read_attestation', return_value={'server_pid': 10, 'vm_pid': 11}), \
                patch.object(probe, '_check_live_pids'), \
                patch.object(probe.time, 'sleep'):
            result = probe.probe_boot(1, client)
            self.assertFalse(result['boot_completed'])
            self.assertEqual(result['attempts'], 4)
            client.read.return_value = 'unexpected private text'
            with self.assertRaises(probe.Refused) as error:
                probe.probe_boot(1, client)
        self.assertNotIn('private text', str(error.exception))

    def test_fixed_pid_process_command_and_uid(self):
        executable = str(probe.ROOT/'shared/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64-headless')
        with patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='600 '+executable)) as command:
            probe._candidate_process(123)
        self.assertEqual(command.call_args.args[0], ['/bin/ps', '-p', '123', '-o', 'uid=,comm='])
        for text in ('501 '+executable, '600 /tmp/emulator', '600 /bin/sh'):
            with patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=text)), self.assertRaises(probe.Refused):
                probe._candidate_process(123)

    def test_discovery_private_owned_fixture_and_identity_failures(self):
        with tempfile.TemporaryDirectory(prefix='huoguo-discovery-owned-') as temporary:
            home = Path(temporary).resolve()
            home.chmod(0o700)
            directory = home/'tmp/avd/running'
            directory.mkdir(parents=True, mode=0o700)
            source = directory/'pid_123.ini'
            token = 'owned-fixture-token-not-live'
            valid = 'avd.name='+probe.AVD+'\ngrpc.port=8566\ngrpc.token='+token+'\n'
            original_fstat = probe.os.fstat
            def as_vm(fd):
                info = original_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=600, st_nlink=info.st_nlink,
                                       st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                                       st_ctime_ns=info.st_ctime_ns)
            with patch.object(probe, 'VM_HOME', home), \
                    patch.object(probe, 'DISCOVERY_DIRS', {'tmp': directory}), \
                    patch.object(probe, '_candidate_process') as process, \
                    patch.object(probe, '_listener_owner'), \
                    patch.object(probe.os, 'fstat', side_effect=as_vm):
                source.write_text(valid); source.chmod(0o600)
                self.assertEqual(probe._read_discovery(123, 'tmp'), token)
                self.assertEqual(process.call_count, 2)
                for data in (valid.replace('8566', '8556'), valid.replace(probe.AVD, 'OriginalAVD'),
                             valid+'grpc.port=8566\n', valid.replace(token, 'private\x00token'),
                             'x'*(probe.MAX_DISCOVERY+1)):
                    source.write_text(data)
                    with self.assertRaises(probe.Refused):
                        probe._read_discovery(123, 'tmp')
                source.write_text(valid)
                source.chmod(0o666)
                with self.assertRaises(probe.Refused):
                    probe._read_discovery(123, 'tmp')
                source.unlink(); source.symlink_to(home/'outside')
                with self.assertRaises(OSError):
                    probe._read_discovery(123, 'tmp')
                for pid, location in ((True, 'tmp'), (1000000, 'tmp'), (123, '/outside')):
                    with self.assertRaises(probe.Refused):
                        probe._read_discovery(pid, location)

    def test_token_broker_only_anonymous_pipe_and_no_secret_report(self):
        token = 'owned-fixture-token-not-live'
        reader, writer = os.pipe()
        try:
            with patch.object(probe.os, 'getuid', return_value=0), \
                    patch.object(probe.os, 'geteuid', return_value=0), \
                    patch.object(probe, '_read_discovery', return_value=token):
                result = probe.broker_token(123, writer)
            data = json.loads(os.read(reader, 4096))
            self.assertEqual(data['token'], token)
            self.assertEqual(data['endpoint'], probe.GRPC_ENDPOINT)
            self.assertNotIn(token, json.dumps(result))
            self.assertFalse(result['token_in_report'])
            with self.assertRaises(probe.Refused):
                probe.broker_token(123, writer)
            with tempfile.TemporaryFile() as ordinary:
                with patch.object(probe.os, 'getuid', return_value=0), \
                        patch.object(probe.os, 'geteuid', return_value=0), self.assertRaises(probe.Refused):
                    probe.broker_token(123, ordinary.fileno())
        finally:
            os.close(reader); os.close(writer)

    def test_cli_failure_does_not_print_untrusted_exception(self):
        with patch.object(sys, 'argv', ['probe', 'boot', '--ownership-fd', '4']), \
                patch.object(probe, 'probe_boot', side_effect=OSError('private-token-fixture')), \
                redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            probe.main()
        self.assertNotIn('private-token-fixture', output.getvalue())

    def test_endpoint_owner_exact_pid_uid_and_loopback_listener(self):
        valid = 'p123\nu601\nn127.0.0.1:15037\n'
        with patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=valid)) as command:
            probe._listener_owner(15037, 123, 601)
        self.assertEqual(command.call_args.args[0], ['/usr/sbin/lsof', '-nP', '-iTCP:15037',
                                                    '-sTCP:LISTEN', '-Fpun'])
        for data in (valid.replace('u601', 'u600'), valid.replace('p123', 'p124'),
                     valid.replace('127.0.0.1', '*'), valid.replace('15037', '5037'), ''):
            with patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=data)), self.assertRaises(probe.Refused):
                probe._listener_owner(15037, 123, 601)

    def test_attestation_root_file_freshness_and_fixed_identity(self):
        writer, filename = tempfile.mkstemp(prefix='huoguo-attestation-owned-')
        reader = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
        os.unlink(filename)
        try:
            original = probe.os.fstat
            def as_root(fd):
                info = original(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink,
                                       st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                                       st_ctime_ns=info.st_ctime_ns)
            with patch.object(probe.os, 'getuid', return_value=0), \
                    patch.object(probe.os, 'geteuid', return_value=0), \
                    patch.object(probe.os, 'fstat', side_effect=as_root), \
                    patch.object(probe, '_check_live_pids') as process, \
                    patch.object(probe, '_listener_owner') as listener:
                result = probe.attest_endpoints(123, 456, writer)
                record = probe._read_attestation(reader)
            self.assertTrue(result['ownership_attested'])
            self.assertEqual(record['server_pid'], 123)
            self.assertEqual(record['vm_pid'], 456)
            self.assertEqual(process.call_count, 2)
            self.assertEqual([call.args for call in listener.call_args_list],
                             [(15037, 123, 601), (5567, 456, 600)])
            for change in ({'server_uid': 600}, {'adb_server': '127.0.0.1:5037'},
                           {'issued_unix_ns': 1}, {'vm_pid': True}, {'extra': 'untrusted'}):
                changed = dict(record, **change)
                os.ftruncate(writer, 0); os.lseek(writer, 0, os.SEEK_SET)
                os.write(writer, (json.dumps(changed)+'\n').encode())
                with patch.object(probe.os, 'fstat', side_effect=as_root), self.assertRaises(probe.Refused):
                    probe._read_attestation(reader)
            with self.assertRaises(probe.Refused):
                probe._read_attestation(reader)
            with patch.object(probe.os, 'fstat', side_effect=as_root), self.assertRaises(probe.Refused):
                probe._read_attestation(writer)
            rpipe, wpipe = os.pipe()
            try:
                with self.assertRaises(probe.Refused):
                    probe._root_attestation_file(rpipe, readonly=True)
            finally:
                os.close(rpipe); os.close(wpipe)
        finally:
            os.close(reader); os.close(writer)

    def test_no_attestation_means_no_adb_connection_and_drip_deadline(self):
        client = Mock()
        with patch.object(probe.os, 'getuid', return_value=601), \
                patch.object(probe.os, 'geteuid', return_value=601), self.assertRaises(probe.Refused):
            probe.probe_boot(1, client)
        client.connect_candidate.assert_not_called()
        sock = SocketFixture(b'partial')
        actual = probe.CandidateAdb()
        with patch.object(probe.time, 'monotonic', return_value=3), self.assertRaises(probe.Refused):
            actual._exact(sock, 8, deadline=2)

    def test_starting_candidate_retries_only_fixed_transport(self):
        client = Mock(connect_candidate=Mock(side_effect=[probe.Refused('candidate_adb_not_present'),
                                                        probe.ADB_TARGET]),
                      read=Mock(side_effect=['1', 'Physical size: 1080x1920', 'GLES: Metal']))
        with patch.object(probe.os, 'getuid', return_value=601), \
                patch.object(probe.os, 'geteuid', return_value=601), \
                patch.object(probe, '_read_attestation', return_value={'server_pid': 10, 'vm_pid': 11}), \
                patch.object(probe, '_check_live_pids'), patch.object(probe.time, 'sleep'):
            result = probe.probe_boot(1, client)
        self.assertTrue(result['boot_completed'])
        self.assertEqual(result['attempts'], 2)


if __name__ == '__main__':
    unittest.main()
