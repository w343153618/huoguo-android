import json
from pathlib import Path
import tempfile
import unittest

from scripts.probes import transport_progress_analysis as analysis


def samples(n=6):
    d = dict(schema_version=1, available=1, capacity=64, recorded_count=n,
             exported_count=n, omitted_prefix_following_count=0,
             all_recorded_samples_exported=1, all_session_observations_covered=0,
             timestamp_is_exact_packet_arrival=0,
             t_ns=[(i + 1) * 1_000_000_000 for i in range(n)])
    for name in analysis.COLUMNS:
        d[name] = [0] * n
    return d


def worker(end=7):
    return [dict(phone_ns=i * 1_000_000_000, worker_received_frames=5,
                 codec_callback_count=4) for i in range(1, end + 1)]


class TransportProgressAnalysisTests(unittest.TestCase):
    def analyze(self, rx, rows=None):
        return analysis.analyze(rx, worker() if rows is None else rows,
                                same_attempt_phone_clock_verified=True)

    def test_valid_JSON_does_not_supply_clock_or_attempt_ownership(self):
        d = analysis.analyze(samples(), worker())
        self.assertEqual(d['correlation_status'], 'provenance_unverified')
        self.assertEqual(d['plateaus'], [])

    def test_legacy_missing_RX_is_unknown_not_zero(self):
        d = self.analyze(dict(schema_version=1, available=0))
        self.assertEqual(d['correlation_status'], 'legacy_RX_unavailable')
        self.assertEqual(d['plateaus'], [])

    def test_RX_completion_is_different_from_worker_admission(self):
        d = samples()
        d['received_media_frames'] = list(range(6))
        r = self.analyze(d)
        self.assertEqual(r['plateaus'][0]['classification'],
                         'RX_completion_continues_while_later_worker_is_flat')
        self.assertFalse(r['codec_or_Surface_root_cause_proven'])

    def test_arriving_video_with_expiries_is_not_UDP_silence(self):
        d = samples()
        for key in ('udp_packets', 'authenticated_packets', 'authenticated_video_packets'):
            d[key] = [i * 100 for i in range(6)]
        d['FEC_frames_expired'] = list(range(6))
        r = self.analyze(d)['plateaus'][0]
        self.assertEqual(r['classification'], 'authenticated_video_continues_RX_completion_flat')
        self.assertEqual(r['deltas']['FEC_frames_expired'], 4)

    def test_audio_control_or_untrusted_packets_are_not_video(self):
        d = samples();d['udp_packets'] = list(range(6))
        self.assertEqual(self.analyze(d)['plateaus'][0]['classification'],
                         'nonvideo_or_unqualified_datagrams_continue')

    def test_zero_sample_counts_do_not_prove_physical_packet_loss(self):
        r = self.analyze(samples())
        self.assertEqual(r['plateaus'][0]['classification'],
                         'RX_sampled_datagram_count_flat_not_physical_loss_proof')
        self.assertFalse(r['whole_session_or_physical_network_loss_verified'])

    def test_RX_loop_sampling_gap_is_unknown_even_when_counters_change(self):
        d = samples();d['t_ns'] = [1, 2, 3, 6, 7, 8]
        d['t_ns'] = [t * 1_000_000_000 for t in d['t_ns']]
        for key in ('udp_packets', 'authenticated_packets', 'authenticated_video_packets'):
            d[key] = list(range(6))
        self.assertEqual(self.analyze(d, worker(9))['plateaus'][0]['classification'],
                         'RX_sample_timeline_gap_unknown')

    def test_retained_prefix_cannot_fill_later_plateau(self):
        d = samples(2)
        r = self.analyze(d, [dict(r, phone_ns=r['phone_ns'] + 10_000_000_000) for r in worker()])
        self.assertEqual(r['plateaus'][0]['classification'], 'RX_coverage_insufficient')

    def test_only_interior_samples_contribute_not_nearest_outside_values(self):
        r = self.analyze(samples())['plateaus'][0]
        self.assertEqual(r['RX_interior_start_ns'], 2_000_000_000)
        self.assertEqual(r['RX_interior_end_ns'], 6_000_000_000)

    def test_worker_change_splits_plateau(self):
        rows = worker();rows[-1]['worker_received_frames'] = 6
        self.assertEqual(self.analyze(samples(), rows)['plateaus'][0]['worker_end_ns'], 6_000_000_000)

    def test_counter_boolean_float_negative_or_backwards_are_refused(self):
        for x in (True, 1.0, -1):
            d = samples();d['udp_packets'][1] = x
            with self.assertRaises(ValueError):self.analyze(d)
        d = samples();d['udp_packets'] = [0, 3, 2, 3, 3, 3]
        with self.assertRaises(ValueError):self.analyze(d)

    def test_auth_subset_mismatch_is_refused(self):
        d = samples();d['authenticated_video_packets'] = [1] * 6
        with self.assertRaises(ValueError):self.analyze(d)

    def test_clock_and_shape_mismatch_are_refused(self):
        for change in ('clock', 'length', 'coverage'):
            d = samples()
            if change == 'clock':d['t_ns'][1] = d['t_ns'][0]
            if change == 'length':d['FEC_packets'].pop()
            if change == 'coverage':d['all_session_observations_covered'] = 1
            with self.assertRaises(ValueError):self.analyze(d)
        rows = worker();rows[2]['phone_ns'] = rows[1]['phone_ns']
        with self.assertRaises(ValueError):self.analyze(samples(), rows)

    def test_no_input_text_or_unbounded_objects_are_exported(self):
        d = samples();d['untrusted'] = 'secret-placeholder'
        r = self.analyze(d)
        self.assertNotIn('secret-placeholder', json.dumps(r))
        self.assertFalse(r['timestamp_is_exact_packet_arrival'])
        self.assertFalse(r['timestamp_counter_snapshot_delay_bound_known'])

    def test_file_loading_rejects_duplicate_nonfinite_and_oversized_JSON(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'input.json'
            for raw in ('{"x":1,"x":2}', '{"x":NaN}', ' ' * 65537):
                p.write_text(raw)
                with self.assertRaises(ValueError):analysis._load(p)

    def test_actual_RX_timestamp_is_reassigned_after_socket_processing(self):
        root = Path(__file__).resolve().parents[1]
        source = (root/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        method = source.split('private void receive(', 1)[1].split('private void consume(', 1)[0]
        socket = method.index('socket.receive(packet)')
        refresh = method.index('now=System.nanoTime();consume(a,gen,NativeUdpFec.nativeExpire')
        sample = method.index('samples.put(new JSONObject().put("t_ns",now)')
        self.assertLess(socket, refresh)
        self.assertLess(refresh, sample)
        self.assertNotIn('now=', method[refresh + len('now=System.nanoTime();'):sample])


if __name__ == '__main__':
    unittest.main()
