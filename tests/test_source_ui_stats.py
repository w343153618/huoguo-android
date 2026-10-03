import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.probes import source_ui_stats as m


def snapshot(**changes):
    fields = {'video_id': 'aqz-KE-bpKQ', 'video_format': '\u202d299 avc1 1920x1080@60\u2009(visionOS 1.02)',
              'audio_format': '\u202d251:fixtureTrack opus\u2009(visionOS 1.02)',
              'viewport': '1080x608xtrue', 'dropped_frames': '0 / 11319',
              'scpn': 'inert-private-text-must-not-escape'}
    fields.update(changes)
    root = ET.Element('hierarchy')
    layout = ET.SubElement(root, 'node', {'resource-id': m.PREFIX + 'nerd_stats_layout',
                                        'package': m.PACKAGE, 'class': 'android.widget.RelativeLayout'})
    for key, text in fields.items():
        ET.SubElement(layout, 'node', {'resource-id': m.PREFIX + key, 'package': m.PACKAGE,
                                     'class': 'android.widget.TextView', 'text': text})
    ET.SubElement(root, 'node', {'resource-id': m.PREFIX + 'player_control_play_pause_replay_button',
                               'package': m.PACKAGE, 'content-desc': 'Play video'})
    ET.SubElement(root, 'node', {'resource-id': m.PREFIX + 'time_bar_current_time',
                               'package': m.PACKAGE, 'text': '6:17'})
    return root


class StatsUIChecks(unittest.TestCase):
    def parse(self, root):
        return m.parse_stats(ET.tostring(root), 'aqz-KE-bpKQ')

    def test_actual_split_resource_schema_retains_descriptor_only(self):
        result = self.parse(snapshot())
        self.assertTrue(result['format_known'])
        self.assertEqual(result['video_format'], {'itag': 299, 'codec': 'avc1', 'width': 1920,
                                                 'height': 1080, 'descriptor_fps': 60.0})
        self.assertEqual(result['audio_format'], {'itag': 251, 'codec': 'opus'})
        self.assertEqual(result['position_ui_seconds'], 377)
        self.assertEqual(result['player_ui_state'], 'paused')
        self.assertFalse(result['process_identity_verified'])
        self.assertFalse(result['decoded_or_presented_fps_verified'])
        self.assertIsNone(result['observed_content_fps'])

    def test_private_debug_strings_and_track_tag_not_returned(self):
        result = json.dumps(self.parse(snapshot()))
        for private in ('inert-private-text', 'fixtureTrack', 'visionOS'):
            self.assertNotIn(private, result)

    def test_different_actual_video_not_filled_from_expected(self):
        result = self.parse(snapshot(video_id='aaaaaaaaaaa'))
        self.assertEqual(result['status'], 'different_video')
        self.assertIsNone(result['video_id'])
        self.assertIsNone(result['video_format'])

    def test_menu_or_title_is_not_stats(self):
        root = ET.Element('hierarchy')
        ET.SubElement(root, 'node', {'text': 'Big Buck Bunny 60fps Quality 1080p60'})
        self.assertEqual(self.parse(root)['status'], 'unavailable')

    def test_duplicate_layout_rejected(self):
        root = snapshot(); root.append(snapshot()[0])
        self.assertEqual(self.parse(root)['status'], 'rejected')

    def test_duplicate_or_missing_format_rejected(self):
        for duplicate in (True, False):
            root = snapshot(); layout = root[0]
            node = next(n for n in layout if n.get('resource-id') == m.PREFIX + 'video_format')
            if duplicate:
                layout.append(ET.fromstring(ET.tostring(node)))
            else:
                layout.remove(node)
            self.assertFalse(self.parse(root)['format_known'])

    def test_foreign_layout_or_field_rejected(self):
        for target in (0, 1):
            root = snapshot(); (root[0] if target == 0 else root[0][1]).set('package', 'foreign')
            self.assertEqual(self.parse(root)['status'], 'rejected')

    def test_malformed_and_out_of_bounds_formats_rejected(self):
        for value in ('299 avc1 0x1080@60', '299 avc1 1920x1080@999',
                      '299 avc1 1920x1080@NaN', '299 avc1 1920x1080@60 foreign',
                      '299 unknown 1920x1080@60', '1080p60'):
            self.assertFalse(self.parse(snapshot(video_format=value))['format_known'])

    def test_fractional_descriptor_fps_is_not_presented_fps(self):
        result = self.parse(snapshot(video_format='299 avc1 1920x1080@59.94'))
        self.assertEqual(result['video_format']['descriptor_fps'], 59.94)
        self.assertFalse(result['decoded_or_presented_fps_verified'])

    def test_dropped_counter_rejects_inversion_and_cannot_become_fps(self):
        self.assertFalse(self.parse(snapshot(dropped_frames='3 / 2'))['format_known'])
        self.assertEqual(self.parse(snapshot())['dropped_frames_cumulative'], {'dropped': 0, 'total': 11319})

    def test_deep_large_malformed_or_entity_xml_rejected(self):
        for raw in (b'<', b'<!DOCTYPE hierarchy [<!ENTITY x "data">]><hierarchy/>', b'x' * (m.MAX_BYTES + 1)):
            self.assertEqual(m.parse_stats(raw)['status'], 'rejected')
        root = ET.Element('hierarchy'); child = root
        for _ in range(m.MAX_DEPTH + 1):
            child = ET.SubElement(child, 'node')
        self.assertEqual(self.parse(root)['status'], 'rejected')
        root = ET.Element('hierarchy')
        for _ in range(m.MAX_NODES):
            ET.SubElement(root, 'node')
        self.assertEqual(self.parse(root)['status'], 'rejected')

    def test_invalid_expected_id_is_explicit_error(self):
        for value in ('../../private', 123, True):
            with self.assertRaises(ValueError):
                m.parse_stats(ET.tostring(snapshot()), value)

    def test_utf16_dtd_cannot_bypass_preparse_entity_rejection(self):
        xml = '<!DOCTYPE hierarchy [<!ENTITY x "data">]><hierarchy/>'
        for raw in (xml.encode('utf-16-le'), xml.encode('utf-16'), b'\xff'):
            self.assertEqual(m.parse_stats(raw)['status'], 'rejected')

    def test_wrong_layout_tag_or_class_cannot_claim_stats_schema(self):
        for tag, cls in (('foreign', 'android.widget.RelativeLayout'), ('node', 'foreign')):
            root = snapshot(); root[0].tag = tag; root[0].set('class', cls)
            self.assertEqual(self.parse(root)['status'], 'rejected')

    def test_viewport_dimensions_and_duplicate_player_state_are_rejected(self):
        for viewport in ('9000x608xtrue', '1080x608', '0x0xtrue'):
            self.assertFalse(self.parse(snapshot(viewport=viewport))['format_known'])
        root = snapshot(); root.append(ET.fromstring(ET.tostring(root[1])))
        self.assertFalse(self.parse(root)['format_known'])

    def test_default_cli_never_reads_files_or_operates_device(self):
        with patch.object(m, 'read_owned', side_effect=AssertionError('read')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['device_operations'], 0)

    def test_owned_snapshot_rejects_symlink_and_public_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'snapshot.xml'; path.write_bytes(ET.tostring(snapshot())); path.chmod(0o600)
            self.assertEqual(m.read_owned(path), path.read_bytes())
            link = Path(temporary) / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError):
                m.read_owned(link)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                m.read_owned(path)


if __name__ == '__main__':
    unittest.main()
