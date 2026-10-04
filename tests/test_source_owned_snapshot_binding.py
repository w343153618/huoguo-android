"""Cross-receipt mismatch coverage, not Android execution or source acceptance."""
import unittest

from scripts.probes import source_owned_snapshot_binding as m
from tests.test_source_snapshot_protocol import status


def case():
    log = status(); xml = b'<hierarchy />'
    started = b'1 122 566 123 2000 567 8 90\n'
    waited = ('1 122 566 123 2000 567 8 90 0 1 0 0 0 1 1 %d 1 0 100\n'
              % len(log)).encode()
    completed = ('1 123 2000 567 8 90 8 95 %d\n' % len(xml)).encode()
    args = dict(started_raw=started, waited_raw=waited, completed_raw=completed,
                log=log, xml=xml, directory=(2000, 0o40700, 8, 90, 4096, 2),
                expected_uid=2000, expected_parent_pid=122,
                expected_parent_start_ticks=566)
    args['files'] = {name: (2000, 0o100600, 8, 91 + i, len(raw), 1)
        for i, (name, raw) in enumerate(zip(m.FILES, (started, waited, completed, log, xml)))}
    return args


class OwnedSnapshotBindingChecks(unittest.TestCase):
    def reject(self, args):
        with self.assertRaises(ValueError): m.bind(**args)

    def update(self, key, content):
        args = case(); args[key] = content
        name = dict(started_raw='started', waited_raw='waited',
                    completed_raw='completed', log='runner.log', xml='window.xml')[key]
        meta = args['files'][name]
        args['files'][name] = (*meta[:4], len(content), meta[5])
        return args

    def test_consistent_serialization_and_wait_never_grant_ownership_or_lease(self):
        result = m.bind(**case())
        self.assertTrue(result['receipt_chain_consistent'])
        for key in ('actual_execution_ownership_verified', 'remote_UI_quiescence_verified',
                    'owned_scope_removed_verified', 'source_qualification_verified',
                    'permission_lease_verified'):
            self.assertFalse(result[key])
        self.assertNotIn('xml', result)

    def test_parent_or_java_PID_start_UID_mismatch_rejected(self):
        for key, value in (('expected_parent_pid', 124), ('expected_parent_start_ticks', 568),
                           ('expected_uid', 0), ('expected_parent_pid', True),
                           ('expected_parent_start_ticks', 0)):
            args = case(); args[key] = value; self.reject(args)
        for old, new in ((b'123', b'124'), (b'567', b'568'), (b'2000', b'0')):
            args = case(); self.reject(self.update('completed_raw', args['completed_raw'].replace(old, new)))
        args = case()
        self.reject(self.update('waited_raw', args['waited_raw'].replace(b'122 566', b'122 568')))

    def test_failed_cancelled_unreaped_or_unclosed_wait_is_not_success(self):
        original = case()['waited_raw'].split()
        # Identity has seven fields after schema; final fields begin at index8.
        for index, value in ((8, b'1'), (10, b'7'), (11, b'1'), (12, b'1'),
                             (13, b'0'), (14, b'0'), (16, b'0'), (17, b'1')):
            words = original[:]; words[index] = value
            self.reject(self.update('waited_raw', b' '.join(words) + b'\n'))
        words = original[:]; words[15] = str(int(words[15]) + 1).encode()
        self.reject(self.update('waited_raw', b' '.join(words) + b'\n'))

    def test_completed_parent_XML_inode_and_size_must_bind_fresh_metadata(self):
        original = case()['completed_raw']
        for old, new in ((b'8 90', b'8 99'), (b'8 95', b'8 96'),
                         (b'8 95', b'9 95'), (b'13\n', b'14\n')):
            self.reject(self.update('completed_raw', original.replace(old, new)))
        args = case(); args['xml'] += b'x'; self.reject(args)

    def test_all_five_regular_private_single_link_files_are_required(self):
        for name in m.FILES:
            args = case(); del args['files'][name]; self.reject(args)
            for column, value in ((0, 0), (1, 0o120600), (1, 0o100644),
                                  (2, 9), (3, 0), (3, 90), (4, 0), (5, 2)):
                args = case(); meta = list(args['files'][name]); meta[column] = value
                args['files'][name] = tuple(meta); self.reject(args)
        args = case(); args['files']['foreign'] = args['files']['started']; self.reject(args)
        args = case(); meta = args['files']['waited']
        args['files']['waited'] = (*meta[:3], 91, *meta[4:]); self.reject(args)

    def test_malformed_bounded_receipts_and_runner_error_never_pass(self):
        for key in ('started_raw', 'waited_raw', 'completed_raw', 'log', 'xml'):
            args = case(); args[key] = ''; self.reject(args)
            self.reject(self.update(key, b''))
            self.reject(self.update(key, b'x' * 1048576))
        self.reject(self.update('log', status().replace(b'OK (1 test)', b'FAILURES!!!')))
        for directory in ((2000, 0o40777, 8, 90, 4096, 2),
                          (2000, 0o40700, 8, True, 4096, 2),
                          (2000, 0o40700, 8, 90, -1, 2)):
            args = case(); args['directory'] = directory; self.reject(args)


if __name__ == '__main__': unittest.main()
