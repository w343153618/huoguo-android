"""Inert command/parser and actual local journal integration, no device execution."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from scripts.probes import source_owned_snapshot_capture as m
from scripts.probes.source_owned_scope_journal import ScopeJournal
from tests.test_source_owned_snapshot_binding import case

NONCE = 'a' * 24
JAR = m.legacy.DeployedJar('b' * 24, 'c' * 64, 2000, 8, 100, 5888, 8, 99)
NATIVE = m.DeployedNative('d' * 24, 'e' * 64, 2000, 8, 110, 16920, 8, 109)


def header():
    return m.PARENT + b'122\n122 (sh) S ' + b'0 ' * 18 + b'566\n' + m.PARENT_END


def envelope():
    args = case()
    rows = []
    for meta in (args['directory'], *[args['files'][k] for k in m.binding.FILES]):
        rows.append(('%d %x %d %d %d %d\n' % meta).encode())
    raw = m.META + b''.join(rows)
    for marker, key in zip(m.SECTIONS, ('started_raw', 'waited_raw', 'completed_raw', 'log', 'xml')):
        raw += marker + args[key]
    return raw + m.SECTIONS[-1]


class Reader:
    def __init__(self, rows=None):
        self.rows = [header(), envelope()] if rows is None else rows
        self.calls = []; self.buffers = []
    def read(self, args, limit):
        self.calls.append((args, limit)); value = self.rows.pop(0)
        raw, ok, reaped = value if type(value) is tuple else (value, True, True)
        output = bytearray(raw); self.buffers.append(output)
        return output, {'command_ok': ok, 'child_reaped': reaped}


class OwnedSnapshotCaptureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='huoguo-owned-capture-')
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.capture = m.OwnedSnapshotCapture(JAR, NATIVE, ScopeJournal(self.root))
    def tearDown(self): self.temp.cleanup()

    def test_construction_inert_and_capture_persists_before_first_remote_read(self):
        self.assertEqual(list(self.root.iterdir()), [])
        reader = Reader(); first_read = reader.read
        def read(args, limit):
            self.assertEqual(len(self.capture.possibly_retained), 1)
            self.capture.journal.assert_registered(self.capture.possibly_retained[0])
            return first_read(args, limit)
        reader.read = read
        authority = Mock(return_value=None)
        xml, result = self.capture.capture(NONCE, reader, authority)
        self.assertEqual(xml, case()['xml']); xml.clear()
        self.assertEqual(authority.call_count, 4)
        self.assertTrue(result['receipt_chain_consistent'])
        self.assertFalse(result['remote_UI_quiescence_verified'])
        self.assertFalse(result['permission_lease_verified'])
        self.assertFalse(result['owned_scope_removed_verified'])
        self.assertEqual(len(list(self.root.iterdir())), 2)
        self.assertEqual(len(reader.calls), 2)
        self.assertTrue(all(not v for v in reader.buffers))

    def test_unknown_transport_truncated_header_or_failed_observation_retains_exact_scope(self):
        cases = [[(header(), False, True)], [(header(), True, False)], [header()[:-2]],
                 [header(), envelope()[:-2]], [header(), (envelope(), False, True)],
                 [header(), envelope().replace(b'1 123 2000 567', b'1 124 2000 567')]]
        for i, rows in enumerate(cases):
            nonce = str(i) * 24; reader = Reader(rows)
            with self.assertRaises(ValueError): self.capture.capture(nonce, reader, lambda: None)
            self.assertEqual(self.capture.possibly_retained[-1].nonce, nonce)
            self.assertTrue(all(not v for v in reader.buffers))
            self.assertFalse((self.root / ('observed-' + nonce + '.json')).exists())
            self.assertTrue((self.root / ('scope-' + nonce + '.json')).exists())

    def test_no_authority_or_boolean_marker_cannot_dispatch(self):
        for authority in (None, {}, lambda: True, lambda: False):
            reader = Reader()
            with self.assertRaises(ValueError): self.capture.capture(NONCE, reader, authority)
            self.assertEqual(reader.calls, [])
            self.assertEqual(list(self.root.iterdir()), [])

    def test_authority_loss_after_registration_or_launch_stops_and_preserves_scope(self):
        for i, position in enumerate((2, 3, 4)):
            count = 0
            def authority():
                nonlocal count
                count += 1
                if count == position: raise ValueError('fixture authority lost')
            reader = Reader(); nonce = str(i) * 24
            with self.assertRaises(ValueError): self.capture.capture(nonce, reader, authority)
            self.assertEqual(len(reader.calls), position - 2)
            self.assertTrue((self.root / ('scope-' + nonce + '.json')).exists())
            self.assertFalse((self.root / ('observed-' + nonce + '.json')).exists())
            self.assertTrue(all(not v for v in reader.buffers))

    def test_foreign_deployment_namespace_UID_or_input_never_dispatches(self):
        with self.assertRaises(ValueError): m.OwnedSnapshotCapture(JAR, {}, ScopeJournal(self.root))
        with self.assertRaises(ValueError): m.OwnedSnapshotCapture(JAR, NATIVE, {})
        other = m.DeployedNative('d' * 24, 'e' * 64, 0, 8, 110, 16920, 8, 109)
        with self.assertRaises(ValueError): m.OwnedSnapshotCapture(JAR, other, ScopeJournal(self.root))
        for nonce in (JAR.nonce, NATIVE.nonce, 'A' * 24, '../../foreign', NONCE + '\n'):
            reader = Reader()
            with self.assertRaises(ValueError): self.capture.capture(nonce, reader, lambda: None)
            self.assertEqual(reader.calls, [])
            self.assertEqual(list(self.root.iterdir()), [])

    def test_shell_plan_parses_without_execution_and_has_closed_pins_and_exec(self):
        # Only local sh -n parses syntax. Never invoke its body or a device.
        shell = shutil.which('sh')
        if not shell: raise RuntimeError('local shell required for syntax check')
        for script in (m.launch_script(NONCE, JAR, NATIVE), m.collection_script(NONCE, JAR, NATIVE)):
            result = subprocess.run([shell, '-n'], input=script.encode(), capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('rm ', script); self.assertNotIn('kill ', script)
            self.assertNotIn('input ', script)
        launch = m.launch_script(NONCE, JAR, NATIVE)
        self.assertIn('exec ' + NATIVE.path + ' --snapshot ' + NONCE, launch)
        self.assertIn('/proc/"$$"/stat', launch)
        self.assertIn(JAR.sha256, launch); self.assertIn(NATIVE.sha256, launch)
        self.assertEqual(launch.count('sha256sum'), 2)
        self.assertEqual(m.collection_script(NONCE, JAR, NATIVE).count('sha256sum'), 4)

    def test_parent_identity_is_independent_from_started_file_and_strictly_bounded(self):
        self.assertEqual(m.parent_identity(header()), (122, 566))
        for raw in (b'', header() + b'x', header().replace(b'(sh)', b'(foreign)'),
                    header().replace(b'122\n122', b'124\n122'),
                    header().replace(b'566\n', b'0\n'), b'x' * 8192):
            with self.assertRaises(ValueError): m.parent_identity(raw)
        with self.assertRaises(ValueError):
            m.observation(envelope(), uid=2000, parent_pid=122, parent_start_ticks=568)

    def test_file_count_marker_alias_size_and_runner_failure_rejected(self):
        for raw in (envelope() + b'x', envelope().replace(m.META, m.META * 2),
                    envelope().replace(m.SECTIONS[0], b''),
                    envelope().replace(b'OK (1 test)', b'FAILURES!!!'),
                    envelope().replace(b'2000 8180 8 95 13 1', b'2000 8180 8 95 13 2'),
                    b'x' * 1048576):
            with self.assertRaises(ValueError): m.observation(raw, uid=2000, parent_pid=122, parent_start_ticks=566)


if __name__ == '__main__': unittest.main()
