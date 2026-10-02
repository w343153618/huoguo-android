"""Offline LTR result/AnnexB boundary tests; no encoder or devices started."""
import importlib.util
from pathlib import Path
import struct
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/probes/apple_ltr_fixture.py'
SPEC = importlib.util.spec_from_file_location('apple_ltr_fixture', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class LtrFixtureTest(unittest.TestCase):
    def test_mixed_annex_b_startcodes_and_nal_types(self):
        bodies = PROBE.nals(bytes.fromhex('000000016742002000000161aa00000165bb'))
        self.assertEqual([b[0] & 31 for b in bodies], [7, 1, 5])

    def test_annex_b_rejects_empty_or_prefix_garbage(self):
        for data in (b'', b'x\x00\x00\x01\x65', b'\x00\x00\x01',
                     b'\x00\x00\x01\x00\x00\x01\x65'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                PROBE.nals(data)

    def test_full_decode_hash_comparison_honors_loss_interval(self):
        full = ['a', 'b', 'c', 'd', 'e', 'f']
        result = PROBE.recovery_hash_match(full, ['a', 'b', 'e', 'f'], 2, 4)
        self.assertTrue(result['all_frames_match_complete_decode'])
        self.assertEqual(result['expected_frames'], 4)

    def test_decode_count_or_order_difference_does_not_claim_recovery(self):
        for recovered in (['a', 'b', 'f', 'e'], ['a', 'b', 'e'], ['a', 'x', 'e', 'f']):
            result = PROBE.recovery_hash_match(['a', 'b', 'c', 'd', 'e', 'f'], recovered, 2, 4)
            self.assertFalse(result['all_frames_match_complete_decode'])

    def test_vcl_reference_metadata_cannot_replace_decode_hash_comparison(self):
        # VCL ref_idc and profile do not prove independent recovery; only the
        # separate full-vs-loss decode hashes establish actual pixel identity.
        result = PROBE.recovery_hash_match(['a', 'b', 'c'], ['a', 'x'], 1, 2)
        self.assertFalse(result['recovery_frames_match'])

    def test_framed_repeats_config_once_with_monotonic_pts_and_original_loss_gap(self):
        packets = [{'pts_us': 0, 'config': True, 'keyframe': False, 'payload': b'\x00\x00\x01\x67x'}]
        for i in range(6):
            packets.append({'pts_us': 1_000_000 + i * 1_000_000 // 60,
                            'config': False, 'keyframe': i == 0,
                            'payload': b'\x00\x00\x01' + bytes((0x65 if i == 0 else 0x61, i))})
        data = PROBE.framed_stream(packets, (540, 960), 60, 6, [0, 1, 4, 5], 2)
        self.assertEqual(data[:16], b'h264' + struct.pack('>III', 0x80000000, 540, 960))
        records, offset = [], 16
        while offset < len(data):
            flags, size = struct.unpack('>QI', data[offset:offset + 12])
            records.append(flags)
            offset += 12 + size
        self.assertEqual(sum(bool(v & PROBE.CONFIG) for v in records), 1)
        media = [v & PROBE.PTS_MASK for v in records if not v & PROBE.CONFIG]
        self.assertEqual(len(media), 8)
        self.assertTrue(all(a < b for a, b in zip(media, media[1:])))
        self.assertEqual(media[2] - media[1], 50_000)
        self.assertEqual(media[4], 1_100_000)


if __name__ == '__main__':
    unittest.main()
