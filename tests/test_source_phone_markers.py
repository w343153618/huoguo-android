import subprocess
import unittest

from scripts.probes import source_phone_markers as m
from scripts.probes.source_authenticated_driver import Rejected, names

UID = 10001


def stat(size, inode=9, uid=UID, mode='600', bits='8180'):
    return '1:%d:%d:%s:%d:%s\n' % (inode, uid, mode, size, bits)


class Runner:
    def __init__(self, rows): self.rows, self.commands = list(rows), []
    def __call__(self, command):
        self.commands.append(command)
        row = self.rows.pop(0)
        return subprocess.CompletedProcess(command, row[0], row[1], row[2] if len(row) > 2 else '')


class SourcePhoneMarkerChecks(unittest.TestCase):
    def test_read_requires_matching_inode_uid_mode_size_and_bounded_body(self):
        body = b'77\n'
        r = Runner([(0, stat(3)+body.decode()+'\n'+stat(3))])
        marker = m.PhoneMarkers(r, UID)
        self.assertEqual(marker.read(names(1)['dispatched'], 32), body)
        self.assertIn('count=33', r.commands[0]); self.assertIn('[ ! -L ', r.commands[0])
        for tail in (stat(3, inode=10), stat(3, uid=10002), stat(3, mode='644'), stat(3, bits='a1ff')):
            r = Runner([(0, stat(3)+body.decode()+'\n'+tail)])
            with self.assertRaises(Rejected): m.PhoneMarkers(r, UID).read(names(1)['dispatched'], 32)

    def test_missing_is_only_empty_exact_exit44_and_permission_error_is_failure(self):
        self.assertIsNone(m.PhoneMarkers(Runner([(44, '')]), UID).read(names(1)['ready'], 2048))
        for row in ((44, '', 'denied'), (1, ''), (44, 'foreign')):
            with self.assertRaises(Rejected): m.PhoneMarkers(Runner([row]), UID).read(names(1)['ready'], 2048)

    def test_overbound_prefix_and_raw_non_ascii_are_not_returned(self):
        for value in (stat(33)+'x'*33+'\n'+stat(33), stat(1)+'é\n'+stat(1), 'not a stat\n'):
            with self.assertRaises(Rejected):
                m.PhoneMarkers(Runner([(0, value)]), UID).read(names(1)['dispatched'], 32)

    def test_publication_is_nonreplacing_and_cleanup_checks_actual_inode(self):
        stage = m.PRIVATE+'udp-ui-source-driver-a1B2c3D4e5F6'
        r = Runner([(44, ''), (0, stage+'\n'+stat(0)), (0, stat(0)), (0, ''), (0, stat(0)), (0, ''),
                    (44, ''), (0, stat(3)), (0, ''), (44, '')])
        marker = m.PhoneMarkers(r, UID); marker.publish(names(1)['verified'], b'77\n')
        self.assertIn('ln -T ', r.commands[5]); self.assertNotIn('mv ', r.commands[5])
        self.assertTrue(r.commands[3].startswith('restorecon '))
        self.assertTrue(r.commands[3].endswith('2>/dev/null'))
        result = marker.cleanup(); self.assertTrue(result['owned_marker_cleanup_confirmed'])
        self.assertFalse(result['filesystem_checks_atomic'])
        self.assertFalse(marker.owned)

    def test_restorecon_failure_is_fatal_but_exact_empty_inode_is_already_owned(self):
        stage = m.PRIVATE+'udp-ui-source-driver-a1B2c3D4e5F6'
        r = Runner([(44, ''), (0, stage+'\n'+stat(0)), (0, stat(0)), (1, ''),
                    (0, stat(0)), (0, ''), (44, '')])
        marker = m.PhoneMarkers(r, UID)
        with self.assertRaisesRegex(Rejected, 'marker_command'):
            marker.publish(names(1)['verified'], b'77\n')
        self.assertEqual(marker.owned, {stage: (1, 9, UID, 0)})
        self.assertTrue(marker.cleanup()['owned_marker_cleanup_confirmed'])
        self.assertFalse(any('ln -T ' in command for command in r.commands))

    def test_foreign_inode_is_retained_and_cleanup_failure_cannot_pass(self):
        path = m.PRIVATE+names(1)['verified']
        r = Runner([(0, stat(3, inode=10))]); marker = m.PhoneMarkers(r, UID)
        marker.owned[path] = (1, 9, UID, 3)
        value = marker.cleanup()
        self.assertFalse(value['owned_marker_cleanup_confirmed'])
        self.assertEqual(value['cleanup_failures'], 1)
        self.assertTrue(all(not c.startswith('rm ') for c in r.commands))

    def test_foreign_selector_and_non_numeric_payload_reject_before_execution(self):
        r = Runner([]); marker = m.PhoneMarkers(r, UID)
        with self.assertRaises(Rejected): marker.read('auth.json', 32)
        with self.assertRaises(Rejected): marker.publish(names(1)['verified'], b'$(password)\n')
        with self.assertRaises(Rejected): marker.publish(names(1)['ready'], b'77\n')
        self.assertEqual(r.commands, [])

    def test_frame_READ_is_only_closed_bounded_phase_one_command(self):
        stage = m.PRIVATE+'udp-ui-source-driver-a1B2c3D4e5F6'
        r = Runner([(44, ''), (0, stage+'\n'+stat(0)), (0, stat(0)), (0, ''), (0, stat(0)), (0, '')])
        marker = m.PhoneMarkers(r, UID);marker.publish(names(1)['command'], b'READ 77\n')
        self.assertIn("'READ 77\n'",r.commands[5]);self.assertIn('ln -T ',r.commands[5])
        for name,body in ((names(2)['command'],b'READ 77\n'),(names(1)['verified'],b'READ 77\n'),
                (names(1)['command'],b'READ 077\n'),(names(1)['command'],b'READ 0\n'),
                (names(1)['command'],b'READ 9223372036854775808\n'),(names(1)['command'],b'READ 77\nREAD 77\n')):
            run=Runner([])
            with self.assertRaises(Rejected):m.PhoneMarkers(run,UID).publish(name,body)
            self.assertEqual(run.commands,[])


if __name__ == '__main__': unittest.main()
