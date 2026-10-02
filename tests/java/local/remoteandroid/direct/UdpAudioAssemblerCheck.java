package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import java.util.List;

/** Pure protocol checks. Does not prove AAC decode, audible output or lip sync. */
public final class UdpAudioAssemblerCheck {
    private static byte[] fragment(long pts,int id,boolean config,byte[] data,int index){
        int count=(data.length+1039)/1040,offset=index*1040,size=Math.min(1040,data.length-offset);
        return ByteBuffer.allocate(40+size).order(ByteOrder.BIG_ENDIAN).putInt(0x48475541).put((byte)1)
            .put((byte)(config?1:0)).putShort((short)40).putLong(pts).putInt(id).putInt(0x00616163)
            .putInt(data.length).putShort((short)index).putShort((short)count).putInt(offset).putInt(size)
            .put(data,offset,size).array();
    }
    private static void check(boolean ok,String reason){if(!ok)throw new AssertionError(reason);}
    public static void main(String[] args){
        long start=1_000_000_000L;byte[] data=new byte[2081];for(int i=0;i<data.length;i++)data[i]=(byte)i;
        UdpAudioAssembler a=new UdpAudioAssembler();
        a.accept(fragment(100,1,false,data,2),start);a.accept(fragment(100,1,false,data,0),start+1);
        a.accept(fragment(100,1,false,data,0),start+2);a.accept(fragment(100,1,false,data,1),start+3);
        check(a.poll(start+4).isEmpty(),"reorder hold");
        List<UdpAudioAssembler.Frame> frames=a.poll(start+10_000_004L);
        check(frames.size()==1&&Arrays.equals(frames.get(0).data,data),"out-of-order complete reassembly");
        a.accept(fragment(100,1,false,data,2),start+20_000_000L);check(a.poll(start+40_000_000L).isEmpty(),"completed duplicate");
        check(a.duplicate==2,"duplicate counts");
        a.accept(fragment(200,2,false,data,0),start+40_000_000L);
        a.poll(start+120_000_000L);check(a.expired==1&&a.pendingCount()==0,"80 ms deadline");
        a.accept(fragment(200,2,false,data,1),start+120_000_001L);check(a.pendingCount()==0,"expired fragments cannot resurrect");
        byte[] malformed=fragment(300,3,false,data,0);malformed[7]=39;a.accept(malformed,start+130_000_000L);
        check(a.malformed==1&&a.pendingCount()==0,"malformed header rejected");
        byte[] collision=fragment(400,4,false,data,0);a.accept(collision,start+140_000_000L);
        collision[40]^=1;a.accept(collision,start+140_000_001L);check(a.malformed==2,"conflicting duplicate rejected");
        byte[] asc={(byte)0x11,(byte)0x90};a.accept(fragment(0,5,true,asc,0),start+150_000_000L);
        frames=a.poll(start+150_000_000L);check(frames.size()==1&&frames.get(0).config,"immediate codec configuration");
        check(UdpAudioAssembler.supportedConfig(asc),"48 kHz stereo AAC-LC config");
        check(!UdpAudioAssembler.supportedConfig(new byte[]{0x12,0x10}),"44.1 kHz not silently treated as 48 kHz");
        check(!UdpAudioAssembler.supportedConfig(new byte[0]),"zero configuration rejected");
        a.accept(fragment(90,6,false,new byte[]{1},0),start+160_000_000L);
        check(a.poll(start+171_000_000L).isEmpty()&&a.reorderDrops==1,"older PTS not submitted after newer");
        UdpAudioAssembler bounded=new UdpAudioAssembler();
        for(int id=1;id<=100;id++)bounded.accept(fragment(id,id,false,data,0),start+id);
        check(bounded.pendingCount()==32&&bounded.evicted==68,"pending bounded under loss");
        byte[] badPts=fragment(Long.MAX_VALUE,101,false,new byte[]{1},0);bounded.accept(badPts,start+101);
        check(bounded.malformed==1,"timestamp multiplication overflow rejected");
        UdpAudioAssembler ordered=new UdpAudioAssembler();
        ordered.accept(fragment(200,2,false,new byte[]{2},0),start);
        ordered.accept(fragment(100,1,false,new byte[]{1},0),start+5_000_000L);
        check(ordered.poll(start+10_000_000L).isEmpty(),"younger earlier PTS holds later frame within reorder window");
        frames=ordered.poll(start+15_000_000L);
        check(frames.size()==2&&frames.get(0).ptsUs==100&&frames.get(1).ptsUs==200,"multiple records ordered by PTS");
        byte[] zero=fragment(1,3,false,new byte[]{1},0);zero=Arrays.copyOf(zero,40);
        ordered.accept(zero,start+16_000_000L);check(ordered.malformed==1,"zero payload rejected");
        System.out.println("UdpAudioAssemblerCheck PASS: reassembly/reorder/duplicate/config/malformed/expiry/bounds/overflow");
    }
}
