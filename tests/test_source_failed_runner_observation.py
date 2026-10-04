"""Inert failed-lifetime observations and private journal; no Android execution."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.probes import source_failed_runner_observation as m
from scripts.probes.source_owned_scope_journal import ScopeJournal
from tests.test_source_owned_snapshot_capture import JAR, NATIVE, NONCE, Reader, header
from tests.test_source_snapshot_retirement import six_case


def envelope(**changes):
    args = six_case()
    fields = args['waited_raw'].split()
    for key, value in changes.items():
        fields[1 + len(m.owner.IDENTITY) + m.owner.FINAL.index(key)] = str(value).encode()
    waited = b' '.join(fields) + b'\n'
    metas = [args['directory'], args['files']['started'],
             (*args['files']['waited'][:4], len(waited), 1)]
    rows = [('%d %x %d %d %d %d\n' % value).encode() for value in metas]
    return m.META + b''.join(rows) + m.SECTIONS[0] + args['started_raw'] + m.SECTIONS[1] + waited + m.SECTIONS[2]


class FailureLifetimeChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='huoguo-failed-runner-fixture-')
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.journal = ScopeJournal(self.root)
        self.observer = m.FailedRunnerObservation(JAR, NATIVE, self.journal)

    def tearDown(self): self.temp.cleanup()

    def observe(self, raw):
        return m.observation(raw, uid=2000, parent_pid=122, parent_start_ticks=566)

    def test_failure_modes_and_natural_zero_all_retain_unknown_UI_and_lease(self):
        for fields in ({}, {'exit_value':7}, {'exit_value':127},
                {'reason':1, 'exit_kind':2, 'exit_value':15, 'term_sent':1},
                {'reason':2, 'exit_kind':2, 'exit_value':9, 'term_sent':1, 'kill_sent':1},
                {'reason':3, 'log_eof':0, 'log_bytes':8191},
                {'reason':4, 'exit_kind':0, 'exit_value':0, 'child_reaped':0, 'log_eof':0, 'ownership_error':1}):
            with self.subTest(fields=fields):
                result = self.observe(envelope(**fields))
                self.assertTrue(result['lifetime_receipt_consistent'])
                self.assertTrue(result['lease_release_unaccepted'])
                self.assertTrue(result['remote_scope_may_exist'])
                self.assertTrue(all(result[k] is False for k in m.FALSE_FLAGS))
                self.assertEqual(result['exit_value'], fields.get('exit_value', 0))

    def test_nonzero_launcher_can_be_read_after_exact_registered_scope_only(self):
        self.assertEqual(list(self.root.iterdir()), [])
        ticket = self.journal.register(NONCE); reader = Reader([envelope(reason=1, exit_kind=2, exit_value=15, term_sent=1)])
        result = self.observer.inspect_registered(ticket, header(),
            {'command_ok':False, 'child_reaped':True}, reader, lambda:None)
        self.assertEqual(result['reason'], 1)
        self.assertEqual(len(reader.calls), 1)
        self.assertTrue(all(not b for b in reader.buffers))
        self.assertTrue((self.root/f'scope-{NONCE}.json').is_file())
        saved = json.loads((self.root/f'failure-{NONCE}.json').read_text())
        self.assertTrue(saved['remote_scope_may_exist'])
        self.assertFalse(saved['binding']['permission_lease_verified'])
        self.assertFalse((self.root/f'observed-{NONCE}.json').exists())

    def test_current_cleanup_authority_and_local_reap_required_before_any_read(self):
        ticket = self.journal.register(NONCE)
        for authority, info in ((lambda:True, {'command_ok':False, 'child_reaped':True}),
                (lambda:None, {'command_ok':False, 'child_reaped':False}),
                (lambda:None, {'command_ok':1, 'child_reaped':True})):
            reader = Reader([envelope()])
            with self.assertRaises(ValueError):
                self.observer.inspect_registered(ticket, header(), info, reader, authority)
            self.assertEqual(reader.calls, [])
        self.assertTrue((self.root/f'scope-{NONCE}.json').is_file())

    def test_lost_authority_after_parse_keeps_scope_clears_raw_and_no_outcome(self):
        ticket = self.journal.register(NONCE); reader = Reader([envelope()]); calls=0
        def authority():
            nonlocal calls
            calls += 1
            if calls == 3: raise ValueError('expired cleanup authority')
        with self.assertRaises(ValueError):
            self.observer.inspect_registered(ticket, header(),
                {'command_ok':False,'child_reaped':True},reader,authority)
        self.assertTrue(all(not b for b in reader.buffers))
        self.assertTrue((self.root/f'scope-{NONCE}.json').is_file())
        self.assertFalse((self.root/f'failure-{NONCE}.json').exists())

    def test_partial_headers_metadata_aliases_missing_wait_or_foreign_parent_refused(self):
        for raw in (envelope()[:-3], envelope()+b'x', envelope().replace(m.META,m.META*2),
                    envelope().replace(b'2000 8180 8 92',b'2000 8180 8 91'),
                    envelope().replace(b'2000 8180 8 92',b'2000 8180 9 92'),
                    envelope().replace(b'1 122 566 123 2000 567',b'1 122 566 124 2000 567',1),
                    envelope().replace(m.SECTIONS[1],b'')):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError): self.observe(raw)
        with self.assertRaises(ValueError):
            m.observation(envelope(),uid=2000,parent_pid=122,parent_start_ticks=999)
        ticket = self.journal.register(NONCE); reader=Reader([envelope()])
        with self.assertRaises(ValueError):
            self.observer.inspect_registered(ticket,header()[:-2],
                {'command_ok':False,'child_reaped':True},reader,lambda:None)
        self.assertEqual(reader.calls,[])

    def test_failed_collection_or_missing_scope_retains_original_registration(self):
        ticket=self.journal.register(NONCE)
        for row in ((envelope(),False,True),(envelope(),True,False),b'',envelope()[:-1]):
            reader=Reader([row])
            with self.assertRaises(ValueError):
                self.observer.inspect_registered(ticket,header(),
                    {'command_ok':False,'child_reaped':True},reader,lambda:None)
            self.assertTrue(all(not b for b in reader.buffers))
            self.assertTrue((self.root/f'scope-{NONCE}.json').is_file())
        self.assertFalse((self.root/f'failure-{NONCE}.json').exists())

    def test_journal_rejects_promoted_authority_extra_raw_and_boolean_counters(self):
        ticket=self.journal.register(NONCE); result=self.observe(envelope())
        variants=[]
        for key in m.FALSE_FLAGS: variants.append(dict(result,**{key:True}))
        variants.extend((dict(result,xml='secret'),dict(result,child_reaped=True),
                         dict(result,lease_release_unaccepted=False),dict(result,reason=0,term_sent=1)))
        for wrong in variants:
            with self.assertRaises(ValueError): self.journal.record_failure_lifetime_consistency(ticket,wrong)
        self.assertFalse((self.root/f'failure-{NONCE}.json').exists())

    def test_read_plan_parses_and_contains_no_launch_signal_delete_or_xml(self):
        shell=shutil.which('sh')
        if shell is None: raise RuntimeError('local syntax parser required')
        plan=m.collection_script(NONCE,JAR,NATIVE)
        result=subprocess.run([shell,'-n'],input=plan.encode(),capture_output=True,timeout=2)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('"$d/started"',plan);self.assertIn('"$d/waited"',plan)
        for forbidden in ('exec ','runtest ','--snapshot','mkdir','kill ','rm ','window.xml','runner.log','retired','input '):
            self.assertNotIn(forbidden,plan)

    def test_journal_outcome_replay_never_closes_or_replaces_scope(self):
        ticket=self.journal.register(NONCE); result=self.observe(envelope())
        self.journal.record_failure_lifetime_consistency(ticket,result)
        before=(self.root/f'failure-{NONCE}.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.journal.record_failure_lifetime_consistency(ticket,result)
        self.assertEqual((self.root/f'failure-{NONCE}.json').read_bytes(),before)
        self.journal.assert_registered(ticket)


if __name__=='__main__': unittest.main()
