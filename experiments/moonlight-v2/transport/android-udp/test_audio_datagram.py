import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('audio_datagram', Path(__file__).with_name('audio_datagram.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AudioDatagramTests(unittest.TestCase):
    def test_header_config_and_single_frame(self):
        packet, = m.packetize_audio(m.CONFIG_FLAG | 123, b'\x11\x90', 1)
        fields = m.HEADER.unpack(packet[:m.HEADER.size])
        self.assertEqual(fields, (b'HGUA', 1, 1, 40, 123, 1, m.AAC, 2, 0, 1, 0, 2))
        self.assertEqual(packet[40:], b'\x11\x90')

    def test_fragmentation_and_reassembly(self):
        data = bytes(range(256)) * 256
        packets = m.packetize_audio(987, data, 9)
        self.assertTrue(all(len(p) <= 1080 for p in packets))
        self.assertEqual(b''.join(p[40:] for p in packets), data)
        for index, packet in enumerate(packets):
            fields = m.HEADER.unpack(packet[:40])
            self.assertEqual((fields[8], fields[9], fields[10]), (index, len(packets), index * 1040))

    def test_repeated_configuration_is_identical(self):
        self.assertEqual(m.packetize_audio(m.CONFIG_FLAG, b'\x11\x90', 1),
                         m.packetize_audio(m.CONFIG_FLAG, b'\x11\x90', 1))

    def test_bounds(self):
        for timestamp, data, frame_id, codec in ((0, b'', 1, m.AAC),
                (0, b'x' * 65537, 1, m.AAC), (m.CONFIG_FLAG, b'x' * 65, 1, m.AAC),
                (1 << 61, b'x', 1, m.AAC), (-1, b'x', 1, m.AAC),
                (0, b'x', 0, m.AAC), (0, b'x', 1 << 32, m.AAC), (0, b'x', 1, 0)):
            with self.subTest(timestamp=timestamp, frame_id=frame_id, codec=codec):
                with self.assertRaises(ValueError):
                    m.packetize_audio(timestamp, data, frame_id, codec)


if __name__ == '__main__':
    unittest.main()
