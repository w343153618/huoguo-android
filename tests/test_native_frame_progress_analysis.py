"""Offline closed numeric prefixes only; no phone, native JNI or network."""
import json
import unittest

from scripts.probes import native_frame_progress_analysis as analysis
import test_udp_native_frame_export as java_export


def event(sequence, frame, kind=3, at=2_000_000):
    first = at - (80_000 if kind == 3 else 10)
    return {'event_code': kind, 'frame_id': frame, 'reference_id': 0, 'flags': 3,
            'first_arrival_us': first, 'last_arrival_us': first + (0 if kind == 3 else 10),
            'fec_quorum_ready_us': 0 if kind == 3 else at, 'event_phone_us': at,
            'deadline_phone_us': first + 80_000, 'logical_bytes': 100,
            'reason_code': {1: 0, 2: 1, 3: 3, 4: 4, 5: 5}[kind],
            'event_sequence': sequence, 'pts_us': 0}


def snapshot(rows=(), *, retained=None, pending=0, native_evicted=0, java_evicted=0, enabled=1):
    value = {'schema_version': 1, 'available': int(enabled == 1), 'enabled': enabled,
             'capacity': 64, 'source_ring_capacity': 8192, 'native_ring_capacity': 256,
             'all_generated_events_exported': 0, 'all_pipeline_events_covered': 0,
             'timestamp_is_physical_packet_arrival': 0, 'quorum_is_codec_ready': 0,
             'host_phone_clock_subtraction_valid': 0, 'frame_identity_from_JSON_verified': 0,
             'omitted_rows_validated': 0}
    if enabled != 1:
        return value
    retained = len(rows) if retained is None else retained
    generated = retained + pending + native_evicted + java_evicted
    contiguous = all(row['event_sequence'] == i + 1 for i, row in enumerate(rows))
    value.update(source_retained_count=retained, exported_count=len(rows),
                 omitted_prefix_following_count=retained - len(rows), generated_count=generated,
                 native_pending_count=pending, native_evicted_count=native_evicted,
                 java_evicted_count=java_evicted, exported_sequence_contiguous_from_one=int(contiguous),
                 omitted_rows_validated=int(retained == len(rows)),
                 all_generated_events_exported=int(retained == len(rows) and not pending
                                                  and not native_evicted and not java_evicted
                                                  and contiguous and (rows[-1]['event_sequence'] if rows else 0) == generated))
    for name in ('event_code', 'frame_id', 'reference_id', 'flags', 'first_arrival_us', 'last_arrival_us',
                 'fec_quorum_ready_us', 'event_phone_us', 'deadline_phone_us', 'logical_bytes',
                 'reason_code', 'event_sequence', 'pts_us'):
        value[name] = [row[name] for row in rows]
    return value


def rx():
    r = {'schema_version': 1, 'available': 1, 'capacity': 64, 'exported_count': 4,
         'recorded_count': 4, 'omitted_prefix_following_count': 0, 'all_recorded_samples_exported': 1,
         'all_session_observations_covered': 0, 'timestamp_is_exact_packet_arrival': 0,
         't_ns': [1_500_000_000, 2_500_000_000, 3_500_000_000, 4_500_000_000]}
    for name in ('udp_packets', 'authenticated_packets', 'authenticated_video_packets',
                 'received_media_frames', 'codec_callback_count', 'FEC_packets', 'FEC_frames_expired',
                 'FEC_reference_lost', 'FEC_dependency_dropped', 'FEC_keyframe_requests'):
        r[name] = [0, 0, 0, 0]
    for name in ('udp_packets', 'authenticated_packets', 'authenticated_video_packets', 'FEC_packets'):
        r[name] = [10, 20, 30, 40]
    return r


def worker(start=1_000_000_000, end=5_000_000_000):
    return [{'phone_ns': t, 'worker_received_frames': 1, 'codec_callback_count': 1} for t in (start, end)]


