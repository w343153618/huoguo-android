import pathlib
import collections
import io
import socket
import struct
import tempfile
import unittest

from hardware_stream import (CONFIG_FLAG, PTS_MASK, HostHardwareSession,
                             FrameRateBudget, audio_timestamp, clock_mapping, experimental_burst_arguments,
                             guest_server_pids, read_control, read_exact, scale_touch, take_raw_frame)


class FragmentedInput:
    """Model a TCP control message arriving in unrelated small fragments."""
    def __init__(self, data):
        self.data = data

    def recv(self, size):
        part, self.data = self.data[:min(size, 3)], self.data[min(size, 3):]
        return part


class HardwareProtocolTest(unittest.TestCase):
    def test_experimental_burst_is_omitted_for_the_deployed_encoder(self):
        self.assertEqual(experimental_burst_arguments(None, 'VBR', None, None), [])
        self.assertEqual(experimental_burst_arguments(None, 'CBR', None, None), [])

    def test_burst_requires_supported_window_and_explicit_vbr_binary(self):
        for native, mode, size, duration in ((None, 'VBR', 100000, .08),
                ('native', 'CBR', 100000, .08), ('native', 'VBR', None, .08),
                ('native', 'VBR', 100000, None), ('native', 'VBR', 10, .08),
                ('native', 'VBR', 100000, float('nan')), ('native', 'VBR', True, .08)):
            with self.subTest(native=native, mode=mode, size=size, duration=duration):
                with self.assertRaises(ValueError):
                    experimental_burst_arguments(native, mode, size, duration)
        self.assertEqual(experimental_burst_arguments('native', 'ADAPTIVE_VBR', 100000, .08),
                         ['--burst-bytes', '100000', '--burst-seconds', '0.08'])

    def test_native_pipe_reads_exact_bytes_and_refuses_truncated_access_unit(self):
        source = io.BytesIO(b'h264\x00\x01')
        self.assertEqual(read_exact(source, 4), b'h264')
        with self.assertRaises(EOFError):
            read_exact(source, 3)

    def test_interleaved_variable_messages_keep_next_control_aligned(self):
        name = b'keyboard'
        descriptor = b'\x05\x01\x09\x06'
        create = b'\x0c' + struct.pack('>HHHB', 1, 2, 3, len(name)) + name
        create += struct.pack('>H', len(descriptor)) + descriptor
        report = b'\x00\x01\x02'
        data = b'\x0d' + struct.pack('>HH', 1, len(report)) + report
        text = b'\x01' + struct.pack('>I', 5) + b'hello'
        clipboard = b'\x09' + struct.pack('>QBI', 99, 1, 4) + b'test'
        adaptive = b'\xf0' + struct.pack('>I', 2500000)
        source = FragmentedInput(create + data + text + clipboard + adaptive)
        for expected in (create, data, text, clipboard, adaptive):
            self.assertEqual(read_control(source), expected)
        with self.assertRaises(EOFError):
            read_control(source)

    def test_bad_text_size_rejected_before_reading_unbounded_payload(self):
        with self.assertRaises(ValueError):
            read_control(FragmentedInput(b'\x01' + struct.pack('>I', 100000000)))

    def test_audio_clock_mapping_preserves_key_and_config_flags(self):
        key = 1 << 61
        offset = 1700000000000000
        self.assertEqual(audio_timestamp(CONFIG_FLAG, offset), CONFIG_FLAG)
        self.assertEqual(audio_timestamp(key | 1000000, offset), key | (offset + 1000000))
        self.assertEqual(audio_timestamp(1000000, offset) & PTS_MASK, offset + 1000000)
        with self.assertRaises(ValueError):
            audio_timestamp(100, -101)

    def test_guest_wall_clock_skew_cannot_shift_audio_against_host_video(self):
        host_time = 1700000000000000
        sample = {'unix_us': host_time - 300000, 'monotonic_us': 8000000, 'sample_span_us': 1}
        mapping = clock_mapping(sample, host_time - 500, host_time + 500)
        self.assertEqual(audio_timestamp(8000000, mapping['offset_us']), host_time)
        self.assertEqual(mapping['guest_wall_skew_ms'], -300)
        self.assertEqual(mapping['uncertainty_us'], 501)

    def test_guest_cleanup_targets_only_the_owned_server_scid(self):
        rows = ('100 app_process / com.genymobile.scrcpy.Server 4.1 scid=01234567\n'
                '101 app_process / com.genymobile.scrcpy.Server 4.1 scid=abcdef12\n'
                '102 unrelated_app scid=01234567\n')
        self.assertEqual(guest_server_pids(rows, '01234567'), ['100'])

    def test_scaled_touch_keeps_pointer_pressure_and_buttons(self):
        original = struct.pack('>BBQiiHHHII', 2, 0, 123, 270, 600, 540, 1200, 32768, 1, 1)
        scaled = scale_touch(original, (1080, 2400))
        self.assertEqual(struct.unpack_from('>iiHH', scaled, 10), (540, 1200, 1080, 2400))
        self.assertEqual(scaled[:10], original[:10])
        self.assertEqual(scaled[22:], original[22:])
        # Landscape source dimensions and physical orientation are distinct.
        landscape = struct.pack('>BBQiiHHHII', 2, 1, 123, 600, 270, 1200, 540, 0, 0, 0)
        self.assertEqual(struct.unpack_from('>iiHH', scale_touch(landscape, (2400, 1080)), 10),
                         (1200, 540, 2400, 1080))

    def test_missing_runtime_fails_before_spawning_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'hardware runtime unavailable'):
                HostHardwareSession(pathlib.Path(directory), 'emulator-5554', 'phone17-root',
                                    1200, 4000000, 60, 'ADAPTIVE_VBR')

    def test_delivery_jitter_can_burst_two_but_does_not_build_a_long_queue(self):
        now = [0.]
        budget = FrameRateBudget(60, lambda: now[0])
        self.assertEqual(budget.delay(), 0)
        budget.consume()
        self.assertEqual(budget.delay(), 0)
        budget.consume()
        self.assertAlmostEqual(budget.delay(), 1 / 60)
        now[0] += 1 / 60
        self.assertAlmostEqual(budget.delay(), 0)
        budget.consume()
        # An idle period cannot grant a large stale-frame burst on reconnect.
        now[0] += 100
        budget.consume(); budget.consume()
        self.assertAlmostEqual(budget.delay(), 1 / 60)

    def test_latest_raw_frame_drops_stale_images_before_the_encoder(self):
        frames = collections.deque(['older', 'newest'], maxlen=2)
        self.assertEqual(take_raw_frame(frames, 'latest'), ('newest', 1))
        self.assertFalse(frames)
        self.assertEqual(take_raw_frame(frames, 'latest'), (None, 0))
        frames.extend(['older', 'newest'])
        self.assertEqual(take_raw_frame(frames, 'fifo'), ('older', 0))
        self.assertEqual(list(frames), ['newest'])
        with self.assertRaises(ValueError):
            take_raw_frame(frames, 'unsafe')


if __name__ == '__main__':
    unittest.main()
