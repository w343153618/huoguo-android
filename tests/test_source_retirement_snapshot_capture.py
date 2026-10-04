"""Six-file capture/journal with fake remote reads; no device runner execution."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.probes import source_retirement_snapshot_capture as m
from scripts.probes.source_owned_scope_journal import ScopeJournal
from tests.test_source_owned_snapshot_capture import JAR, NATIVE, NONCE, Reader, header, envelope as old_envelope
from tests.test_source_snapshot_retirement import six_case


def envelope():
    args = six_case()
    rows = [('%d %x %d %d %d %d\n' % value).encode() for value in
            (args['directory'], *[args['files'][name] for name in m.retirement.FILES])]
    raw = m.META + b''.join(rows)
    for marker, key in zip(m.SECTIONS, ('started_raw', 'waited_raw', 'completed_raw', 'log', 'xml', 'retired_raw')):
        raw += marker + args[key]
    return raw + m.SECTIONS[-1]


class RetirementCaptureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='huoguo-retirement-capture-')
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.capture = m.RetirementSnapshotCapture(JAR, NATIVE, ScopeJournal(self.root))
    def tearDown(self): self.temp.cleanup()

    def test_constructor_inert_success_uses_six_file_journal_but_retains_scope(self):
        self.assertEqual(list(self.root.iterdir()), [])
        reader = Reader([header(), envelope()]); original = reader.read
        def read(args, limit):
            self.capture.journal.assert_registered(self.capture.possibly_retained[0])
            return original(args, limit)
        reader.read = read
        xml, result = self.capture.capture(NONCE, reader, lambda: None)
        self.assertEqual(xml, six_case()['xml']); xml.clear()
        self.assertFalse(result['remote_UI_quiescence_verified'])
        self.assertFalse(result['permission_lease_verified'])
        observed=json.loads((self.root/f'observed-{NONCE}.json').read_text())
        self.assertEqual(observed['schema'], 'owned-source-retirement-consistency-observed-v1')
        self.assertTrue(observed['remote_scope_may_exist'])
        self.assertTrue((self.root/f'scope-{NONCE}.json').is_file())
        self.assertEqual(len(self.capture.possibly_retained), 1)
        self.assertTrue(all(not raw for raw in reader.buffers))
        self.assertIn('--snapshot-retirement', reader.calls[0][0][0])
        self.assertIn('"$d/retired"', reader.calls[1][0][0])

    def test_old_envelope_failed_retirement_or_truncated_read_keeps_exact_scope(self):
        for i, row in enumerate((old_envelope(), envelope()[:-2],
            envelope().replace(b'1 123 2000 567 8 90 1\n', b'1 123 2000 568 8 90 1\n'),
            (envelope(), False, True), (envelope(), True, False))):
            nonce=str(i)*24; reader=Reader([header(), row])
            with self.assertRaises(ValueError): self.capture.capture(nonce, reader, lambda: None)
            self.assertTrue((self.root/f'scope-{nonce}.json').is_file())
            self.assertFalse((self.root/f'observed-{nonce}.json').exists())
            self.assertTrue(all(not raw for raw in reader.buffers))

    def test_authority_loss_after_parse_clears_buffers_and_never_promotes_observation(self):
        n=0
        def authority():
            nonlocal n
            n+=1
            if n==4: raise ValueError('fixture expired lease')
        reader=Reader([header(), envelope()])
        with self.assertRaises(ValueError): self.capture.capture(NONCE, reader, authority)
        self.assertTrue((self.root/f'scope-{NONCE}.json').exists())
        self.assertFalse((self.root/f'observed-{NONCE}.json').exists())
        self.assertTrue(all(not raw for raw in reader.buffers))

    def test_shell_plans_parse_only_and_no_deletion_signal_or_guest_input(self):
        shell=shutil.which('sh')
        if shell is None: raise RuntimeError('local syntax parser required')
        for script in (m.launch_script(NONCE,JAR,NATIVE),m.collection_script(NONCE,JAR,NATIVE)):
            result=subprocess.run([shell,'-n'],input=script.encode(),capture_output=True,timeout=2)
            self.assertEqual(result.returncode,0,result.stderr)
            for forbidden in ('rm ', 'kill ', 'input ', 'force-stop', 'cmd media_session'):
                self.assertNotIn(forbidden,script)

    def test_six_metadata_rows_or_extra_marker_and_retired_link_alias_rejected(self):
        for raw in (envelope()+b'x', envelope().replace(m.SECTIONS[5],b''),
                    envelope().replace(m.META,m.META*2),
                    envelope().replace(b'2000 8180 8 96', b'2000 8180 8 95'),
                    envelope().replace(b'2000 8180 8 96 22 1',b'2000 8180 8 96 22 2')):
            with self.assertRaises(ValueError):
                m.observation(raw,uid=2000,parent_pid=122,parent_start_ticks=566)

    def test_journal_rejects_claimed_authority_secret_extra_and_schema_mix(self):
        result,_=m.observation(envelope(),uid=2000,parent_pid=122,parent_start_ticks=566)
        ticket=self.capture.journal.register(NONCE)
        for key in ('actual_execution_ownership_verified','remote_UI_quiescence_verified',
                    'permission_lease_verified','installed_framework_execution_verified'):
            wrong=dict(result);wrong[key]=True
            with self.assertRaises(ValueError): self.capture.journal.record_retirement_consistency(ticket,wrong)
        wrong=dict(result,password='secret')
        with self.assertRaises(ValueError): self.capture.journal.record_retirement_consistency(ticket,wrong)
        with self.assertRaises(ValueError): self.capture.journal.record_consistency(ticket,result)
        self.assertFalse((self.root/f'observed-{NONCE}.json').exists())


if __name__=='__main__': unittest.main()
