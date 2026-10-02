package local.remoteandroid.direct;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;

/** Offline checks of actual wire encoders and metadata inbox state; no Android calls. */
public final class UdpFeedbackDiagnosticProbe {
    private static int checks;
    private interface Invalid { void run() throws Exception; }
    private static void check(boolean okay,String name){if(!okay)throw new AssertionError(name);checks++;}
    private static void rejects(Invalid action,String name)throws Exception{
        boolean rejected=false;try{action.run();}catch(IOException expected){rejected=true;}check(rejected,name);
    }
    private static UdpVideoProbe.VideoFrame frame(long pts,boolean idr,int size){
        return new UdpVideoProbe.VideoFrame(new byte[size],540,960,idr?8:0,size-20,pts,123456789L,idr,0);
    }
    public static void main(String[] args)throws Exception{
        long[] values={1,100,200,20,24000,2,3,4,5,6,700,800,90};
        byte[] payload=UdpVideoProbe.networkFeedbackPayload(values);
        ByteBuffer decoded=ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN);
        check(payload.length==112,"HGUF exact length");
        check(decoded.getInt()==0x48475546&&decoded.getInt()==1,"HGUF magic and version");
        for(int i=0;i<13;i++)check(decoded.getLong()==values[i],"HGUF column "+i);
        rejects(()->UdpVideoProbe.networkFeedbackPayload(null),"null feedback rejected");
        rejects(()->UdpVideoProbe.networkFeedbackPayload(new long[12]),"truncated feedback rejected");
        for(int i=0;i<13;i++){
            final long[] bad=values.clone();bad[i]=-1;
            rejects(()->UdpVideoProbe.networkFeedbackPayload(bad),"negative column "+i+" rejected");
        }
        final long[] zero=values.clone();zero[0]=0;
        rejects(()->UdpVideoProbe.networkFeedbackPayload(zero),"zero sequence rejected");
        final long[] reversed=values.clone();reversed[2]=99;
        rejects(()->UdpVideoProbe.networkFeedbackPayload(reversed),"regressed interval rejected");
        // A signed-looking ID/time token is intentionally opaque and copied exactly.
        byte[] ping=ByteBuffer.allocate(16).putInt(0x48475051).putInt(0xfedcba98).putLong(Long.MIN_VALUE+123).array();
        byte[] pong=UdpVideoProbe.pingReply(ping,300,305);
        ByteBuffer reply=ByteBuffer.wrap(pong);
        check(pong.length==32&&reply.getInt()==0x48475052,"HGPR exact length/magic");
        check(Arrays.equals(Arrays.copyOfRange(ping,4,16),Arrays.copyOfRange(pong,4,16)),"opaque token copied exactly");
        reply.position(16);check(reply.getLong()==300&&reply.getLong()==305,"phone arrival/send only");
        rejects(()->UdpVideoProbe.pingReply(new byte[16],300,305),"unknown ping magic rejected");
        rejects(()->UdpVideoProbe.pingReply(Arrays.copyOf(ping,15),300,305),"short ping rejected");
        rejects(()->UdpVideoProbe.pingReply(ping,305,300),"phone time regression rejected");

