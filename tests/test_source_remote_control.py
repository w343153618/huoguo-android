import unittest

from scripts.probes import source_remote_control as control
from scripts.probes import source_ui_stats as stats


def snapshot(label='Play video', *, bounds='[486,370][594,478]', clickable='true', enabled='true', package=None):
    package = package or stats.PACKAGE
    fields = [('video_id', 'aqz-KE-bpKQ'), ('video_format', '299 avc1 1920x1080@60'),
              ('audio_format', '251 opus')]
    xml = '<hierarchy><node resource-id="'+stats.PREFIX+'nerd_stats_layout" package="'+stats.PACKAGE+'" class="android.widget.RelativeLayout">'
    for name, text in fields:
        xml += '<node resource-id="'+stats.PREFIX+name+'" package="'+stats.PACKAGE+'" class="android.widget.TextView" text="'+text+'"/>'
    return (xml + '</node><node resource-id="'+stats.PREFIX+'player_control_play_pause_replay_button" package="'+package+'" class="android.widget.ImageButton" content-desc="'+label+'" bounds="'+bounds+'" clickable="'+clickable+'" enabled="'+enabled+'"/></hierarchy>').encode()


class SourceRemoteControlTests(unittest.TestCase):
    def target(self, raw, state='paused'):
        return control.parse_target(raw, expected_video_id='aqz-KE-bpKQ', required_state=state,
                                    display_width=1080, display_height=1920)

    def test_target_remains_inert_without_source_identity_and_owned_attempt(self):
        d = self.target(snapshot())
        self.assertTrue(d['available'])
        self.assertEqual(d['x_u16'], 32768)
        self.assertFalse(d['process_identity_or_attempt_verified'])
        self.assertFalse(d['input_executed'])
        self.assertFalse(d['playback_transition_verified'])

    def test_pause_is_distinct_from_replay_or_arbitrary_button(self):
        self.assertEqual(self.target(snapshot('Pause video'), 'playing')['action_code'], 2)
        for label in ('Replay video', 'Next video', 'Add to queue', ''):
            self.assertFalse(self.target(snapshot(label))['available'])

    def test_hidden_disabled_nonclickable_foreign_or_duplicate_control_is_refused(self):
        for kwargs in ({'enabled':'false'}, {'clickable':'false'}, {'package':'foreign.package'}):
            self.assertFalse(self.target(snapshot(**kwargs))['available'])
        xml = snapshot();node = xml.split(b'</node>', 1)[1].split(b'</hierarchy>', 1)[0]
        self.assertFalse(self.target(xml.replace(b'</hierarchy>', node+b'</hierarchy>'))['available'])

    def test_control_outside_actual_geometry_or_empty_is_refused(self):
        for bounds in ('[1080,0][1081,10]', '[0,1920][10,1921]', '[3,2][3,5]',
                       '[-1,0][10,10]', '[10,10][5,5]', '[0,0][100000,1]'):
            self.assertFalse(self.target(snapshot(bounds=bounds))['available'])

    def test_video_identity_format_and_state_cannot_be_replaced_by_coordinates(self):
        for raw in (snapshot().replace(b'aqz-KE-bpKQ', b'XXXXXXXXXXX'),
                    snapshot().replace(b'299 avc1 1920x1080@60', b'unknown'),
                    snapshot('Pause video')):
            self.assertFalse(self.target(raw)['available'])

    def test_DTD_deep_oversized_and_nonUTF8_inputs_remain_unavailable(self):
        for raw in (b'<!DOCTYPE hierarchy [<!ENTITY x "test">]>'+snapshot(), b'\xff',
                    b' '*(stats.MAX_BYTES+1), b'<hierarchy>'+b'<node>'*40+b'</node>'*40+b'</hierarchy>'):
            self.assertFalse(self.target(raw)['available'])

    def test_geometry_and_unknown_state_are_strict(self):
        for width in (True, 1080.0, 0, 8193):
            with self.assertRaises(ValueError):
                control.parse_target(snapshot(), expected_video_id='aqz-KE-bpKQ', required_state='paused',
                                     display_width=width, display_height=1920)
        with self.assertRaises(ValueError):self.target(snapshot(), 'unknown')

    def test_expected_content_identity_is_required_even_when_UI_is_parseable(self):
        for ident in (None, '', True, 'too-short', 'aqz-KE-bpKQ/foreign'):
            with self.assertRaises(ValueError):
                control.parse_target(snapshot(), expected_video_id=ident, required_state='paused',
                                     display_width=1080, display_height=1920)


if __name__ == '__main__':
    unittest.main()
