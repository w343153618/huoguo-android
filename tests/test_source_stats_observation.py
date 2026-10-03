import contextlib
import io
import json
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.probes import source_stats_observation as m
from tests.test_source_decoder_observation import FakeReader, identity, session, PRIVATE
from tests.test_source_ui_stats import snapshot


def pipe(root=None, trailer=None):
    xml = ET.tostring(snapshot() if root is None else root, encoding='utf-8', xml_declaration=True)
    return m.UI_CREATED + xml + (b'' if trailer is None else trailer) + m.UI_DONE


def focus(package=m.stats.PACKAGE):
    return ('  mCurrentFocus=Window{abcdef u0 ' + package + '/com.google.android.apps.youtube.app.WatchWhileActivity}\n').encode()


def values():
    return [identity(), focus(), session(state=2, speed=0, position=377000), pipe(),
            session(state=2, speed=0, position=377000), focus(), identity()]


class StatsObservationTests(unittest.TestCase):
    def run_fixture(self, entries=None, **kwargs):
        reader = FakeReader(values() if entries is None else entries)
        result = m.collect('inert-not-adb', 'emulator-5556', 'aqz-KE-bpKQ',
                           reader_factory=lambda *_: reader, **kwargs)
        return result, reader

    def test_default_is_inert(self):
        with patch.object(m, 'collect', side_effect=AssertionError('device')), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['device_operations'], 0)

    def test_paused_full_bracket_and_private_bytes_not_exported(self):
        result, reader = self.run_fixture()
        self.assertTrue(result['qualified'])
        self.assertTrue(result['identity_stable'])
        self.assertTrue(result['session_owner_matches_bracket'])
        self.assertTrue(result['remote_uia_completion_receipt'])
        self.assertEqual(result['stats']['video_format']['itag'], 299)
        self.assertFalse(result['stats']['process_identity_verified'])
        self.assertIsNone(result['decoded_or_presented_fps'])
        self.assertIsNone(result['visual_motion_observed'])
        self.assertFalse(result['overlay_or_collection_overhead_measured'])
        self.assertTrue(all(not b for b in reader.buffers))
        for secret in (PRIVATE, 'inert-private-text', 'fixtureTrack', 'visionOS'):
            self.assertNotIn(secret, json.dumps(result))

    def test_fixed_queries_and_one_private_owned_temporary(self):
        result, reader = self.run_fixture()
        self.assertEqual(len(reader.arguments), 7)
        self.assertEqual(reader.arguments[0][0], [m.observation.IDENTITY_SCRIPT])
        script = reader.arguments[3][0][0]
        self.assertIn('umask 077;', script)
        self.assertIn('mkdir "$d" || exit 20', script)
        self.assertIn('trap ', script)
        self.assertIn('rmdir "$d" || exit 24', script)
        for forbidden in ('/sdcard/', 'input ', 'am start', 'force-stop', 'rm -r'):
            self.assertNotIn(forbidden, script)
        self.assertTrue(result['device_UI_temp_created'])
        self.assertTrue(result['device_UI_temp_removal_confirmed'])
        self.assertFalse(result['host_UI_files_created'])

    def test_pipe_requires_success_receipt_and_closed_trailer(self):
        for raw in (pipe().replace(m.UI_DONE, b''), pipe()+b'x', pipe()+m.UI_DONE,
                    b'ERROR: could not get idle state.'+m.UI_DONE,
                    b'x'*m.stats.MAX_BYTES, b'foreign\n'+pipe()):
            value, complete = m.parse_pipe_snapshot(raw, 'aqz-KE-bpKQ')
            self.assertFalse(complete)
            self.assertFalse(value['format_known'])
        value, complete = m.parse_pipe_snapshot(pipe(trailer=b'foreign text'), 'aqz-KE-bpKQ')
        self.assertTrue(complete)
        self.assertFalse(value['format_known'])

    def test_command_complete_does_not_imply_stats_qualified(self):
        root = ET.Element('hierarchy')
        ET.SubElement(root, 'node')
        value, complete = m.parse_pipe_snapshot(pipe(root), 'aqz-KE-bpKQ')
        self.assertTrue(complete)
        self.assertFalse(value['format_known'])
        entries = values(); entries[3] = pipe(snapshot(video_id='aaaaaaaaaaa'))
        result, _ = self.run_fixture(entries)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['stats']['status'], 'different_video')

    def test_wrong_command_status_cannot_parse_fake_success(self):
        entries = values(); entries[3] = (pipe(), 4)
        entries.insert(4, b'')  # Only the owned path gets a fallback cleanup.
        result, reader = self.run_fixture(entries)
        self.assertFalse(result['qualified'])
        self.assertFalse(result['remote_uia_completion_receipt'])
        self.assertEqual(result['remote_uia_status'], 'unconfirmed')
        self.assertTrue(result['all_local_children_reaped'])
        self.assertTrue(result['device_UI_temp_removal_confirmed'])
        self.assertTrue(all(not b for b in reader.buffers))

    def test_foreign_ambiguous_or_stale_focus_hint_rejected(self):
        for raw in (focus('other.app'), focus()+focus(), b'mFocusedApp='+focus(),
                    focus().replace(b'u0 ', b'u10 '), focus().replace(b'mCurrentFocus', b'foreignmCurrentFocus')):
            self.assertFalse(m.parse_foreground(raw))
        for index in (1, 5):
            entries = values(); entries[index] = focus('other.app')
            self.assertFalse(self.run_fixture(entries)[0]['qualified'])

    def test_restart_uid_owner_and_duplicate_session_fail(self):
        for index, changed in ((6, identity(start=453402)), (6, identity(uid=10001)),
                               (2, session(pid=123, state=2)),
                               (4, session(state=2)+session(state=2))):
            entries = values(); entries[index] = changed
            self.assertFalse(self.run_fixture(entries)[0]['qualified'])

    def test_state_flip_and_visible_contrary_control_fail(self):
        for index in (2, 4):
            entries = values(); entries[index] = session(state=3)
            self.assertFalse(self.run_fixture(entries)[0]['qualified'])
        root = snapshot(); root[1].set('content-desc', 'Pause video')
        entries = values(); entries[3] = pipe(root)
        self.assertFalse(self.run_fixture(entries)[0]['qualified'])

    def test_playing_allows_controls_to_disappear_but_requires_media_state(self):
        root = snapshot(); root.remove(root[1])
        entries = values(); entries[2] = entries[4] = session(state=3)
        entries[3] = pipe(root)
        result, _ = self.run_fixture(entries, required_state='playing')
        self.assertTrue(result['qualified'])
        for index in (2, 4):
            changed = list(entries); changed[index] = session(state=3, speed=0.5)
            self.assertFalse(self.run_fixture(changed, required_state='playing')[0]['qualified'])

    def test_local_reap_failure_and_over_budget_not_qualified(self):
        for unreaped, ago in ((True, 0), (False, 16)):
            reader = FakeReader(values()); reader.unreaped = unreaped
            reader.started_ns = time.monotonic_ns() - ago * 1_000_000_000
            result = m.collect('inert', 'emulator-5556', 'aqz-KE-bpKQ', reader_factory=lambda *_: reader)
            self.assertFalse(result['qualified'])

    def test_invalid_inputs_fail_before_factory(self):
        with patch.object(m.observation, 'BoundedReader') as factory:
            for kwargs in ({'timeout': True}, {'timeout': float('nan')}, {'timeout': 16},
                           {'required_state': 'anything'}):
                with self.assertRaises(ValueError):
                    m.collect('inert', 'emulator-5556', 'aqz-KE-bpKQ', reader_factory=factory, **kwargs)
            for serial, video in (('x;rm', 'aqz-KE-bpKQ'), ('emulator-5556', '../../secret')):
                with self.assertRaises(ValueError):
                    m.collect('inert', serial, video, reader_factory=factory)
            factory.assert_not_called()

    def test_pipe_xml_entities_and_duplicate_layout_never_qualify(self):
        raw = m.UI_CREATED + b'<?xml version="1.0"?><!DOCTYPE hierarchy [<!ENTITY x "data">]><hierarchy/>' + m.UI_DONE
        self.assertFalse(m.parse_pipe_snapshot(raw, 'aqz-KE-bpKQ')[0]['format_known'])
        root = snapshot(); root.append(snapshot()[0])
        self.assertFalse(m.parse_pipe_snapshot(pipe(root), 'aqz-KE-bpKQ')[0]['format_known'])

    def test_creation_failure_never_removes_a_directory_it_did_not_create(self):
        entries = values(); entries[3] = (b'', 4)
        result, reader = self.run_fixture(entries)
        self.assertFalse(result['device_UI_temp_created'])
        self.assertIsNone(result['fallback_cleanup_command'])
        self.assertEqual(len(reader.arguments), 7)

    def test_cleanup_failure_and_uncertain_remote_completion_remain_visible(self):
        entries = values(); entries[3] = (m.UI_CREATED, 2); entries.insert(4, (b'', 2))
        result, _ = self.run_fixture(entries)
        self.assertFalse(result['qualified'])
        self.assertFalse(result['device_UI_temp_removal_confirmed'])
        self.assertEqual(result['remote_uia_status'], 'unconfirmed')

    def test_internal_nonce_cannot_inject_shell_or_select_foreign_directory(self):
        for value in ('../foreign', 'a'*23, 'a'*25, 'A'*24, 'x;rm', True):
            with self.assertRaises(ValueError):
                m.snapshot_script(value)


if __name__ == '__main__':
    unittest.main()
