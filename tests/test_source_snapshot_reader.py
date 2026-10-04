import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from scripts.probes import source_snapshot_reader as m
from scripts.probes import source_stats_observation as stats
from scripts.probes import source_remote_observation as remote
from tests.test_source_decoder_observation import FakeReader, identity, session
from tests.test_source_stats_observation import focus
from tests.test_source_ui_stats import snapshot
from tests.test_source_snapshot_protocol import status

NONCE = 'a' * 24
JAR = m.DeployedJar('b' * 24, 'c' * 64, 2000, 8, 100, 5888, 8, 99)
PARENT = b'2000 41c0 8 90 4096 2\n'


def xml():
    root = snapshot()
    root[1].set('class', 'android.widget.ImageButton')
    root[1].set('enabled', 'true'); root[1].set('clickable', 'true')
    root[1].set('content-desc', 'Pause video')
    root[1].set('bounds', '[486,370][594,478]')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def data():
    receipt = ('1 123 2000 567 8 90 8 91 %d\n' % len(xml())).encode()
    raw = m.DIR + PARENT + m.RECEIPT + receipt + m.LOG + status() + m.XML + xml() + m.END
    files = ('2000 8180 8 92 %d 1\n2000 8180 8 93 %d 1\n2000 8180 8 91 %d 1\n'
             % (len(receipt), len(status()), len(xml()))).encode()
    return [raw, b'__HG_RUNNER_ABSENT__\n', files, b'']


