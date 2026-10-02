"""Actual-send scheduling and reference-chain safety, fake clocks/sockets only."""
from pathlib import Path
import struct
import sys
import threading
import unittest

DIRECTORY = Path(__file__).resolve().parents[1]/'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from udp_session_sender import AuthenticatedSender, SocketPacer, SocketVideoGate, PacingDeadline
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet


class Clock:
    def __init__(self): self.now=10.
    def __call__(self): return self.now
    def sleep(self, duration): self.now+=duration
    def ns(self): return round(self.now*1_000_000_000)


class Socket:
    def __init__(self, clock): self.clock=clock; self.sent=[]
    def send(self, packet):
        self.sent.append((self.clock(), packet))
        return len(packet)


def shard(frame, capture, *, key=False, reference=0, index=0, blocks=1, block=0, data=1):
    body=b'x'*10
    result=bytearray(56+len(body))
    result[:9]=b'HGUD\x01'+bytes((3 if key else 2, index, data, 2))
    struct.pack_into('>HHH', result, 10, block, blocks, len(body))
    struct.pack_into('>QQ', result, 16, frame, capture)
    struct.pack_into('>IIIIQ', result, 32, len(body), len(body), 80_000, 0x53494d31, reference)
    result[56:]=body
    return bytes(result)


class PacingChecks(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.socket=Socket(self.clock)
        self.pacer=SocketPacer(1_000_000, clock=self.clock, wait=self.clock.sleep)
        self.sender=AuthenticatedSender(self.socket, bytes(range(32)), 3, pacer=self.pacer, clock_ns=self.clock.ns)
        self.recovery=[]
        self.gate=SocketVideoGate(self.sender, self.recovery.append, clock_us=lambda:self.clock.ns()//1000)

    def frame(self, frame, *, key=False, reference=0, capture=None):
        capture=self.clock.ns()//1000 if capture is None else capture
        return [self.gate.send(shard(frame, capture, key=key, reference=reference, index=i)) for i in range(3)]

    def test_backlogged_pipe_packets_still_have_socket_spacing_with_full_overhead(self):
        for _ in range(10): self.sender.send(b'x'*1000, 'video')
        times=[value[0] for value in self.socket.sent]
        for earlier, later in zip(times,times[1:]):
            self.assertAlmostEqual(later-earlier, 1068*8/1_000_000)
        self.assertEqual(self.pacer.snapshot()['reserved_video_wire_bytes'], 10*1068)

    def test_audio_priority_is_immediate_but_accounts_debt_before_next_video(self):
        self.sender.send(b'x'*1000, 'video')
        self.sender.send(b'a'*100, 'audio')
        self.assertEqual(self.socket.sent[0][0], self.socket.sent[1][0])
        self.sender.send(b'x'*1000, 'video')
        self.assertAlmostEqual(self.socket.sent[2][0]-10, (1068+168)*8/1_000_000)
        self.assertEqual(self.pacer.snapshot()['priority_wire_bytes'], 168)

    def test_idle_does_not_bank_credit_for_catchup_burst(self):
        self.sender.send(b'x'*1000, 'video')
        self.clock.sleep(1)
        self.sender.send(b'x'*1000, 'video')
        self.sender.send(b'x'*1000, 'video')
        self.assertAlmostEqual(self.socket.sent[-1][0]-self.socket.sent[-2][0],1068*8/1_000_000)

    def test_bounded_catchup_actual_send_envelope_after_long_scheduler_stalls(self):
        pacer=SocketPacer(16_000_000,burst_bytes=2048,clock=self.clock,wait=self.clock.sleep)
        sender=AuthenticatedSender(self.socket,bytes(range(32)),3,pacer=pacer,clock_ns=self.clock.ns)
        for i in range(120):
            if i in (10,31,68): self.clock.sleep(.005)
            sender.send(b'x'*1000,'video')
            self.clock.sleep(.00008) # Simulated nonzero seal/socket processing.
        times=[row[0] for row in self.socket.sent]
        for first in range(len(times)):
            for last in range(first,len(times)):
                wire_bytes=(last-first+1)*1068
                allowance=(times[last]-times[first])*16_000_000/8+2048+1068
                self.assertLessEqual(wire_bytes,allowance+1)
        self.assertEqual(pacer.snapshot()['burst_bytes'],2048)

    def test_late_actual_write_spends_catchup_credit_before_following_packets(self):
        pacer=SocketPacer(16_000_000,burst_bytes=2048,clock=self.clock,wait=self.clock.sleep)
        original=self.socket.send
        def late_write(packet):
            if not self.socket.sent: self.clock.sleep(.01)
            return original(packet)
        self.socket.send=late_write
        sender=AuthenticatedSender(self.socket,bytes(range(32)),3,pacer=pacer,clock_ns=self.clock.ns)
        for _ in range(8): sender.send(b'x'*1000,'video')
        times=[row[0] for row in self.socket.sent]
        immediate=sum(abs(value-times[0])<1e-9 for value in times)
        self.assertLessEqual(immediate,2) # 2 KiB credit cannot produce a frame-sized burst.
        self.assertGreater(times[-1],times[0])

    def test_catchup_credit_bounds_do_not_raise_eighty_ms_deadline(self):
        for invalid in (-1,4097,True):
            with self.assertRaises(ValueError): SocketPacer(1_000_000,burst_bytes=invalid)
        pacer=SocketPacer(1_000_000,burst_bytes=2048,clock=self.clock,wait=self.clock.sleep)
        with self.assertRaises(PacingDeadline): pacer.video(1068,10_001_000)
        self.assertEqual(pacer.snapshot()['video_reservations'],0)

    def test_rejection_does_not_consume_aead_nonce_or_indefinitely_reserve_debt(self):
        with self.assertRaises(PacingDeadline):
            self.sender.send(b'x'*1000, 'video', deadline_us=10_001_000)
        self.assertEqual(self.sender.sequence, 1)
        self.assertEqual(self.socket.sent, [])
        self.sender.send(b'x', 'touch_ack')
        self.assertEqual(self.sender.sequence, 2)

    def test_video_wait_never_holds_authentication_lock_against_audio(self):
        waiting=threading.Event(); release=threading.Event()
        def wait(duration):
            waiting.set()
            if not release.wait(2): raise RuntimeError('bounded test timeout')
            self.clock.sleep(duration)
        self.pacer.wait=wait
        self.sender.send(b'x'*1000, 'video')
        errors=[]
        def send_video():
            try: self.sender.send(b'x'*1000,'video')
            except Exception as error: errors.append(error)
        thread=threading.Thread(target=send_video); thread.start()
        self.assertTrue(waiting.wait(1))
        self.sender.send(b'a', 'audio') # Completes while video sleeper is blocked.
        self.assertEqual(len(self.socket.sent), 2)
        release.set(); thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_partial_reference_expiry_drops_future_p_chain_until_complete_idr(self):
        self.frame(1,key=True)
        capture=self.clock.ns()//1000
        self.assertGreater(self.gate.send(shard(2,capture,reference=1,index=0,data=3)),0)
        self.clock.sleep(.1)
        self.assertEqual(self.gate.send(shard(2,capture,reference=1,index=1,data=3)),0)
        self.assertEqual(self.gate.send(shard(2,capture,reference=1,index=2,data=3)),0)
        self.assertEqual(self.frame(3,reference=2),[0,0,0])
        self.assertTrue(self.gate.needs_idr)
        self.assertTrue(all(self.frame(4,key=True)))
        self.assertFalse(self.gate.needs_idr)
        self.assertTrue(all(self.frame(5,reference=4)))
        self.assertEqual(self.gate.counts['deadline_dropped_frames'],1)
        self.assertEqual(self.gate.counts['skipped_chain_frames'],1)
        self.assertIn('socket_frame_deadline',self.recovery)

    def test_last_parity_deadline_preserves_complete_media_reference_and_nonce_safety(self):
        capture=self.clock.ns()//1000
        self.gate.send(shard(1,capture,key=True,index=0))
        self.gate.send(shard(1,capture,key=True,index=1))
        self.clock.sleep(.08)
        self.assertEqual(self.gate.send(shard(1,capture,key=True,index=2)),0)
        self.assertTrue(self.gate.current['media_data_complete'])
        self.assertFalse(self.gate.current['all_records_processed'])
        self.assertFalse(self.gate.current['complete'])
        self.assertFalse(self.gate.needs_idr)
        self.assertTrue(all(self.frame(2,reference=1)))
        self.assertEqual(self.recovery,[])
        self.assertEqual(self.gate.counts['tail_parity_deadline_datagrams'],1)
        self.assertEqual(self.gate.counts['deadline_dropped_frames'],0)
        sequences=[HEADER.unpack(packet[:HEADER.size])[3] for _,packet in self.socket.sent]
        self.assertEqual(sequences,list(range(1,len(sequences)+1)))
        replay=ReplayWindow()
        for _,packet in self.socket.sent: open_packet(bytes(range(32)),3,packet,replay,SERVER_NONCE)

    def test_both_tail_parities_expire_after_data_no_idr_and_distinct_fec_state(self):
        capture=self.clock.ns()//1000
        self.gate.send(shard(1,capture,key=True,index=0))
        self.clock.sleep(.081)
        for i in (1,2): self.assertEqual(self.gate.send(shard(1,capture,key=True,index=i)),0)
        self.assertEqual(self.gate.current['tail_parity_deadline_datagrams'],2)
        self.assertEqual(self.gate.counts['tail_parity_deadline_frames'],1)
        self.assertFalse(self.gate.needs_idr)
        self.assertTrue(all(self.frame(2,reference=1)))
        previous=self.gate.snapshot()['frame_events'][0]
        self.assertTrue(previous['media_data_complete'])
        self.assertFalse(previous['all_records_processed'])
        self.assertEqual(self.recovery,[])

    def test_native_omits_only_tail_parity_data_complete_does_not_break_source_chain(self):
        capture=self.clock.ns()//1000
        self.gate.send(shard(1,capture,key=True,index=0))
        self.assertTrue(all(self.frame(2,reference=1)))
        previous=self.gate.snapshot()['frame_events'][0]
        self.assertEqual(previous['drop_reason'],'native_tail_parity_not_emitted_after_data_complete')
        self.assertTrue(previous['media_data_complete'])
        self.assertFalse(previous['all_records_processed'])
        self.assertEqual(self.gate.counts['incomplete_fec_output_frames'],1)
        self.assertEqual(self.recovery,[])

    def test_real_original_data_deadline_still_breaks_chain_even_if_some_data_sent(self):
        capture=self.clock.ns()//1000
        self.gate.send(shard(1,capture,key=True,index=0,data=4))
        self.clock.sleep(.081)
        self.assertEqual(self.gate.send(shard(1,capture,key=True,index=1,data=4)),0)
        self.assertFalse(self.gate.current['media_data_complete'])
        self.assertTrue(self.gate.needs_idr)
        self.assertEqual(self.frame(2,reference=1),[0,0,0])
        self.assertEqual(self.gate.counts['deadline_dropped_frames'],1)
        self.assertEqual(self.gate.counts['tail_parity_deadline_datagrams'],0)
        self.assertTrue(all(self.frame(3,key=True)))

    def test_multi_block_quorum_requires_every_original_data_shard_not_just_last_block(self):
        capture=self.clock.ns()//1000
        for i in range(12):
            self.gate.send(shard(1,capture,key=True,index=i,blocks=2,block=0,data=10))
        self.assertFalse(self.gate.current['media_data_complete'])
        self.gate.send(shard(1,capture,key=True,index=0,blocks=2,block=1,data=1))
        self.assertTrue(self.gate.current['media_data_complete'])
        self.assertFalse(self.gate.current['all_records_processed'])
        self.assertTrue(all(self.frame(2,reference=1)))
        self.assertEqual(self.recovery,[])

    def test_missing_original_data_in_final_block_is_not_a_parity_only_case(self):
        capture=self.clock.ns()//1000
        for i in range(12):
            self.gate.send(shard(1,capture,key=True,index=i,blocks=2,block=0,data=10))
        # Final block's original data record is missing; a parity record alone
        # is not permission to assume it was attempted or recoverable remotely.
        self.assertEqual(self.gate.send(shard(1,capture,key=True,index=1,blocks=2,block=1,data=1)),0)
        self.assertFalse(self.gate.current['media_data_complete'])
        self.assertTrue(self.gate.needs_idr)
        self.assertEqual(self.frame(2,reference=1),[0,0,0])

    def test_missing_native_frame_or_missing_shard_cannot_pass_dependent_p(self):
        self.frame(1,key=True)
        self.assertEqual(self.frame(3,reference=2),[0,0,0])
        self.frame(4,key=True)
        capture=self.clock.ns()//1000
        self.gate.send(shard(5,capture,reference=4,index=0,data=3))
        self.gate.send(shard(5,capture,reference=4,index=2,data=3))
        self.assertTrue(self.gate.needs_idr)
        self.assertEqual(self.frame(6,reference=5),[0,0,0])

    def test_metadata_bounded_numeric_and_all_times_same_host_clock(self):
        self.gate.events=__import__('collections').deque(maxlen=2)
        for frame in range(1,5): self.frame(frame,key=True)
        report=self.gate.snapshot()
        self.assertEqual(len(report['frame_events']),2)
        self.assertEqual(report['frame_events_evicted'],2)
        for row in report['frame_events']:
            self.assertLessEqual(row['capture_host_us'],row['first_socket_host_us'])
            self.assertLessEqual(row['first_socket_host_us'],row['last_socket_host_us'])
            self.assertNotIn('payload',row)
            self.assertTrue(row['complete'])

    def test_disabled_guard_keeps_legacy_packet_forwarding_and_does_not_drop_old_capture(self):
        gate=SocketVideoGate(self.sender,self.recovery.append,guard=False,clock_us=lambda:self.clock.ns()//1000)
        self.assertGreater(gate.send(shard(1,1,reference=1)),0)
        self.assertEqual(self.recovery,[])

    def test_fault_injection_remains_explicit_and_does_not_hide_subsequent_media(self):
        self.sender.video_drop_every=20
        self.sender.video_attempts=19
        values=self.frame(1,key=True)
        self.assertEqual(values[0],0)
        self.assertGreater(values[1],0)
        self.assertFalse(self.gate.needs_idr)
        self.assertEqual(self.sender.snapshot()['video']['test_dropped_datagrams'],1)


if __name__ == '__main__': unittest.main()
