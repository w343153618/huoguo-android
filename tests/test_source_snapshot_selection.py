import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.probes import source_snapshot_selection as m

ROW = {'nonce': 'a'*24, 'sha256': 'b'*64, 'uid': 2000, 'device': 8,
       'inode': 100, 'size': 5888, 'parent_device': 8, 'parent_inode': 99}


class SourceSnapshotSelectionChecks(unittest.TestCase):
    def test_closed_descriptor_rejects_credentials_extra_duplicate_and_wrong_integer(self):
        parsed = m.parse(json.dumps(ROW).encode())
        self.assertEqual(parsed.inode, 100)
        for value in (dict(ROW, credential='not permitted'), dict(ROW, uid=True),
                      dict(ROW, nonce='bad;input'), dict(ROW, size=1048576), []):
            with self.assertRaises(ValueError): m.parse(json.dumps(value).encode())
        for raw in (b'{"uid":1,"uid":1}', b'null', b'\xff', b' '*4097):
            with self.assertRaises(ValueError): m.parse(raw)

    def test_descriptor_requires_exact_owned_private_regular_inode_not_link_or_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'descriptor.json'; p.write_text(json.dumps(ROW)); p.chmod(0o600)
            self.assertEqual(m.read_private(p).sha256, ROW['sha256'])
            p.chmod(0o644)
            with self.assertRaises(ValueError): m.read_private(p)
            p.chmod(0o600); link = Path(directory)/'symlink'; link.symlink_to(p)
            with self.assertRaises(OSError): m.read_private(link)
            hard = Path(directory)/'hardlink'; os.link(p, hard)
            with self.assertRaises(ValueError): m.read_private(p)
            hard.unlink(); fifo = Path(directory)/'fifo'; os.mkfifo(fifo, 0o600)
            with self.assertRaises(ValueError): m.read_private(fifo)
            with self.assertRaises(ValueError): m.read_private('relative.json')
            with patch.object(m.os, 'getuid', return_value=os.getuid()+1), self.assertRaises(ValueError):
                m.read_private(p)

    def test_changed_metadata_or_partial_file_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'descriptor.json'; p.write_text(json.dumps(ROW)); p.chmod(0o600)
            with patch.object(m.os, 'read', return_value=b'{}'), self.assertRaises(ValueError): m.read_private(p)
            before = p.stat(); after = Mock(wraps=before); after.st_ino = before.st_ino+1
            with patch.object(m.os, 'fstat', side_effect=[before, after]), self.assertRaises(ValueError):
                m.read_private(p)

    def test_selection_creation_is_inert_and_uses_only_fixed_guest(self):
        selection = m.Selection(m.parse(json.dumps(ROW).encode()))
        with patch.object(m, 'DirectDumpReader') as cls:
            self.assertEqual(selection.status()['reader_count'], 0); cls.assert_not_called()
            with self.assertRaises(ValueError): selection.factory('inert', 'emulator-5554', 6)
            cls.assert_not_called()
            reader = cls.return_value; reader.possibly_retained = ['huoguo-source-ui-'+'c'*24]
            selection.factory('inert', 'emulator-5556', 6)
            self.assertFalse(selection.status()['remote_scope_clear_verified'])
            self.assertEqual(len(selection.status()['possibly_retained_scopes']), 1)
            reader.possibly_retained = []
            self.assertTrue(selection.status()['remote_scope_clear_verified'])
            self.assertFalse(selection.status()['readonly_qualification_is_permission_or_ownership'])


if __name__ == '__main__': unittest.main()
