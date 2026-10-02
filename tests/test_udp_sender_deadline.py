"""Actual sender deadline regressions, fake clock/socket only; no device or network."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[1]/'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0,str(DIRECTORY))
import udp_session_sender as transport
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet


class Clock:
    def __init__(self):self.now=10.
    def __call__(self):return self.now
    def sleep(self,duration):self.now+=duration
    def ns(self):return round(self.now*1_000_000_000)


class Socket:
    def __init__(self,clock):self.clock=clock;self.sent=[]
    def send(self,packet):self.sent.append((self.clock.ns(),packet));return len(packet)


class DeadlineChecks(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.socket=Socket(self.clock);self.key=bytes(range(32))
        self.pacer=transport.SocketPacer(16_000_000,burst_bytes=2048,clock=self.clock,wait=self.clock.sleep)
        self.sender=transport.AuthenticatedSender(self.socket,self.key,3,pacer=self.pacer,clock_ns=self.clock.ns)

    def valid_following_packets(self):
        before=self.sender.sequence
        self.sender.send(b'following-video','video')
        self.sender.send(b'following-audio','audio')
        sequences=[HEADER.unpack(packet[:HEADER.size])[3]for _,packet in self.socket.sent]
        self.assertEqual(sequences,[before,before+1])
        self.assertEqual(self.sender.sequence,before+2)
        replay=ReplayWindow()
        for _,packet in self.socket.sent:open_packet(self.key,3,packet,replay,SERVER_NONCE)

    def test_lock_wait_rechecks_deadline_before_nonce_and_send(self):
        checked=threading.Event();original=self.pacer.video;errors=[]
        def mark_checked(*args,**kwargs):original(*args,**kwargs);checked.set()
        self.pacer.video=mark_checked
        self.sender.lock.acquire()
        def video():
            try:self.sender.send(b'x'*1000,'video',deadline_us=10_010_000)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=video,daemon=True)
        thread.start()
        try:
            self.assertTrue(checked.wait(1),'video must first pass the original pacer check')
            self.clock.sleep(.020) # Priority sender/lock holder has delayed video.
        finally:self.sender.lock.release()
        thread.join(1)
        self.assertFalse(thread.is_alive(),'deadline rejection must not deadlock the shared nonce mutex')
        self.assertEqual(len(errors),1,'late packet must raise PacingDeadline')
        self.assertIsInstance(errors[0],transport.PacingDeadline)
        self.assertEqual(self.socket.sent,[],'no packet may be sent after the deadline')
        self.assertEqual(self.sender.sequence,1,'reject before seal must not consume a nonce')
        self.valid_following_packets()

    def test_seal_delay_rechecks_deadline_and_keeps_consumed_nonce_unique(self):
        original=transport.seal
        def delayed_seal(*args,**kwargs):
            packet=original(*args,**kwargs)
            self.clock.sleep(.020) # Authentication or scheduler delay after initial check.
            return packet
        with patch.object(transport,'seal',side_effect=delayed_seal):
            with self.assertRaises(transport.PacingDeadline):
                self.sender.send(b'x'*1000,'video',deadline_us=10_010_000)
        self.assertEqual(self.socket.sent,[],'sealed expired packet must never reach socket.send')
        self.assertEqual(self.sender.sequence,2,'sealed nonce remains consumed and must not be reused')
        self.assertEqual(self.sender.snapshot()['video']['deadline_rejections'],1)
        self.assertEqual(self.sender.snapshot()['video']['send_errors'],0)
        self.assertEqual(self.sender.snapshot()['video']['max_seal_ns'],20_000_000)
        self.valid_following_packets()

    def test_seal_finishes_before_deadline_but_wire_headroom_is_insufficient(self):
        original=transport.seal
        def delayed_seal(*args,**kwargs):
            packet=original(*args,**kwargs);self.clock.sleep(.0098);return packet
        with patch.object(transport,'seal',side_effect=delayed_seal):
            with self.assertRaises(transport.PacingDeadline):
                self.sender.send(b'x'*1000,'video',deadline_us=10_010_000)
        self.assertLess(self.clock.ns(),10_010_000_000)
        self.assertEqual(self.socket.sent,[])
        self.assertEqual(self.sender.sequence,2)

    def test_syscall_started_in_time_but_completed_late_is_audited_not_undone(self):
        original=self.socket.send
        def delayed_send(packet):
            self.clock.sleep(.020);return original(packet)
        self.socket.send=delayed_send
        self.sender.send(b'x'*1000,'video',deadline_us=10_010_000)
        counters=self.sender.snapshot()['video']
        self.assertEqual(len(self.socket.sent),1,'already posted UDP packet cannot be canceled retrospectively')
        self.assertEqual(counters['datagrams'],1)
        self.assertEqual(counters['max_send_syscall_ns'],20_000_000)
        self.assertEqual(counters['send_completed_after_deadline_count'],1)
        self.assertEqual(counters['max_send_deadline_overshoot_ns'],10_000_000)
        self.assertEqual(counters['deadline_rejections'],0)
        self.assertEqual(self.sender.sequence,2)


if __name__=='__main__':unittest.main()