class NativeFrameProgressChecks(unittest.TestCase):
    def test_default_does_not_associate_valid_json_or_convert_it_into_provenance(self):
        out = analysis.analyze(snapshot([event(1, 10)]), rx(), worker())
        self.assertEqual(out['association_status'], 'provenance_unverified')
        self.assertEqual(out['plateaus'], [])
        self.assertEqual(out['RX_progress']['plateaus'], [])

    def test_missing_off_and_known_empty_do_not_become_no_fault_or_coverage_claims(self):
        for enabled in (-1, 0):
            out = analysis.analyze(snapshot(enabled=enabled), rx(), worker(), same_attempt_phone_clock_verified=True)
            self.assertEqual(out['association_status'], 'native_unavailable_not_zero_events')
            self.assertIsNone(out['retained_native_rows'])
        out = analysis.analyze(snapshot(), rx(), worker(), same_attempt_phone_clock_verified=True)
        self.assertEqual(out['plateaus'][0]['interior_retained_event_count'], 0)
        self.assertFalse(out['whole_session_or_plateau_native_coverage_verified'])
        self.assertTrue(out['native_zero_counts_mean_no_retained_event_only'])

    def test_rx_progress_and_native_expiry_coexist_without_causation(self):
        out = analysis.analyze(snapshot([event(1, 10)]), rx(), worker(), same_attempt_phone_clock_verified=True)
        self.assertEqual(out['RX_progress']['plateaus'][0]['classification'], 'authenticated_video_continues_RX_completion_flat')
        self.assertEqual(out['plateaus'][0]['interior_event_counts_code1_to5'], [0, 0, 1, 0, 0])
        self.assertFalse(out['physical_packet_loss_or_FEC_root_cause_proven'])

    def test_two_native_stages_for_one_frame_are_not_two_distinct_frames_or_codec_ready(self):
        q = event(1, 10, kind=1)
        d = dict(q, event_code=2, reason_code=1, event_sequence=2, pts_us=123)
        out = analysis.analyze(snapshot([q, d]), rx(), worker(), same_attempt_phone_clock_verified=True)
        p = out['plateaus'][0]
        self.assertEqual(p['interior_retained_event_count'], 2)
        self.assertEqual(p['interior_distinct_frame_ids'], 1)
        self.assertFalse(out['codec_ready_or_presentation_verified'])

    def test_microsecond_boundary_overlap_is_not_arbitrarily_assigned(self):
        rows = [event(1, 10, at=1_000_000), event(2, 11, at=2_000_000), event(3, 12, at=5_000_000)]
        out = analysis.analyze(snapshot(rows), rx(), worker(1_000_000_501, 5_000_000_999),
                               same_attempt_phone_clock_verified=True)
        self.assertEqual(out['plateaus'][0]['interior_retained_event_count'], 1)
        self.assertEqual(out['plateaus'][0]['boundary_ambiguous_retained_event_count'], 2)

    def test_retained_prefix_eviction_and_pending_cannot_establish_absence(self):
        variants = (snapshot([event(3, 10)], java_evicted=2),
                    snapshot([event(3, 10)], native_evicted=2),
                    snapshot([event(1, 10)], pending=1),
                    snapshot([event(i, i, at=2_000_000 + i) for i in range(1, 65)], retained=70))
        for v in variants:
            out = analysis.analyze(v, rx(), worker(), same_attempt_phone_clock_verified=True)
            self.assertFalse(out['plateaus'][0]['native_whole_plateau_coverage_verified'])
            self.assertTrue(out['plateaus'][0]['observed_counts_are_not_fault_or_loss_rates'])

    def test_boolean_float_and_promoted_flags_are_rejected(self):
        for key, bad in (('generated_count', True), ('capacity', 64.0), ('all_pipeline_events_covered', 1),
                         ('timestamp_is_physical_packet_arrival', 1), ('frame_identity_from_JSON_verified', 1)):
            v = snapshot([event(1, 10)])
            v[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                analysis.validate(v)
        with self.assertRaises(ValueError):
            analysis.analyze(snapshot(), rx(), worker(), same_attempt_phone_clock_verified=1)

    def test_schema_and_lengths_are_closed_not_raw_log_pass_through(self):
        v = snapshot([event(1, 10)])
        v['password'] = 'DO_NOT_EXPORT'
        with self.assertRaises(ValueError):
            analysis.validate(v)
        v = snapshot([event(1, 10)])
        v['event_code'] = []
        with self.assertRaises(ValueError):
            analysis.validate(v)
        out = analysis.analyze(snapshot([event(1, 10)]), rx(), worker(), same_attempt_phone_clock_verified=True)
        self.assertNotIn('"frame_id"', json.dumps(out))

    def test_accounting_and_fabricated_complete_prefix_are_rejected(self):
        for key, bad in (('generated_count', 99), ('native_pending_count', 257), ('source_retained_count', 100),
                         ('exported_sequence_contiguous_from_one', 0), ('omitted_rows_validated', 0)):
            v = snapshot([event(1, 10)])
            v[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                analysis.validate(v)

    def test_invalid_grants_quorum_and_expiry_and_conversion_overflow_are_rejected(self):
        for key, bad in (('deadline_phone_us', 2_000_001), ('event_phone_us', 1_999_999),
                         ('fec_quorum_ready_us', 2_000_001), ('first_arrival_us', -1),
                         ('event_phone_us', (1 << 63) - 1)):
            v = snapshot([event(1, 10)])
            v[key][0] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                analysis.validate(v)

    def test_same_frame_contradiction_duplicate_terminal_and_reverse_time_are_rejected(self):
        q = event(1, 10, kind=1)
        d = dict(q, event_code=2, reason_code=1, event_sequence=2)
        contradictory = dict(d, logical_bytes=101)
        for rows in ([q, contradictory], [dict(d, event_sequence=1), d],
                     [event(1, 10), event(2, 11, at=1_900_000)]):
            with self.assertRaises(ValueError):
                analysis.validate(snapshot(rows))

    def test_existing_rx_coverage_gap_remains_unknown_and_no_host_clock_is_introduced(self):
        r = rx()
        r['t_ns'] = [1_000_000_001, 3_500_000_001, 4_000_000_001, 4_500_000_001]
        out = analysis.analyze(snapshot([event(1, 10)]), r, worker(), same_attempt_phone_clock_verified=True)
        self.assertEqual(out['RX_progress']['plateaus'][0]['classification'], 'RX_sample_timeline_gap_unknown')
        self.assertFalse(out['whole_session_or_plateau_native_coverage_verified'])
        self.assertNotIn('host_capture_us', json.dumps(out))

    def test_missing_rx_is_unassociated_rather_than_successful_empty_plateaus(self):
        out = analysis.analyze(snapshot([event(1, 10)]), {'schema_version': 1, 'available': 0}, worker(),
                               same_attempt_phone_clock_verified=True)
        self.assertEqual(out['association_status'], 'RX_unavailable_native_prefix_unassociated')
        self.assertEqual(out['plateaus'], [])


class NativeFrameJavaProjectionContractChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        java_export.NativeFrameExportChecks.setUpClass.__func__(cls)

    def test_actual_source_java_projection_matches_python_consumer(self):
        # Actual Java methods, still typed JSON substitutes and synthetic rows;
        # not a live JNI/ART report, Attempt ownership or collection acceptance.
        for mode in ('missing', 'off', 'empty', 'types', 'prefix', 'evicted', 'pending'):
            with self.subTest(mode=mode):
                value = java_export.NativeFrameExportChecks.result(self, mode)
                parsed = analysis.validate(value)
                if mode in ('missing', 'off'):
                    self.assertIsNone(parsed)
                else:
                    self.assertEqual(len(parsed), value['exported_count'])


if __name__ == '__main__':
    unittest.main()
