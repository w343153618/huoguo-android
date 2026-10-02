import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1]/'experiments/moonlight-v2/transport/android-udp/udp_probe_protocol.py'
spec = importlib.util.spec_from_file_location('udp_probe_protocol_test', MODULE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class AuthenticatedUdpProbeTest(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.session = 0xa123456789abcdef

    def packet(self, sequence, payload=b'READY'):
        return p.seal(self.key, self.session, sequence, payload, p.CLIENT_NONCE)

    def test_authenticated_out_of_order_window_rejects_replays_and_too_old(self):
        replay = p.ReplayWindow(4)
        for sequence in (1, 3, 2, 4):
            self.assertEqual(p.open_packet(self.key, self.session, self.packet(sequence), replay), b'READY')
        for sequence in (1, 2, 3, 4):
            with self.assertRaises(ValueError):
                p.open_packet(self.key, self.session, self.packet(sequence), replay)
        self.assertEqual(p.open_packet(self.key, self.session, self.packet(10), replay), b'READY')
        with self.assertRaises(ValueError):
            p.open_packet(self.key, self.session, self.packet(5), replay)

    def test_tampered_high_sequence_cannot_advance_replay_state(self):
        replay = p.ReplayWindow()
        forged = bytearray(self.packet(1_000_000));forged[-1] ^= 1
        with self.assertRaises(Exception):
            p.open_packet(self.key, self.session, forged, replay)
        self.assertEqual(replay.maximum, -1)
        self.assertEqual(p.open_packet(self.key, self.session, self.packet(1), replay), b'READY')

    def test_session_and_direction_are_authenticated(self):
        packet = self.packet(0)
        with self.assertRaises(ValueError):
            p.open_packet(self.key, self.session+1, packet, p.ReplayWindow())
        with self.assertRaises(Exception):
            p.open_packet(self.key, self.session, packet, p.ReplayWindow(), p.SERVER_NONCE)
        self.assertNotEqual(packet, p.seal(self.key, self.session, 0, b'READY', p.SERVER_NONCE))

    def test_mtu_key_sequence_and_truncation_bounds(self):
        packet = self.packet(0, bytes(1080))
        self.assertEqual(len(packet), 1120)
        self.assertEqual(p.open_packet(self.key, self.session, packet, p.ReplayWindow()), bytes(1080))
        for packet in (b'', bytes(39), bytes(1401)):
            with self.assertRaises(ValueError):
                p.open_packet(self.key, self.session, packet, p.ReplayWindow())
        for key, sequence, payload in ((b'short', 0, b'x'), (self.key, -1, b'x'),
                                        (self.key, 2**64, b'x'), (self.key, 0, bytes(1361))):
            with self.assertRaises(ValueError):
                p.seal(key, self.session, sequence, payload)
