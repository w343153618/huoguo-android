"""Actual local persistence/failure boundaries; no remote or App lease acceptance."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import source_owned_scope_journal as m
from scripts.probes import source_owned_snapshot_binding as binding
from tests.test_source_owned_snapshot_binding import case

NONCE = 'a' * 24


class OwnedScopeJournalChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='huoguo-scope-journal-')
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.journal = m.ScopeJournal(self.root)

    def tearDown(self): self.temp.cleanup()

    def test_constructor_inert_and_registration_durable_private_and_uncertain(self):
        with patch.object(m.os, 'open', side_effect=AssertionError('constructor I/O')):
            m.ScopeJournal(self.root)
        ticket = self.journal.register(NONCE)
        path = self.root / ('scope-' + NONCE + '.json')
        self.assertEqual(path.stat().st_mode & 0o7777, 0o600)
        self.journal.assert_registered(ticket)
        value = json.loads(path.read_bytes())
        self.assertTrue(value['remote_scope_may_exist'])
        self.assertFalse(value['permission_lease_verified'])
        self.assertFalse(value['remote_UI_quiescence_verified'])

    def test_scope_collision_or_symlink_never_overwrites_or_dispatches(self):
        self.journal.register(NONCE)
        with self.assertRaises(FileExistsError): self.journal.register(NONCE)
        foreign = self.root / 'foreign'; foreign.write_bytes(b'unrelated')
        target = self.root / ('scope-' + 'b' * 24 + '.json'); target.symlink_to(foreign)
        with self.assertRaises(FileExistsError): self.journal.register('b' * 24)
        self.assertEqual(foreign.read_bytes(), b'unrelated')

    def test_failure_to_fsync_keeps_possible_scope_and_no_accepted_ticket(self):
        for failed_call in (1, 2):
            nonce = str(failed_call) * 24; calls = []
            real = os.fsync
            def fail(fd):
                calls.append(fd)
                if len(calls) == failed_call: raise OSError('fixture sync failure')
                return real(fd)
            with patch.object(m.os, 'fsync', side_effect=fail):
                with self.assertRaises(OSError): self.journal.register(nonce)
            self.assertTrue((self.root / ('scope-' + nonce + '.json')).exists())
            with self.assertRaises(FileExistsError): self.journal.register(nonce)

    def test_foreign_mode_or_alias_directory_rejected_before_record_creation(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): self.journal.register(NONCE)
        self.assertEqual(list(self.root.iterdir()), [])
        self.root.chmod(0o700)
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): m.ScopeJournal(alias).register(NONCE)
        with self.assertRaises(ValueError): m.ScopeJournal(Path('relative'))

    def test_record_replacement_mutation_permissions_or_hardlink_rejected(self):
        ticket = self.journal.register(NONCE)
        path = self.root / ('scope-' + NONCE + '.json'); original = path.read_bytes()
        path.write_bytes(original.replace(b'"remote_scope_may_exist":true',
                                          b'"remote_scope_may_exist":null'))
        with self.assertRaises(ValueError): self.journal.assert_registered(ticket)
        path.write_bytes(original); path.chmod(0o644)
        with self.assertRaises(ValueError): self.journal.assert_registered(ticket)
        path.chmod(0o600); os.link(path, self.root / 'extra-link')
        with self.assertRaises(ValueError): self.journal.assert_registered(ticket)
        (self.root / 'extra-link').unlink()
        replacement = self.root / 'replacement'; replacement.write_bytes(original); replacement.chmod(0o600)
        os.replace(replacement, path)
        with self.assertRaises(ValueError): self.journal.assert_registered(ticket)

    def test_consistency_is_append_only_and_never_removes_or_grants_lease(self):
        ticket = self.journal.register(NONCE); result = binding.bind(**case())
        self.journal.record_consistency(ticket, result)
        value = json.loads((self.root / ('observed-' + NONCE + '.json')).read_bytes())
        self.assertTrue(value['remote_scope_may_exist'])
        self.assertFalse(value['binding']['owned_scope_removed_verified'])
        with self.assertRaises(FileExistsError): self.journal.record_consistency(ticket, result)
        self.journal.assert_registered(ticket)

    def test_arbitrary_fields_bytes_or_permission_promotion_never_written(self):
        ticket = self.journal.register(NONCE); original = binding.bind(**case())
        for key in m.UNVERIFIED:
            result = dict(original); result[key] = True
            with self.assertRaises(ValueError): self.journal.record_consistency(ticket, result)
        for bad in ({'password': 'must-not-save'}, dict(original, raw_xml='<private/>'),
                    dict(original, xml_bytes=True), dict(original, receipt_chain_consistent=1)):
            with self.assertRaises(ValueError): self.journal.record_consistency(ticket, bad)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_foreign_nonce_rejected_before_filesystem_operations(self):
        for value in ('../../foreign', 'a' * 23, 'A' * 24, 'a' * 24 + '\n', None):
            with self.assertRaises(ValueError): self.journal.register(value)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_journal_capacity_is_fixed_and_rejects_before_creating_scope(self):
        for i in range(m.MAX_RECORDS): (self.root / ('existing-%d' % i)).touch()
        with self.assertRaises(ValueError): self.journal.register(NONCE)
        self.assertFalse((self.root / ('scope-' + NONCE + '.json')).exists())


if __name__ == '__main__': unittest.main()