        check(UdpVideoProbe.parseContentHintFps(0)==0,"zero explicitly clears content hint");
        check(UdpVideoProbe.parseContentHintFps(60)==60,"60 Hz content hint accepted");
        check(UdpVideoProbe.parseContentHintFps(120L)==120,"120 Hz integral number accepted");
        check(UdpVideoProbe.parseContentHintFps(240.0)==240,"bounded integral double accepted");
        rejects(()->UdpVideoProbe.parseContentHintFps(null),"null content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps("60"),"content hint string coercion rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(true),"boolean content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(-1),"negative explicit content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(241),"oversized content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(59.94),"fractional content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(Double.NaN),"NaN content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(Double.POSITIVE_INFINITY),"infinite content hint rejected");
        rejects(()->UdpVideoProbe.parseContentHintFps(Long.MAX_VALUE),"huge content hint rejected");

        check(UdpVideoProbe.parseSurfaceSubmitLeadMs(0)==0,"submission wait default disabled");
        check(UdpVideoProbe.parseSurfaceSubmitLeadMs(8)==8,"8ms submission lead accepted");
        check(UdpVideoProbe.parseSurfaceSubmitLeadMs(16L)==16,"16ms submission lead accepted");
        check(UdpVideoProbe.parseSurfaceSubmitLeadMs(16.0)==16,"integral submission lead number accepted");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(null),"null submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs("16"),"submission lead string coercion rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(true),"boolean submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(-1),"negative submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(80),"unsupported submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(8.5),"fractional submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(Double.NaN),"NaN submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(Double.POSITIVE_INFINITY),"infinite submission lead rejected");
        rejects(()->UdpVideoProbe.parseSurfaceSubmitLeadMs(Long.MAX_VALUE),"huge submission lead rejected");

        long ready=1_000_000_000L;
        check(MainActivity.probeVideoSubmissionParkNs(ready+60_000_000L,ready,ready,0)==0,"zero lead preserves release without wait");
        check(MainActivity.probeVideoSubmissionParkNs(ready+60_000_000L,ready,ready,15)==0,"unsupported lead cannot create wait");
        check(MainActivity.probeVideoSubmissionParkNs(ready+60_000_000L,ready,ready,16)==2_000_000L,"individual park bounded to2ms");
        check(MainActivity.probeVideoSubmissionParkNs(ready+16_000_000L,ready,ready,16)==0,"release at requested lead");
        check(MainActivity.probeVideoSubmissionParkNs(ready+16_100_000L,ready,ready,16)==100_000L,"last park respects lead boundary");
        check(MainActivity.probeVideoSubmissionParkNs(ready+7_000_000L,ready,ready,8)==0,"already near target releases unchanged");
        check(MainActivity.probeVideoSubmissionParkNs(ready-1,ready,ready,16)==0,"past target cannot wait");
        check(MainActivity.probeVideoSubmissionParkNs(ready+200_000_000L,ready+79_900_000L,ready,16)==100_000L,"last park respects80ms output budget");
        check(MainActivity.probeVideoSubmissionParkNs(ready+200_000_000L,ready+80_000_000L,ready,16)==0,"80ms budget exhaustion releases original target");
        check(MainActivity.probeVideoSubmissionParkNs(ready+200_000_000L,ready+90_000_000L,ready,16)==0,"overslept output never adds another park");
        PlaybackClock shared=new PlaybackClock(60,0,true);shared.observe(0,ready);
        shared.videoDeadline(0,ready+150_000_000L);long audioDeadline=shared.audioDeadline(0);
        for(int i=0;i<100;i++)shared.deadline(0);
        check(shared.audioDeadline(0)==audioDeadline,"park-time pure deadline reads preserve shared audio mapping");

        UdpVideoProbe.VideoInbox disabled=new UdpVideoProbe.VideoInbox();
        disabled.offer(frame(1,true,32));disabled.success(disabled.take());disabled.fail(0);
        check(disabled.events.isEmpty(),"diagnostics default disabled");
        UdpVideoProbe.VideoInbox q=new UdpVideoProbe.VideoInbox(true);
        check(q.offer(frame(1,true,32)),"IDR admitted with diagnostics");
        UdpVideoProbe.InboxEvent admission=q.events.getLast();
        check(admission.event.equals("idr_admitted")&&admission.ptsUs==1&&admission.timeNs>0,"admission metadata");
        UdpVideoProbe.VideoFrame idr=q.take();q.success(idr);
        check(q.events.getLast().event.equals("recovered")&&q.events.getLast().reason.equals("initial_idr_committed"),"initial recovery logged");
        q.fail(idr,"codec_input_timeout");
        UdpVideoProbe.InboxEvent loss=q.events.getLast();
        check(loss.event.equals("chain_lost")&&loss.reason.equals("codec_input_timeout")&&q.epoch==1&&q.needsIdr(),"failure reason and epoch logged");
        check(!q.offer(frame(2,false,32)),"diagnostics preserve reference gating");
        q.offer(frame(3,true,32));q.fail(idr,"stale_codec_epoch");
        check(q.events.getLast().event.equals("stale_fail_ignored")&&q.queue.size()==1,"old epoch cannot invalidate fresh IDR");
        q.success(q.take());check(q.events.getLast().reason.equals("epoch_idr_committed"),"new epoch recovery logged");
        for(int i=0;i<300;i++)q.fail(q.epoch);
        check(q.events.size()==256&&q.eventsEvicted>0,"epoch metadata storage bounded");
        q.close();check(!q.offer(frame(99,true,32)),"closed inbox behavior unchanged");
        System.out.println("PASS "+checks+" UDP feedback and bounded metadata checks (offline)");
    }
}
