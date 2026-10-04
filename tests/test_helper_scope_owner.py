"""Actual host scope/write-FD/owned-child fixtures, not Android UID/PM acceptance."""
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/helper_scope_owner.c'


class HelperOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('clang') or shutil.which('cc')
        if compiler is None:
            raise RuntimeError('helper_owner_host_compiler_required')
        cls.output = tempfile.TemporaryDirectory(prefix='huoguo-helper-owner-host-')
        cls.fixture = Path(cls.output.name) / 'fixture'
        cls.production = Path(cls.output.name) / 'production'
        for binary, flags in ((cls.fixture, ['-DHG_HELPER_OWNER_FIXTURE']), (cls.production, [])):
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            *flags, str(SOURCE), '-o', str(binary)],
                           capture_output=True, check=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.output.cleanup()

    def setUp(self):
        self.base = tempfile.TemporaryDirectory(prefix='huoguo-owner-scope-')
        self.path = Path(self.base.name)
        self.nonce = 'a'*24
        self.parent = self.path / ('huoguo-helper-owner-' + self.nonce)
        self.apk = self.parent / 'stage/owned.apk'
        self.data = b'host public fixture, not helper APK bytes\n'*7
        self.children = []

    def tearDown(self):
        # Test cleanup is limited to actual host children this case started.
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        self.base.cleanup()  # own host temporary scope, never Android rm

    def args(self, action='natural', data=None):
        data = self.data if data is None else data
        st = self.path.stat()
        return ['--fixture', str(self.path), self.nonce, str(st.st_dev), str(st.st_ino),
                str(len(data)), hashlib.sha256(data).hexdigest(), action]

    def start(self, args=None, *, production=False):
        child = subprocess.Popen([str(self.production if production else self.fixture),
                                  *(self.args() if args is None else args)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, close_fds=True, shell=False)
        self.children.append(child)
        return child

    def run_owner(self, args=None, data=None, *, production=False):
        child = self.start(args, production=production)
        out, err = child.communicate(self.data if data is None else data, timeout=4)
        self.assertEqual(err, b'')
        records = [json.loads(line) for line in out.splitlines()]
        for row in records:
            self.assertFalse(row['release_authorized'])
        return child.returncode, records

    def test_both_defaults_inert_and_production_has_no_activation(self):
        for production in (False, True):
            code, records = self.run_owner([], b'', production=production)
            self.assertEqual(code, 0)
            self.assertFalse(records[0]['operations_started'])
            self.assertFalse(records[0]['production_activation_available'])
        code, records = self.run_owner(self.args(), production=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(records, [])
        self.assertFalse(self.parent.exists())

    def test_owned_upload_writer_closed_before_natural_child(self):
        code, rows = self.run_owner()
        self.assertEqual(code, 0, rows)
        upload, footer = rows[0], rows[-1]
        self.assertTrue(upload['upload_verified'])
        self.assertTrue(upload['writer_closed'])
        self.assertEqual(self.apk.read_bytes(), self.data)
        self.assertEqual(self.apk.stat().st_mode & 0o7777, 0o600)
        self.assertEqual(self.parent.stat().st_mode & 0o7777, 0o700)
        self.assertEqual((self.parent/'stage').stat().st_mode & 0o7777, 0o700)
        self.assertTrue(footer['child_reaped'])
        self.assertEqual(footer['child_exit'], 0)
        self.assertTrue(footer['both_pipe_EOF'])
        self.assertTrue(footer['scope_may_remain'])
        self.assertFalse(footer['actual_PM_invoked'])
        self.assertFalse(footer['remote_PM_quiescence_verified'])
        self.assertFalse(footer['retirement_authorized'])

    def test_SHA_padding_read_boundaries_and_max_upload(self):
        for i, size in enumerate((1, 55, 56, 63, 64, 65, 4095, 4096, 4097, 131072)):
            self.nonce = f'{i+1:024x}'
            data = bytes(x % 251 for x in range(size))
            code, rows = self.run_owner(self.args(data=data), data)
            self.assertEqual(code, 0, (size, rows))
            self.assertTrue(rows[0]['writer_closed'])
            apk = self.path / ('huoguo-helper-owner-'+self.nonce) / 'stage/owned.apk'
            self.assertEqual(apk.read_bytes(), data)

    def test_existing_scope_never_adopted(self):
        self.parent.mkdir(mode=0o700)
        foreign = self.parent/'foreign'
        foreign.write_bytes(b'foreign')
        code, rows = self.run_owner()
        self.assertNotEqual(code, 0)
        self.assertFalse(rows[0]['scope_created'])
        self.assertFalse(rows[-1]['possible_scope_created'])
        self.assertEqual(foreign.read_bytes(), b'foreign')
        self.assertFalse(self.apk.exists())

    def test_writable_base_or_wrong_capture_refuses_before_creation(self):
        self.path.chmod(0o770)
        code, rows = self.run_owner()
        self.assertNotEqual(code, 0)
        self.assertFalse(self.parent.exists())
        self.path.chmod(0o700)
        for index in (3, 4):
            args = self.args();args[index] = str(int(args[index])+1)
            code, rows = self.run_owner(args)
            self.assertNotEqual(code, 0)
            self.assertFalse(rows[-1]['possible_scope_created'])

    def test_truncated_extra_wrong_hash_never_forks_and_keeps_scope(self):
        cases = [(self.data[:-1], None), (self.data+b'extra', None), (self.data, '0'*64)]
        for i, (data, wrong_hash) in enumerate(cases):
            self.nonce = f'{i+1:024x}'
            args = self.args()
            if wrong_hash is not None:
                args[6] = wrong_hash
            code, rows = self.run_owner(args, data)
            self.assertNotEqual(code, 0)
            self.assertFalse(rows[0]['upload_verified'])
            self.assertFalse(rows[-1]['child_reaped'])
            self.assertTrue(rows[-1]['scope_may_remain'])
            self.assertTrue((self.path/('huoguo-helper-owner-'+self.nonce)).is_dir())

    def test_upload_without_EOF_times_out_without_PM_or_deletion(self):
        child = self.start()
        child.stdin.write(self.data);child.stdin.flush()
        child.wait(timeout=3)
        out = child.stdout.read();self.assertEqual(child.stderr.read(), b'')
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertNotEqual(child.returncode, 0)
        self.assertFalse(rows[0]['upload_verified'])
        self.assertFalse(rows[-1]['child_reaped'])
        self.assertEqual(self.apk.read_bytes(), self.data)

    def test_failed_child_reaped_does_not_bless_PM_or_scope(self):
        code, rows = self.run_owner(self.args('failure'))
        footer = rows[-1]
        self.assertNotEqual(code, 0)
        self.assertTrue(footer['child_reaped'])
        self.assertEqual(footer['child_exit'], 7)
        self.assertTrue(footer['both_pipe_EOF'])
        self.assertTrue(footer['scope_may_remain'])

    def test_owned_ignoring_TERM_KILL_reaped_cannot_pass(self):
        code, rows = self.run_owner(self.args('ignoreTERM'))
        footer = rows[-1]
        self.assertNotEqual(code, 0)
        self.assertTrue(footer['child_reaped'])
        self.assertEqual(footer['child_signal'], signal.SIGKILL)
        self.assertEqual(footer['TERM_calls'], 1)
        self.assertEqual(footer['KILL_calls'], 1)
        self.assertFalse(footer['host_child_result_accepted'])
        self.assertTrue(self.apk.is_file())

    def test_parent_cancellation_only_owned_unreaped_child(self):
        child = self.start(self.args('cancel'))
        child.stdin.write(self.data);child.stdin.close()
        seen = []
        end = time.monotonic()+2
        # Unbuffered descriptor readiness; no speculative signal to a PID read
        # from JSON. The actual Popen is the only signal target in this fixture.
        data = bytearray()
        while time.monotonic()<end:
            ready, _, _ = select.select([child.stdout], [], [], max(0, end-time.monotonic()))
            if not ready: break
            block = os.read(child.stdout.fileno(), 4096)
            if not block: break
            data.extend(block)
            if b'helper_owner_child_ready' in data: break
        self.assertIn(b'helper_owner_child_ready', data)
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=3)
        data.extend(child.stdout.read())
        seen = [json.loads(line) for line in data.splitlines()]
        self.assertNotEqual(child.returncode, 0)
        self.assertTrue(seen[-1]['child_reaped'])
        self.assertEqual(seen[-1]['child_signal'], signal.SIGKILL)
        self.assertTrue(seen[-1]['scope_may_remain'])

    def test_pipe_EOF_is_not_implied_by_child_exit(self):
        code, rows = self.run_owner(self.args('holdpipe'))
        footer = rows[-1]
        self.assertNotEqual(code, 0)
        self.assertEqual(footer['child_exit'], 0)
        self.assertTrue(footer['child_reaped'])
        self.assertFalse(footer['both_pipe_EOF'])
        self.assertTrue(footer['scope_may_remain'])

    def test_overflow_continues_drain_but_cannot_pass(self):
        code, rows = self.run_owner(self.args('overflow'))
        footer = rows[-1]
        self.assertNotEqual(code, 0)
        self.assertTrue(footer['pipe_overflow'])
        self.assertTrue(footer['child_reaped'])
        self.assertTrue(footer['both_pipe_EOF'])

    def test_auto_reap_or_competing_handler_refused_before_fork(self):
        for i, action in enumerate(('auto_reap', 'handler_policy')):
            self.nonce = f'{i+1:024x}'
            code, rows = self.run_owner(self.args(action))
            self.assertNotEqual(code, 0)
            self.assertTrue(rows[0]['writer_closed'])
            self.assertTrue(rows[0]['upload_verified'])
            self.assertFalse(rows[-1]['child_reaped'])
            self.assertEqual(rows[-1]['TERM_calls'], 0)
            self.assertEqual(rows[-1]['KILL_calls'], 0)
            self.assertTrue(rows[-1]['scope_may_remain'])

    def test_cancel_after_upload_does_not_start_a_child(self):
        code, rows = self.run_owner(self.args('cancel_before_fork'))
        self.assertNotEqual(code, 0)
        self.assertTrue(rows[0]['upload_verified'])
        self.assertTrue(rows[0]['writer_closed'])
        self.assertFalse(rows[-1]['child_reaped'])
        self.assertFalse(any(row['event'] == 'helper_owner_owned_child_started' for row in rows))
        self.assertEqual(rows[-1]['TERM_calls'], 0)
        self.assertEqual(rows[-1]['KILL_calls'], 0)
        self.assertEqual(self.apk.read_bytes(), self.data)

    def test_changed_mode_or_foreign_entry_not_adopted_before_fork(self):
        for i, action in enumerate(('mutated_mode', 'foreign_entry')):
            self.nonce = f'{i+1:024x}'
            code, rows = self.run_owner(self.args(action))
            self.assertNotEqual(code, 0)
            self.assertTrue(rows[0]['upload_verified'])
            self.assertFalse(rows[-1]['child_reaped'])
            apk = self.path/('huoguo-helper-owner-'+self.nonce)/'stage/owned.apk'
            self.assertEqual(apk.read_bytes(), self.data)
            if action == 'mutated_mode':
                self.assertEqual(apk.stat().st_mode & 0o7777, 0o400)
            else:
                self.assertEqual((apk.parent/'foreign').read_bytes(), b'foreign')

    def test_closed_fixture_arguments_cannot_select_foreign_command(self):
        for index, value in ((0, '--execute'), (2, '../'+'a'*21), (5, '131073'), (7, '/bin/sh')):
            args = self.args();args[index] = value
            code, rows = self.run_owner(args)
            self.assertNotEqual(code, 0)
            self.assertEqual(rows, [])
            self.assertFalse(self.parent.exists())


if __name__ == '__main__':
    unittest.main()
