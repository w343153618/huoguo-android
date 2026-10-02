"""Actual-send scheduling and reference-chain safety, fake clocks/sockets only."""
from pathlib import Path
from contextlib import redirect_stderr
import io
import struct
import sys
import threading
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]/'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from udp_session_sender import AuthenticatedSender, SocketPacer, SocketVideoGate, PacingDeadline
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet
import run_phone_udp
import udp_session_sender


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


class SocketWaitIsolationChecks(unittest.TestCase):
    def make_sender(self, wait_enabled=True, wait=None):
        clock=Clock(); sock=Socket(clock)
        pacer=SocketPacer(1_000_000,clock=clock,wait=wait or clock.sleep,wait_enabled=wait_enabled)
        sender=AuthenticatedSender(sock,bytes(range(32)),3,pacer=pacer,clock_ns=clock.ns)
        recovery=[]
        gate=SocketVideoGate(sender,recovery.append,clock_us=lambda:clock.ns()//1000)
        return clock,sock,pacer,sender,gate,recovery

    def test_omitted_wait_selection_is_equivalent_to_explicit_current_default(self):
        def trace(explicit):
            clock=Clock(); sock=Socket(clock)
            kwargs={'clock':clock,'wait':clock.sleep}
            if explicit: kwargs['wait_enabled']=True
            pacer=SocketPacer(1_000_000,**kwargs)
            sender=AuthenticatedSender(sock,bytes(range(32)),3,pacer=pacer,clock_ns=clock.ns)
            for _ in range(4): sender.send(b'x'*1000,'video')
            sender.send(b'a'*100,'audio')
            clock.sleep(.01)
            sender.send(b'x'*1000,'video')
            return sock.sent,pacer.snapshot(),sender.snapshot()
        self.assertEqual(trace(False),trace(True))

    def test_wait_disabled_never_calls_wait_but_retains_byte_reservations_and_priority_debt(self):
        def no_wait(_): raise AssertionError('socket timed wait must be disabled')
        clock,sock,pacer,sender,_,_=self.make_sender(False,no_wait)
        sender.send(b'x'*1000,'video')
        sender.send(b'a'*100,'audio')
        for _ in range(9): sender.send(b'x'*1000,'video')
        report=pacer.snapshot()
        self.assertTrue(all(at == 10. for at,_ in sock.sent))
        self.assertFalse(report['wait_enabled'])
        self.assertFalse(report['socket_send_spacing_enforced'])
        self.assertEqual(report['video_waits'],0)
        self.assertEqual(report['video_wait_us'],0)
        self.assertEqual(report['video_reservations'],10)
        self.assertEqual(report['reserved_video_wire_bytes'],10*1068)
        self.assertEqual(report['priority_wire_bytes'],168)
        self.assertEqual(report['skipped_video_waits'],9)
        self.assertGreater(report['skipped_planned_video_wait_us'],0)
        self.assertAlmostEqual(pacer.next_at-10,(10*1068+168)*8/1_000_000)

    def test_disabling_wait_does_not_disable_admission_deadline_or_grow_debt_after_rejections(self):
        for enabled in (True,False):
            with self.subTest(wait_enabled=enabled):
                clock,sock,pacer,sender,_,_=self.make_sender(enabled)
                sender.send(b'x'*1000,'video')
                reserved=pacer.next_at; sequence=sender.sequence
                for _ in range(3):
                    with self.assertRaises(PacingDeadline):
                        sender.send(b'x'*1000,'video',deadline_us=clock.ns()//1000+1_000)
                self.assertEqual(pacer.next_at,reserved)
                self.assertEqual(sender.sequence,sequence)
                self.assertEqual(len(sock.sent),1)
                self.assertEqual(pacer.snapshot()['deadline_rejections'],3)

    def test_wait_disabled_still_rejects_expired_data_and_requires_complete_idr(self):
        for enabled in (True,False):
            with self.subTest(wait_enabled=enabled):
                clock,sock,pacer,sender,gate,recovery=self.make_sender(enabled)
                def send_frame(frame,key=False,reference=0):
                    capture=clock.ns()//1000
                    return [gate.send(shard(frame,capture,key=key,reference=reference,index=i))
                            for i in range(3)]
                self.assertTrue(all(send_frame(1,key=True)))
                capture=clock.ns()//1000
                self.assertGreater(gate.send(shard(2,capture,reference=1,index=0,data=3)),0)
                clock.sleep(.081)
                self.assertEqual(gate.send(shard(2,capture,reference=1,index=1,data=3)),0)
                self.assertTrue(gate.needs_idr)
                self.assertEqual(send_frame(3,reference=2),[0,0,0])
                # A partial IDR is not sufficient to release its P chain.
                capture=clock.ns()//1000
                self.assertGreater(gate.send(shard(4,capture,key=True,index=0,data=3)),0)
                self.assertEqual(send_frame(5,reference=4),[0,0,0])
                self.assertTrue(gate.needs_idr)
                self.assertTrue(all(send_frame(6,key=True)))
                self.assertFalse(gate.needs_idr)
                self.assertTrue(all(send_frame(7,reference=6)))
                self.assertIn('socket_frame_deadline',recovery)
                self.assertIn('incomplete_native_frame',recovery)
                self.assertTrue(gate.snapshot()['guard_enabled'])

    def test_wait_disabled_preserves_parity_only_expiry_reference_and_nonce_policy(self):
        clock,sock,_,sender,gate,recovery=self.make_sender(False)
        capture=clock.ns()//1000
        self.assertGreater(gate.send(shard(1,capture,key=True,index=0)),0)
        clock.sleep(.081)
        for index in (1,2):
            self.assertEqual(gate.send(shard(1,capture,key=True,index=index)),0)
        self.assertFalse(gate.needs_idr)
        capture=clock.ns()//1000
        self.assertTrue(all(gate.send(shard(2,capture,reference=1,index=i)) for i in range(3)))
        self.assertEqual(recovery,[])
        sequences=[HEADER.unpack(packet[:HEADER.size])[3] for _,packet in sock.sent]
        self.assertEqual(sequences,list(range(1,len(sequences)+1)))
        self.assertEqual(gate.counts['deadline_dropped_frames'],0)
        self.assertEqual(gate.counts['tail_parity_deadline_datagrams'],2)

    def test_wait_disabled_cannot_bypass_payload_or_frame_bounds(self):
        clock,sock,pacer,sender,gate,_=self.make_sender(False)
        for payload in (b'',b'x'*1081):
            with self.assertRaises(ValueError): sender.send(payload,'video')
        with self.assertRaises(ValueError):
            gate.send(shard(1,clock.ns()//1000,key=True,blocks=104))
        self.assertEqual(sock.sent,[])
        self.assertEqual(sender.sequence,1)
        self.assertEqual(pacer.snapshot()['video_reservations'],0)

    def test_wait_disabled_retains_deadline_recheck_after_seal_and_reference_recovery(self):
        clock,sock,_,sender,gate,recovery=self.make_sender(False)
        original=udp_session_sender.seal
        def late_seal(*args):
            packet=original(*args)
            clock.sleep(.081)
            return packet
        capture=clock.ns()//1000
        with patch.object(udp_session_sender,'seal',side_effect=late_seal):
            self.assertEqual(gate.send(shard(1,capture,key=True,index=0,data=3)),0)
        self.assertEqual(sock.sent,[])
        self.assertEqual(sender.sequence,2) # Seal consumed a nonce; rejection cannot reuse it.
        self.assertTrue(gate.needs_idr)
        self.assertIn('socket_frame_deadline',recovery)
        self.assertEqual(sender.snapshot()['video']['send_errors'],0)

    def test_reporting_distinguishes_skipped_socket_wait_from_disabled_guard_or_missing_native_readback(self):
        for enabled in (True,False):
            with self.subTest(wait_enabled=enabled):
                _,_,pacer,_,gate,_=self.make_sender(enabled)
                events=[{'event':'summary','wire_bitrate':1_000_000},
                        {'event':'frame_output','actual_sleep_us':4_000}]
                report=run_phone_udp.pacing_configuration(pacer,gate,events,1_000_000)
                self.assertEqual(report['socket_wait_enabled'],enabled)
                self.assertTrue(report['socket_deadline_reference_guard_enabled'])
                self.assertTrue(report['socket_reservation_and_serialization_checks_enabled'])
                self.assertTrue(report['native_pacing_requested'])
                self.assertTrue(report['native_wire_budget_readback_matches'])
                self.assertTrue(report['native_timed_wait_observed'])
                self.assertFalse(report['native_bounds_modified_by_socket_wait_experiment'])
                empty=run_phone_udp.pacing_configuration(pacer,gate,[],1_000_000)
                self.assertIsNone(empty['native_timed_wait_observed'])
                self.assertIsNone(empty['native_wire_budget_readback_matches'])
                mismatch=run_phone_udp.pacing_configuration(pacer,gate,[
                    {'event':'summary','wire_bitrate':2_000_000}],1_000_000)
                self.assertFalse(mismatch['native_wire_budget_readback_matches'])

    def test_cli_refuses_wait_experiment_without_existing_guard_bundle_before_any_device_access(self):
        argv=['run_phone_udp.py','--disable-socket-wait','--bind-ip','192.168.9.128',
              '--peer-ip','192.168.9.149','--packetizer','unused','--source','unit','--output','unused.json']
        with patch.object(sys,'argv',argv), patch.object(run_phone_udp.subprocess,'run') as run, \
                patch.object(run_phone_udp.subprocess,'Popen') as spawn, \
                patch.object(run_phone_udp.socket,'socket') as sock:
            output=io.StringIO()
            with redirect_stderr(output),self.assertRaises(SystemExit) as error:
                run_phone_udp.main()
            self.assertEqual(error.exception.code,2)
            self.assertIn('--disable-socket-wait requires --socket-pacing',output.getvalue())
            run.assert_not_called(); spawn.assert_not_called(); sock.assert_not_called()

    def test_wait_selection_requires_actual_boolean(self):
        for invalid in (0,1,None,'false'):
            with self.assertRaises(ValueError): SocketPacer(1_000_000,wait_enabled=invalid)


if __name__ == '__main__': unittest.main()