class SnapshotReaderChecks(unittest.TestCase):
    def reader(self, rows=None):
        raw = FakeReader(data() if rows is None else rows)
        adapter = m.DirectDumpReader('inert', 'emulator-5556', 15, JAR, reader_factory=lambda *_: raw)
        return adapter, raw

    def test_construction_never_queries_or_deploys(self):
        factory = Mock(return_value=Mock())
        reader = m.DirectDumpReader('inert', 'emulator-5556', 15, JAR, reader_factory=factory)
        reader.reader.read.assert_not_called()
        self.assertEqual(reader.possibly_retained, [])
        with self.assertRaises(ValueError): m.DirectDumpReader('inert', 'serial', 15, {})
        for timeout in (True, 0, 16, float('nan')):
            never = Mock(side_effect=AssertionError('reader must not be created'))
            with self.assertRaises(ValueError):
                m.DirectDumpReader('inert', 'serial', timeout, JAR, reader_factory=never)
            never.assert_not_called()

    def test_closed_protocol_completion_keeps_source_ownership_separate(self):
        parent, completed, content = m.parse_snapshot(data()[0], 2000)
        self.assertEqual(parent[:4], (2000, 0o40700, 8, 90))
        self.assertEqual(content, xml())
        self.assertFalse(completed['runner_exit_verified'])
        self.assertFalse(completed['source_identity_verified'])
        for raw in (data()[0] + b'x', data()[0].replace(m.END, b''),
                    data()[0].replace(b'1 123 2000', b'1 123 0'),
                    data()[0].replace(b'2000 41c0', b'2000 41ff'),
                    data()[0].replace(b'8 90 8 91', b'8 99 8 91'),
                    data()[0].replace(b'OK (1 test)', b'FAILURES!!!')):
            with self.assertRaises(ValueError): m.parse_snapshot(raw, 2000)

    def test_only_complete_exit_matching_metadata_and_removal_translate_old_pipe(self):
        adapter, raw = self.reader()
        content, info = adapter.read([stats.snapshot_script(NONCE)])
        self.assertTrue(info['command_ok'])
        self.assertTrue(info['snapshot_runner_exit_verified'])
        self.assertFalse(info['snapshot_owned_scope_may_remain'])
        parsed, complete = stats.parse_pipe_snapshot(content, 'aqz-KE-bpKQ')
        self.assertTrue(complete); self.assertTrue(parsed['format_known'])
        self.assertTrue(adapter.serialization_verified); self.assertTrue(adapter.runner_exit_verified)
        self.assertEqual(adapter.possibly_retained, [])
        self.assertEqual(len(raw.arguments), 4)
        self.assertTrue(all(not buffer for buffer in raw.buffers))
        self.assertIn('stat -c \'%u %f %d %i\' "$d"', raw.arguments[3][0][0])
        # Directory size/link count changes while creating files; cleanup uses
        # stable directory identity, not its initially empty size.
        self.assertNotIn('"2000 41c0 8 90 4096 2"', raw.arguments[3][0][0])

    def test_timeout_or_error_retains_scope_and_never_executes_old_cleanup(self):
        for fail in (2, 4):
            adapter, raw = self.reader([(m.DIR + PARENT, fail)])
            content, info = adapter.read([stats.snapshot_script(NONCE)])
            self.assertFalse(info['command_ok']); self.assertFalse(content)
            self.assertTrue(info['snapshot_owned_scope_may_remain'])
            self.assertFalse(info['snapshot_runner_exit_verified'])
            self.assertEqual(adapter.possibly_retained, [m.protocol.namespace(NONCE)])
            adapter.read([stats.cleanup_script(NONCE)])
            self.assertEqual(len(raw.arguments), 1)
            self.assertNotIn('rm -f', raw.arguments[0][0][0])

    def test_live_runner_or_unreadable_proc_never_allows_deletion(self):
        suffix = b'S ' + b' '.join([b'0'] * 18) + b' 567\n'
        for proc in (b'123 (uiautomator) ' + suffix, (b'', 4), b'foreign stat'):
            adapter, raw = self.reader([data()[0], proc])
            content, info = adapter.read([stats.snapshot_script(NONCE)])
            self.assertFalse(info['command_ok']); self.assertFalse(content)
            self.assertEqual(len(raw.arguments), 2)
            self.assertEqual(adapter.possibly_retained, [m.protocol.namespace(NONCE)])
            self.assertNotIn('|| printf', raw.arguments[1][0][0])

    def test_runner_pid_reuse_proves_only_previous_runner_gone(self):
        completed = m.protocol.receipt(('1 123 2000 567 8 90 8 91 %d\n' % len(xml())).encode(), expected_uid=2000)
        suffix = b'S ' + b' '.join([b'0'] * 18) + b' 568\n'
        self.assertTrue(m.runner_exited(b'123 (unrelated reused PID) ' + suffix, completed))
        with self.assertRaises(ValueError): m.runner_query_script({'runner_pid': '123;rm'})

    def test_foreign_file_metadata_or_cleanup_error_cannot_claim_removal(self):
        for index, changed in ((2, data()[2].replace(b'8 91', b'8 999')),
                               (2, data()[2].replace(b'2000 8180', b'0 8180')),
                               (2, data()[2].replace(b' 1\n', b' 2\n')),
                               (3, (b'', 4)), (3, b'foreign output')):
            rows = data(); rows[index] = changed
            adapter, raw = self.reader(rows)
            content, info = adapter.read([stats.snapshot_script(NONCE)])
            self.assertFalse(info['command_ok']); self.assertFalse(content)
            self.assertEqual(adapter.possibly_retained, [m.protocol.namespace(NONCE)])

    def test_namespace_collision_and_metadata_injection_rejected_before_command(self):
        with self.assertRaises(ValueError): m.snapshot_script(JAR.nonce, JAR)
        with self.assertRaises(ValueError): m.cleanup_script(NONCE, ('2000;rm', 0, 0, 1, 0, 0), ())
        with self.assertRaises(ValueError): m.DeployedJar(NONCE, 'x;rm', 2000, 8, 100, 5, 8, 99)
        script = m.snapshot_script(NONCE, JAR)
        self.assertIn(JAR.sha256, script)
        self.assertIn('41c0 8 99', script)
        self.assertNotIn('trap ', script)
        for token in ('input ', 'force-stop', '--nohup', 'rm -r', 'waitForIdle'):
            self.assertNotIn(token, script)

    def test_existing_full_source_qualification_still_rejects_identity_change(self):
        window = focus() + b'  Display: mDisplayId=0\n    mRotation=0 mDeferredRotationPauseCount=0\n'
        for restarted in (False, True):
            rows = [b'Physical size: 1080x1920\n', identity(), window, session(state=3),
                *data(), session(state=3), window,
                identity(start=453402 if restarted else 453401), b'Physical size: 1080x1920\n']
            raw = FakeReader(rows)
            def factory(adb, serial, timeout):
                return m.DirectDumpReader(adb, serial, timeout, JAR, reader_factory=lambda *_: raw)
            result = remote.collect('inert', 'emulator-5556', 'aqz-KE-bpKQ',
                required_state='playing', display_width=1080, display_height=1920,
                require_display=True, reader_factory=factory)
            self.assertEqual(result['target_qualified'], not restarted)
            self.assertFalse(result['authenticated_attempt_verified'])
            self.assertFalse(result['input_executed'])
            self.assertTrue(all(not buffer for buffer in raw.buffers))
            self.assertEqual(len(raw.arguments), 12)


if __name__ == '__main__': unittest.main()
