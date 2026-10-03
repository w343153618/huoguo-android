"""Owned writer deadline/nonce fixtures and bounded local UDP duplex; no phone/WAN."""
import errno
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments/moonlight-v2/transport/android-udp'))
from udp_session_sender import AuthenticatedSender, SocketVideoGate, PacingDeadline
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet
from udp_lan_worker import LanMediaWorker
from test_udp_lan_worker import bare_worker

KEY=bytes(range(32))  # Public synthetic fixture only.


class Clock:
    def __init__(self):self.now=10_000_000_000
    def ns(self):return self.now
    def wait(self,sock,seconds):self.now+=round(seconds*1e9);return False


class Writer:
    def __init__(self,failures=()):self.failures=list(failures);self.attempts=[];self.sent=[];self.closed=0
    def gettimeout(self):return 0
    def send(self,packet):
        self.attempts.append(packet)
        if self.failures:
            error=self.failures.pop(0)
            if error is not None:raise error
        self.sent.append(packet);return len(packet)
    def close(self):self.closed+=1


def blocked():return BlockingIOError(errno.EWOULDBLOCK,'synthetic would block')


def candidate(writer,clock,**kwargs):
    return AuthenticatedSender(writer,KEY,17,send_policy='owned_nonblocking_deadline',
        clock_ns=clock.ns,wait_writable=clock.wait,**kwargs)


def shard(frame,capture,key=True):
    value=bytearray(66);value[:9]=b'HGUD\x01'+bytes((3 if key else 2,0,1,2))
    struct.pack_into('>HHH',value,10,0,1,10);struct.pack_into('>QQ',value,16,frame,capture)
    struct.pack_into('>IIIIQ',value,32,10,10,80_000,0x53494d31,0)
    value[56:]=b'x'*10;return bytes(value)


