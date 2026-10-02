import concurrent.futures
import importlib.util
from pathlib import Path
import sys
import unittest

DIRECTORY = Path(__file__).resolve().parents[1] / 'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from udp_session_sender import AuthenticatedSender
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet


class FakeSocket:
    def __init__(self):
        self.packets = []
        self.fail = False

    def send(self, packet):
        self.packets.append(packet)
        if self.fail:
            self.fail = False
            raise OSError('synthetic send failure')
        return len(packet)


class SessionSenderTest(unittest.TestCase):
    def setUp(self):
        self.socket = FakeSocket()
        self.key = bytes(range(32))
        self.sender = AuthenticatedSender(self.socket, self.key, 17)

    def test_concurrent_media_and_control_share_unique_sequence(self):
        lanes = sorted(self.sender.LANES)
        def send(index):
            payload = index.to_bytes(4, 'big')
            self.sender.send(payload, lanes[index % len(lanes)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(send, range(600)))
        sequences = [HEADER.unpack(packet[:HEADER.size])[3] for packet in self.socket.packets]
        self.assertEqual(sequences, list(range(1, 601)))
        replay = ReplayWindow()
        plaintexts = [open_packet(self.key, 17, packet, replay, SERVER_NONCE)
                      for packet in self.socket.packets]
        self.assertEqual(set(plaintexts), {index.to_bytes(4, 'big') for index in range(600)})
        self.assertEqual(sum(lane['datagrams'] for lane in self.sender.snapshot().values()), 600)

    def test_failure_consumes_nonce_and_retry_uses_new_sequence(self):
        self.socket.fail = True
        with self.assertRaises(OSError):
            self.sender.send(b'first', 'audio')
        self.sender.send(b'retry', 'audio')
        self.assertEqual([HEADER.unpack(p[:HEADER.size])[3] for p in self.socket.packets], [1, 2])
        self.assertEqual(self.sender.snapshot()['audio']['send_errors'], 1)

    def test_size_lane_sequence_and_close_fail_before_socket_write(self):
        for payload, lane in ((b'', 'video'), (bytes(1081), 'audio'), (b'x', 'unknown')):
            with self.assertRaises(ValueError): self.sender.send(payload, lane)
        self.sender.sequence = 1 << 64
        with self.assertRaises(ValueError): self.sender.send(b'x', 'video')
        self.sender.close()
        with self.assertRaises(ValueError): self.sender.send(b'x', 'audio')
        self.assertEqual(self.socket.packets, [])

    def test_injected_loss_is_separate_from_success_and_never_reuses_nonce(self):
        sender = AuthenticatedSender(self.socket, self.key, 17, 50)
        for i in range(100): sender.send(b'video', 'video')
        sender.send(b'audio', 'audio')
        sequences = [HEADER.unpack(p[:HEADER.size])[3] for p in self.socket.packets]
        self.assertNotIn(50, sequences)
        self.assertNotIn(100, sequences)
        self.assertEqual(sequences[-1], 101)
        self.assertEqual(sender.snapshot()['video']['datagrams'], 98)
        self.assertEqual(sender.snapshot()['video']['test_dropped_datagrams'], 2)
        self.assertEqual(sender.snapshot()['audio']['test_dropped_datagrams'], 0)


if __name__ == '__main__': unittest.main()
