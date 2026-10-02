"""Bitstream/config boundary tests, including the measured Apple SPS/PPS only.

The short parameter sets describe codec syntax; no encoded pictures are stored.
Tests start no encoder, decoder, capture, emulator, phone or network service.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from h264_low_latency import (Invalid, annex_nals, escape, pack_rbsp,
                              parse_sps, patch_apple_baseline_config, ue_bits, unescape)

# SPS/PPS only, copied from the measured 540x1200/60 Apple Baseline fixture.
APPLE_CONFIG = bytes.fromhex('0000000127420020ab404404bf7cd4040404080000000128ce3c80')


def synthetic_sps(refs=1, poc_type=0, hrd=False):
    bits = '01000010' + '00000000' + '00100000' + ue_bits(0) + ue_bits(1)
    bits += ue_bits(poc_type)
    if poc_type == 0:
        bits += ue_bits(2)
    bits += ue_bits(refs) + '0' + ue_bits(33) + ue_bits(74) + '11' + '0'
    if hrd:
        bits += '1' + '00000' + '1' + ue_bits(0) + '00000000'
        bits += ue_bits(0) + ue_bits(0) + '1' + '0' * 20
        bits += '0' + '0' + '0' + '0'
    else:
        bits += '0'  # VUI absent
    return b'\x27' + escape(pack_rbsp(bits))


def config_with_sps(sps):
    (_, _), (pps_marker, pps) = annex_nals(APPLE_CONFIG)
    return b'\x00\x00\x00\x01' + sps + pps_marker + pps


class LowLatencyConfigTest(unittest.TestCase):
    def unsupported_unchanged(self, payload):
        result, metadata = patch_apple_baseline_config(payload)
        self.assertEqual(result, payload)
        self.assertEqual(metadata['status'], 'unsupported')
        self.assertIs(metadata['applied'], False)
        self.assertIs(metadata['changed'], False)
        self.assertTrue(metadata['reason'])
        json.dumps(metadata)

    def test_measured_apple_config_only_adds_restriction(self):
        result, metadata = patch_apple_baseline_config(APPLE_CONFIG)
        self.assertEqual(metadata['status'], 'patched')
        self.assertIs(metadata['applied'], True)
        self.assertEqual(metadata['max_num_reorder_frames'], 0)
        self.assertEqual(metadata['max_dec_frame_buffering'], 1)
        self.assertEqual(metadata['max_num_ref_frames'], 1)
        self.assertEqual(metadata['level_max_dpb_frames'], 8)
        self.assertEqual((metadata['width'], metadata['height']), (540, 1200))
        old, new = annex_nals(APPLE_CONFIG), annex_nals(result)
        self.assertEqual(old[1], new[1])  # Complete PPS plus original start code.
        before, after = parse_sps(old[0][1]), parse_sps(new[0][1])
        for key in ('profile_idc', 'constraints_byte', 'level_idc', 'max_num_ref_frames',
                    'sps_id', 'frame_num_bits', 'poc_lsb_bits', 'width', 'height'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(before['_bits'][:before['_restriction_position']],
                         after['_bits'][:after['_restriction_position']])
        json.dumps(metadata)

    def test_correct_restriction_is_idempotent(self):
        once, _ = patch_apple_baseline_config(APPLE_CONFIG)
        twice, metadata = patch_apple_baseline_config(once)
        self.assertEqual(twice, once)
        self.assertEqual(metadata['status'], 'unchanged')
        self.assertIs(metadata['applied'], True)
        self.assertIs(metadata['changed'], False)

    def test_existing_nonzero_reorder_is_not_rewritten(self):
        (marker, nal), _ = annex_nals(APPLE_CONFIG)
        parsed = parse_sps(nal)
        bits = parsed['_bits'][:parsed['_restriction_position']] + '1' + '1'
        bits += ''.join(ue_bits(value) for value in (2, 1, 16, 16, 1, 1))
        self.unsupported_unchanged(config_with_sps(nal[:1] + escape(pack_rbsp(bits))))

    def test_existing_dpb_below_references_is_not_accepted(self):
        (_, nal), _ = annex_nals(APPLE_CONFIG)
        parsed = parse_sps(nal)
        bits = parsed['_bits'][:parsed['_restriction_position']] + '1' + '1'
        bits += ''.join(ue_bits(value) for value in (2, 1, 16, 16, 0, 0))
        self.unsupported_unchanged(config_with_sps(nal[:1] + escape(pack_rbsp(bits))))

    def test_high_profile_is_unsupported(self):
        self.unsupported_unchanged(APPLE_CONFIG[:5] + b'\x64' + APPLE_CONFIG[6:])

    def test_multiple_references_are_unsupported(self):
        self.unsupported_unchanged(config_with_sps(synthetic_sps(refs=2)))

    def test_poc_type_two_is_unsupported_for_production(self):
        self.unsupported_unchanged(config_with_sps(synthetic_sps(poc_type=2)))

    def test_hrd_is_unsupported(self):
        self.unsupported_unchanged(config_with_sps(synthetic_sps(hrd=True)))

    def test_absent_vui_preserves_existing_core(self):
        original = config_with_sps(synthetic_sps())
        result, metadata = patch_apple_baseline_config(original)
        self.assertEqual(metadata['status'], 'patched')
        self.assertEqual(metadata['sps_before']['vui_parameters_present_flag'], 0)
        self.assertEqual(metadata['sps_after']['vui_parameters_present_flag'], 1)
        self.assertEqual(annex_nals(original)[1], annex_nals(result)[1])

    def test_truncation_empty_and_oversize_do_not_change_payload(self):
        for payload in (b'', b'\x00\x00\x01\x27\x42', APPLE_CONFIG[:-1], b'\x00' * 65537):
            with self.subTest(size=len(payload)):
                self.unsupported_unchanged(payload)

    def test_extra_or_reversed_nals_are_unsupported(self):
        self.unsupported_unchanged(APPLE_CONFIG + b'\x00\x00\x01\x09\xf0')
        nals = annex_nals(APPLE_CONFIG)
        self.unsupported_unchanged(b''.join(marker + nal for marker, nal in reversed(nals)))

    def test_emulation_prevention_roundtrip_and_invalid_escape(self):
        raw = bytes.fromhex('000000010000020000030000030100000000')
        self.assertEqual(unescape(escape(raw)), raw)
        with self.assertRaises(Invalid):
            unescape(bytes.fromhex('00000304'))


if __name__ == '__main__':
    unittest.main()
