"""Explicit non-input observer phase; no device, credential or SDK imports.

The caller owns actual instrumentation/marker provenance and a bounded reader.
This state machine never infers a server lease, source identity or pixel content
from a valid marker/report. It is separate from native source target commands.
"""
import re
import time

from scripts.probes import source_authenticated_driver as source

HELPER_SHA256 = '82f4614679e828bdd2738838270a97f188bed11dc19d04ecd2dc2fa0203968d7'


class Rejected(ValueError):
    pass


def projection(value, ready):
    fields = {'width','height','png_bytes','png_sha256','rpc_calls','stream_calls',
              'grpc_channel_closed','elapsed_python_monotonic_ns'}
    if (type(value) is not dict or set(value)!=fields
            or any(type(value[k]) is not int for k in fields-{'png_sha256','grpc_channel_closed'})
            or type(value['png_sha256']) is not str
            or re.fullmatch('[0-9a-f]{64}',value['png_sha256']) is None
            or value['grpc_channel_closed'] is not True
            or value['rpc_calls']!=1 or value['stream_calls']!=0
            or not 1<=value['png_bytes']<=12*1024*1024
            or not 16<=value['width']<=8192 or not 16<=value['height']<=8192
            or value['width']*ready['image_height']!=value['height']*ready['image_width']
            or not 0<=value['elapsed_python_monotonic_ns']<=6_000_000_000):
        raise Rejected('source_frame_observation_rejected')
    return dict(value)


class Coordinator:
    def __init__(self, markers, observe, *, clock=time.monotonic_ns):
        if not callable(observe) or not callable(clock):
            raise Rejected('source_frame_callback_required')
        self.markers,self.observe,self.clock=markers,observe,clock
        self.phase,self.ready,self.observation,self.failed=1,None,None,False

    def advance(self):
        if self.failed: raise Rejected('source_frame_failed_no_retry')
        if self.phase==3:return False
        files=source.names(1)
        try:
            if self.ready is None:
                raw=self.markers.read(files['ready'],2048)
                if raw is None:return False
                self.ready=source.parse_ready(raw,1)
                self.markers.publish(files['command'],('READ %d\n'%self.ready['nonce']).encode('ascii'))
                return True
            raw=self.markers.read(files['dispatched'],32)
            if raw is None:return False
            if raw!=source.nonce_body(self.ready):raise Rejected('source_frame_nonce_changed')
            before=self.clock()
            observed=self.observe(dict(self.ready))
            after=self.clock()
            if (type(before) is not int or type(after) is not int
                    or not 0<=after-before<=6_000_000_000):
                raise Rejected('source_frame_observer_budget')
            self.observation=projection(observed,self.ready)
            self.markers.publish(files['verified'],source.nonce_body(self.ready))
            self.phase=3
            return True
        except BaseException:
            self.failed=True
            raise

    def finish(self, helper):
        if self.failed or self.phase!=3 or self.observation is None:
            raise Rejected('source_frame_incomplete')
        expected={'owned_before':True,'publication_attempted':True,'publication_returned':True,
            'confirmation_matched':True,'owned_after':True,'input_sent':False,
            'remote_pixels_verified_by_helper':False,'source_identity_verified_by_helper':False,
            'server_lease_independently_verified_by_helper':False,'atomic_hold':False,
            'nonce':self.ready['nonce']}
        frame=helper.get('source_frame_observation') if type(helper) is dict else None
        frame_matches=(type(frame) is dict and set(frame)==set(expected)
            and all(type(frame[k]) is type(v) and frame[k]==v for k,v in expected.items()))
        if (type(helper) is not dict or 'failure_class' in helper
                or helper.get('requested_authenticated_source_input') is not True
                or helper.get('helper_owned_attempt_started') is not True
                or helper.get('source_frame_only_observation') is not True
                or helper.get('source_observation_input_sent') is not False
                or helper.get('source_recovery_no_steady_window') is not True
                or helper.get('source_recovery_no_reconnect') is not True
                or helper.get('source_owned_marker_cleanup_failed',False) is not False
                or any(k in helper for k in ('source_phase_1_local_tap','source_phase_2_local_tap',
                                             'steady_media_started_ns'))
                or not frame_matches):
            raise Rejected('source_frame_helper_readback_rejected')
        return {'schema':'owner-source-frame-phase-v1','metadata':dict(self.observation),
            'matching_helper_checks_reported':True,'source_input_sent':False,
            'ownership_established_by_JSON':False,'actual_server_lease_independently_verified':False,
            'source_identity_verified':False,'pixels_visually_reviewed':False,
            'moving_video_or_FPS_verified':False,'remote_UI_runner_started':False}
