import unittest
import xml.etree.ElementTree as ET
from scripts.probes.source_player_quality import parse_quality


class PlayerQualityChecks(unittest.TestCase):
    def parse(self, descriptions):
        root = ET.Element('hierarchy')
        for desc in descriptions:
            ET.SubElement(root, 'node', {'content-desc': desc})
        return parse_quality(root)

    def test_selected_format_is_not_inferred_from_title_or_auto_label(self):
        for labels in (['Big Buck Bunny 60fps'], ['Quality Auto (1080p60)'], ['1080p60'], []):
            self.assertFalse(self.parse(labels)['known'])

    def test_ambiguous_quality_is_unknown_and_plain_p_is_not_assumed_30_or_60(self):
        self.assertFalse(self.parse(['Quality 1080p60', 'Quality 720p60'])['known'])
        self.assertEqual(self.parse(['Quality 1080p']), {'known': True, 'height': 1080, 'fps': None})

    def test_selected_60fps_numeric_readback(self):
        self.assertEqual(self.parse(['Quality 1080p60', 'Playback speed 1.0']),
                         {'known': True, 'height': 1080, 'fps': 60})
