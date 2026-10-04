"""Non-input publication/observation order and failure recovery; no devices."""
import json
import unittest
from scripts.probes import source_frame_coordinator as m

READY={'phase':1,'nonce':77,'app_generation':3,'ui_generation':4,
       'surface_width':1080,'surface_height':1920,'image_width':540,'image_height':960}


def observed():
    return dict(width=1080,height=1920,png_bytes=1234,png_sha256='a'*64,rpc_calls=1,
                stream_calls=0,grpc_channel_closed=True,elapsed_python_monotonic_ns=1)


def helper():
    return {'requested_authenticated_source_input':True,'helper_owned_attempt_started':True,
        'source_frame_only_observation':True,'source_observation_input_sent':False,
        'source_recovery_no_steady_window':True,'source_recovery_no_reconnect':True,
        'source_frame_observation':{'owned_before':True,'publication_attempted':True,
            'publication_returned':True,'confirmation_matched':True,'owned_after':True,
            'input_sent':False,'remote_pixels_verified_by_helper':False,
            'source_identity_verified_by_helper':False,'server_lease_independently_verified_by_helper':False,
            'atomic_hold':False,'nonce':77}}


class Markers:
    def __init__(self):self.values={'udp-ui-source-1-ready':json.dumps(READY).encode()};self.published=[]
    def read(self,name,bound):return self.values.get(name)
    def publish(self,name,body):self.published.append((name,body))


class FrameCoordinatorTests(unittest.TestCase):
    def setup_phase(self, observer=lambda ready:observed(),clock=lambda:1):
        bus=Markers();c=m.Coordinator(bus,observer,clock=clock);c.advance()
        bus.values['udp-ui-source-1-dispatched']=b'77\n'
        return bus,c

    def test_no_observer_before_matching_dispatch_and_only_noninput_request(self):
        calls=[];bus=Markers();c=m.Coordinator(bus,lambda ready:(calls.append(ready) or observed()),clock=lambda:1)
        self.assertEqual(calls,[]);c.advance();self.assertEqual(calls,[])
        self.assertEqual(bus.published,[('udp-ui-source-1-command',b'READ 77\n')]);self.assertFalse(c.advance())
        bus.values['udp-ui-source-1-dispatched']=b'77\n';c.advance()
        self.assertEqual(len(calls),1);result=c.finish(helper())
        self.assertFalse(result['source_identity_verified']);self.assertFalse(result['ownership_established_by_JSON'])
        self.assertFalse(result['source_input_sent']);self.assertFalse(result['moving_video_or_FPS_verified'])

    def test_foreign_dispatch_never_reads_or_confirms(self):
        calls=[];bus,c=self.setup_phase(lambda ready:calls.append(ready))
        bus.values['udp-ui-source-1-dispatched']=b'78\n'
        with self.assertRaises(m.Rejected):c.advance()
        self.assertEqual(calls,[]);self.assertEqual(len(bus.published),1)

    def test_publication_ambiguity_is_sticky_and_not_retried(self):
        bus=Markers();count=[]
        def fail(name,body):count.append(body);raise InterruptedError()
        bus.publish=fail;c=m.Coordinator(bus,lambda ready:observed())
        with self.assertRaises(InterruptedError):c.advance()
        with self.assertRaises(m.Rejected):c.advance()
        self.assertEqual(len(count),1)

    def test_observer_primary_error_is_not_confirmed_or_retried(self):
        original=KeyboardInterrupt()
        def fail(ready):raise original
        bus,c=self.setup_phase(fail)
        with self.assertRaises(KeyboardInterrupt) as error:c.advance()
        self.assertIs(error.exception,original);self.assertEqual(len(bus.published),1)
        with self.assertRaises(m.Rejected):c.advance()

    def test_bounded_closed_metadata_does_not_admit_pixels_or_promoted_ownership(self):
        for update in ({'pixels':b'private'},{'source_identity_verified':True},{'rpc_calls':True},
                       {'stream_calls':1},{'png_bytes':12*1024*1024+1},{'grpc_channel_closed':False},
                       {'png_sha256':'invalid'},{'width':1920},{'elapsed_python_monotonic_ns':6_000_000_001}):
            value=observed();value.update(update);bus,c=self.setup_phase(lambda ready:value)
            with self.assertRaises(m.Rejected):c.advance()
            self.assertEqual(len(bus.published),1)

    def test_clock_overrun_or_reversal_cannot_confirm(self):
        for values in ((1,6_000_000_002),(2,1)):
            sequence=iter(values);bus,c=self.setup_phase(clock=lambda:next(sequence))
            with self.assertRaises(m.Rejected):c.advance()
            self.assertEqual(len(bus.published),1)

    def test_helper_missing_changed_owner_nonce_input_or_steady_cannot_finish(self):
        bus,c=self.setup_phase();c.advance()
        for change in ('nonce','owned_after','input','steady','failure','cleanup','bool_alias','extra'):
            value=helper()
            if change=='nonce':value['source_frame_observation']['nonce']=78
            elif change=='owned_after':value['source_frame_observation']['owned_after']=False
            elif change=='input':value['source_phase_1_local_tap']={}
            elif change=='steady':value['steady_media_started_ns']=1
            elif change=='failure':value['failure_class']='InterruptedException'
            elif change=='bool_alias':value['source_frame_observation']['input_sent']=0
            elif change=='extra':value['source_frame_observation']['secret']='not-admitted'
            else:value['source_owned_marker_cleanup_failed']=True
            with self.assertRaises(m.Rejected):c.finish(value)


if __name__=='__main__':unittest.main()