class CandidateSenderChecks(unittest.TestCase):
    def test_would_block_retry_consumes_fresh_nonce_and_mutex_free_wait(self):
        clock=Clock();writer=Writer([blocked(),None,None]);sender=candidate(writer,clock)
        entered=[]
        def wait(sock,seconds):
            self.assertTrue(sender.lock.acquire(False),'socket wait cannot hold authentication mutex')
            sender.lock.release();entered.append(seconds)
            sender.send(b'priority','audio')
            clock.wait(sock,seconds)
        sender.wait_writable=wait
        sender.send(b'video','video',deadline_us=clock.ns()//1000+20_000)
        self.assertEqual([HEADER.unpack(p[:HEADER.size])[3]for p in writer.attempts],[1,2,3])
        self.assertEqual([HEADER.unpack(p[:HEADER.size])[3]for p in writer.sent],[2,3])
        replay=ReplayWindow()
        self.assertEqual([open_packet(KEY,17,p,replay,SERVER_NONCE)for p in writer.sent],[b'priority',b'video'])
        value=sender.snapshot()['video']
        self.assertEqual((value['datagrams'],value['would_block_calls'],value['would_block_retries']),(1,1,1))
        self.assertEqual(value['retry_wait_ns'],5_000_000);self.assertEqual(entered,[.005])

    def test_video_would_block_expiry_uses_existing_reference_guard_not_fatal(self):
        clock=Clock();writer=Writer([blocked()for _ in range(30)]);sender=candidate(writer,clock);recovery=[]
        gate=SocketVideoGate(sender,recovery.append,clock_us=lambda:clock.ns()//1000,max_events=8)
        self.assertEqual(gate.send(shard(1,clock.ns()//1000)),0)
        self.assertEqual(recovery,['socket_frame_deadline'])
        self.assertTrue(gate.needs_idr);self.assertFalse(gate.current['media_data_complete'])
        value=sender.snapshot()['video']
        self.assertEqual(value['datagrams'],0);self.assertEqual(value['would_block_deadline_drops'],1)
        self.assertEqual(value['deadline_rejections'],1);self.assertLessEqual(clock.ns(),10_080_000_000)
        self.assertEqual(value['max_send_syscall_ns'],0,'fake nonblocking calls do not invent a100ms timeout')
        writer.failures.clear()
        self.assertGreater(gate.send(shard(2,clock.ns()//1000)),0)
        self.assertTrue(gate.current['media_data_complete'])

    def test_priority_budget_drop_not_success_and_all_lanes_bounded(self):
        for lane in ('audio','touch_ack','network_feedback'):
            with self.subTest(lane=lane):
                clock=Clock();writer=Writer([blocked()for _ in range(8)]);sender=candidate(writer,clock)
                self.assertEqual(sender.send(b'priority',lane),0)
                value=sender.snapshot()[lane]
                self.assertEqual((value['datagrams'],value['encrypted_bytes']),(0,0))
                self.assertEqual(value['priority_budget_dropped_datagrams'],1)
                self.assertEqual(value['would_block_deadline_drops'],1)
                self.assertEqual(clock.ns(),10_010_000_000)
                self.assertEqual(len(writer.attempts),2)

    def test_no_clock_progress_still_has_finite_retry_cap(self):
        clock=Clock();writer=Writer([blocked()for _ in range(100)]);sender=candidate(writer,clock)
        sender.wait_writable=lambda sock,seconds:True
        with self.assertRaises(PacingDeadline):sender.send(b'video','video',deadline_us=clock.ns()//1000+80_000)
        value=sender.snapshot()['video']
        self.assertEqual(len(writer.attempts),sender.MAX_WOULD_BLOCK_ATTEMPTS)
        self.assertEqual(value['retry_bound_dropped_datagrams'],1)

    def test_hard_errors_and_timeout_preserve_exception_and_fixed_operation(self):
        for error in (OSError(errno.ENETUNREACH,'secret endpoint/key must not be logged'),TimeoutError('synthetic timeout'),BlockingIOError(errno.EPERM,'not wouldblock')):
            with self.subTest(error=type(error).__name__):
                clock=Clock();writer=Writer([error]);sender=candidate(writer,clock)
                with self.assertRaises(type(error))as raised:sender.send(b'video','video',deadline_us=clock.ns()//1000+80_000)
                self.assertIs(raised.exception,error)
                self.assertEqual(error.huoguo_udp_failure_operation,'udp_socket_send')
                self.assertEqual(error.huoguo_udp_failure_lane,'video')
                self.assertEqual(sender.snapshot()['video']['send_errors'],1)
                self.assertEqual(sender.snapshot()['video']['would_block_calls'],0)

    def test_cancel_and_owned_close_are_bounded_and_exactly_once(self):
        clock=Clock();writer=Writer([blocked()]);cancel=threading.Event();sender=candidate(writer,clock,owns_socket=True,cancelled=cancel.is_set)
        def wait(sock,seconds):cancel.set();clock.wait(sock,seconds)
        sender.wait_writable=wait
        with self.assertRaises(OSError)as raised:sender.send(b'video','video',deadline_us=clock.ns()//1000+80_000)
        self.assertEqual(raised.exception.errno,errno.ECANCELED)
        self.assertEqual(len(writer.attempts),1)
        sender.close();sender.close();self.assertEqual(writer.closed,1)
        self.assertTrue(sender.policy_snapshot()['owned_socket_close_confirmed'])
        legacy=AuthenticatedSender(Writer(),KEY,17);legacy.close();self.assertEqual(legacy.socket.closed,0)

    def test_candidate_requires_nonblocking_writer_and_real_video_deadline(self):
        writer=Writer();clock=Clock()
        writer.gettimeout=lambda:.1
        with self.assertRaises(ValueError):candidate(writer,clock)
        writer.gettimeout=lambda:0
        with self.assertRaises(ValueError):candidate(writer,clock).send(b'video','video')


class OwnedDupAndFailureChecks(unittest.TestCase):
    def test_real_owned_dup_preserves_reader_timeout_endpoint_buffers_and_duplex(self):
        reader=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);peer=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        stop=threading.Event();errors=[];packets=[];alive=[];threads=[];sender=None
        try:
            reader.bind(('127.0.0.1',0));peer.bind(('127.0.0.1',0));reader.connect(peer.getsockname());peer.connect(reader.getsockname())
            reader.settimeout(.1);peer.settimeout(.1)
            original_endpoint=reader.getsockname();original_sndbuf=reader.getsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF)
            self.assertTrue(fcntl.fcntl(reader.fileno(),fcntl.F_GETFL)&os.O_NONBLOCK)
            worker=bare_worker();worker.udp=reader;writer=worker._create_send_wrapper()
            self.assertNotEqual(reader.fileno(),writer.fileno());self.assertEqual(reader.gettimeout(),.1);self.assertEqual(writer.gettimeout(),0)
            self.assertEqual(reader.getsockname(),original_endpoint);self.assertEqual(writer.getsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF),original_sndbuf)
            sender=AuthenticatedSender(writer,KEY,17,send_policy='owned_nonblocking_deadline',owns_socket=True,cancelled=stop.is_set)
            def receive_peer():
                try:
                    while not stop.is_set():
                        try:packet=peer.recv(1401)
                        except socket.timeout:continue
                        packets.append(packet);peer.send(b'ALIVE')
                except Exception as error:
                    if not stop.is_set():errors.append(error)
            def receive_reader():
                try:
                    while not stop.is_set():
                        try:data=reader.recv(1401)
                        except socket.timeout:continue
                        alive.append(data)
                except Exception as error:
                    if not stop.is_set():errors.append(error)
            for operation in (receive_peer,receive_reader):
                thread=threading.Thread(target=operation,daemon=True);threads.append(thread);thread.start()
            def transmit(lane):
                try:
                    for i in range(8):sender.send((lane+str(i)).encode(),lane,deadline_us=time.monotonic_ns()//1000+80_000 if lane=='video'else None)
                except Exception as error:errors.append(error)
            producers=[]
            for lane in sorted(sender.LANES):
                thread=threading.Thread(target=transmit,args=(lane,),daemon=True);producers.append(thread);thread.start()
            for thread in producers:thread.join(2);self.assertFalse(thread.is_alive(),'bounded sends must not deadlock concurrent duplex')
            deadline=time.monotonic()+2
            while len(packets)<sum(v['datagrams']for v in sender.snapshot().values())and time.monotonic()<deadline:time.sleep(.01)
            stop.set()
            for thread in threads:thread.join(.3);self.assertFalse(thread.is_alive())
            self.assertEqual(errors,[]);self.assertTrue(packets);self.assertTrue(alive)
            replay=ReplayWindow()
            decoded=[open_packet(KEY,17,p,replay,SERVER_NONCE)for p in packets]
            self.assertEqual(len(decoded),sum(v['datagrams']for v in sender.snapshot().values()))
            self.assertEqual(len({HEADER.unpack(p[:HEADER.size])[3]for p in packets}),len(packets))
            sender.close();sender.close();self.assertEqual(writer.fileno(),-1)
            self.assertGreaterEqual(reader.fileno(),0);self.assertEqual(reader.gettimeout(),.1)
            peer.send(b'READER_STILL_OWNED');self.assertEqual(reader.recv(64),b'READER_STILL_OWNED')
        finally:
            stop.set()
            if sender is not None:sender.close()
            reader.close();peer.close()
            for thread in threads:thread.join(.3)

    def test_real_closed_owned_wrapper_wait_keeps_value_error_and_fixed_operation(self):
        reader=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);peer=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        sender=None
        class OneBlockedWriter:
            # Only the first send's would-block is injected. The owned FD,
            # close and select negative-FD error below are real socket APIs.
            def __init__(self,sock):self.sock=sock;self.closed=0
            def gettimeout(self):return self.sock.gettimeout()
            def fileno(self):return self.sock.fileno()
            def send(self,packet):raise blocked()
            def close(self):self.closed+=1;self.sock.close()
        try:
            reader.bind(('127.0.0.1',0));peer.bind(('127.0.0.1',0));reader.connect(peer.getsockname());peer.connect(reader.getsockname());reader.settimeout(.1)
            worker=bare_worker();worker.udp=reader;owned=OneBlockedWriter(worker._create_send_wrapper())
            sender=AuthenticatedSender(owned,KEY,17,send_policy='owned_nonblocking_deadline',owns_socket=True)
            def closed_during_wait(sock,seconds):
                sender.close()  # Exactly-once owned close racing the unlocked wait.
                return AuthenticatedSender._wait_writable(sock,seconds)
            sender.wait_writable=closed_during_wait
            with self.assertRaises(ValueError)as raised:sender.send(b'video','video',deadline_us=time.monotonic_ns()//1000+80_000)
            self.assertEqual(raised.exception.huoguo_udp_failure_operation,'udp_send_wait')
            self.assertEqual(raised.exception.huoguo_udp_failure_lane,'video')
            self.assertEqual(owned.fileno(),-1);self.assertEqual(owned.closed,1)
            sender.close();self.assertEqual(owned.closed,1)
            self.assertGreaterEqual(reader.fileno(),0);self.assertEqual(reader.gettimeout(),.1)
            self.assertEqual(sender.snapshot()['video']['datagrams'],0)
            worker._record_failure('udp_video',raised.exception)
            self.assertEqual(worker.failure,'ValueError');self.assertEqual(worker.failure_operation,'udp_send_wait')
        finally:
            if sender is not None:sender.close()
            reader.close();peer.close()

    def test_worker_first_failure_metadata_is_bounded_and_never_exception_text(self):
        worker=bare_worker();first=OSError(errno.ENETUNREACH,'secret-key-and-address')
        first.huoguo_udp_failure_operation='udp_socket_send';first.huoguo_udp_failure_lane='video'
        worker._record_failure('udp_video',first)
        worker._record_failure('udp_ping',TimeoutError('private-credential'),'udp_socket_send')
        self.assertEqual((worker.failure,worker.failure_role,worker.failure_operation),('OSError','udp_video','udp_socket_send'))
        for i in range(12):worker._record_failure('malicious-role',ValueError('account-password'),'unsafe-operation')
        self.assertEqual(len(worker.failure_events),8);self.assertEqual(worker.failure_events_evicted,6)
        self.assertEqual(worker.failure_events[-1]['role'],'unknown_owned_worker')
        self.assertEqual(worker.failure_events[-1]['operation'],'not_instrumented')
        raw=json.dumps(list(worker.failure_events));self.assertNotIn('password',raw);self.assertNotIn('credential',raw);self.assertNotIn('secret',raw)


if __name__=='__main__':unittest.main()
