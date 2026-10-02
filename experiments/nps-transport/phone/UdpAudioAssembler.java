package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pure bounded audio reassembly. Call only after authenticated peer admission. */
final class UdpAudioAssembler {
    static final int MAGIC=0x48475541,HEADER=40,FRAGMENT=1040,MAX_RECORD=65536,AAC=0x00616163;
    static final long DEADLINE_NS=80_000_000L,REORDER_NS=10_000_000L,PTS_MASK=(1L<<61)-1;
    private static final int MAX_PENDING=32,MAX_COMPLETE=16,MAX_RECENT=256;
    long acceptedFragments,malformed,duplicate,expired,evicted,completed,reorderDrops;
    private final LinkedHashMap<Long,Pending> pending=new LinkedHashMap<>();
    private final LinkedHashMap<Long,Boolean> recent=new LinkedHashMap<>();
    private final List<Frame> ready=new ArrayList<>();
    private long lastReleasedPts=-1;

    static final class Frame {
        final long id,ptsUs,firstArrivalNs,completeNs;final boolean config;final byte[] data;
        Frame(long id,long pts,long first,long complete,boolean config,byte[] data){
            this.id=id;ptsUs=pts;firstArrivalNs=first;completeNs=complete;this.config=config;this.data=data;
        }
    }
    private static final class Pending {
        final long id,pts,first;final boolean config;final byte[] data;final boolean[] fragments;
        int filled;
        Pending(long id,long pts,long first,boolean config,int total,int count){
            this.id=id;this.pts=pts;this.first=first;this.config=config;data=new byte[total];fragments=new boolean[count];
        }
    }

    void accept(byte[] packet,long now){
        expire(now);
        if(packet==null||packet.length<HEADER+1||packet.length>1080||now<=0){malformed++;return;}
        ByteBuffer b=ByteBuffer.wrap(packet).order(ByteOrder.BIG_ENDIAN);
        int magic=b.getInt(),version=b.get()&255,flags=b.get()&255,header=b.getShort()&65535;
        long pts=b.getLong(),id=b.getInt()&0xffffffffL;
        int codec=b.getInt(),total=b.getInt(),index=b.getShort()&65535,count=b.getShort()&65535;
        int offset=b.getInt(),size=b.getInt();boolean config=flags==1;
        if(magic!=MAGIC||version!=1||flags>1||header!=HEADER||pts<0||pts>Long.MAX_VALUE/1000L||id==0||codec!=AAC
                ||total<1||total>MAX_RECORD||config&&total>64||count!=(total+FRAGMENT-1)/FRAGMENT
                ||index>=count||offset!=index*FRAGMENT||size!=Math.min(FRAGMENT,total-offset)
                ||size<1||packet.length!=HEADER+size){malformed++;return;}
        if(recent.containsKey(id)){duplicate++;return;}
        Pending p=pending.get(id);
        if(p==null){
            if(pending.size()==MAX_PENDING){Iterator<Map.Entry<Long,Pending>> it=pending.entrySet().iterator();
                Map.Entry<Long,Pending> removed=it.next();remember(removed.getKey());it.remove();evicted++;}
            p=new Pending(id,pts,now,config,total,count);pending.put(id,p);
        }else if(p.pts!=pts||p.config!=config||p.data.length!=total||p.fragments.length!=count){
            pending.remove(id);remember(id);malformed++;return;
        }
        if(p.fragments[index]){
            for(int i=0;i<size;i++)if(p.data[offset+i]!=packet[HEADER+i]){
                pending.remove(id);remember(id);malformed++;return;
            }
            duplicate++;return;
        }
        System.arraycopy(packet,HEADER,p.data,offset,size);p.fragments[index]=true;p.filled++;acceptedFragments++;
        if(p.filled==count){
            pending.remove(id);remember(id);completed++;
            Frame frame=new Frame(id,pts,p.first,now,config,p.data);
            if(ready.size()==MAX_COMPLETE){ready.remove(0);evicted++;}
            ready.add(frame);
        }
    }

    List<Frame> poll(long now){
        expire(now);List<Frame> output=new ArrayList<>();
        ready.sort((a,b)->a.config!=b.config?(a.config?-1:1):Long.compare(a.ptsUs,b.ptsUs));
        for(Iterator<Frame> it=ready.iterator();it.hasNext();){
            Frame frame=it.next();
            if(now-frame.firstArrivalNs>=DEADLINE_NS){it.remove();expired++;continue;}
            // Hold a younger, lower-PTS record for the same bounded reorder
            // window before releasing a later PTS that has already matured.
            if(!frame.config&&now-frame.completeNs<REORDER_NS)break;
            if(!frame.config&&frame.ptsUs<=lastReleasedPts){it.remove();reorderDrops++;continue;}
            it.remove();output.add(frame);if(!frame.config)lastReleasedPts=frame.ptsUs;
        }
        return output;
    }

    private void expire(long now){
        for(Iterator<Map.Entry<Long,Pending>> it=pending.entrySet().iterator();it.hasNext();){
            Map.Entry<Long,Pending> entry=it.next();if(now-entry.getValue().first>=DEADLINE_NS){
                remember(entry.getKey());it.remove();expired++;}
        }
    }
    private void remember(long id){recent.put(id,Boolean.TRUE);if(recent.size()>MAX_RECENT){
        Iterator<Long> it=recent.keySet().iterator();it.next();it.remove();}}
    int pendingCount(){return pending.size();}
    int readyCount(){return ready.size();}

    /** Validate the supported AAC-LC 48 kHz stereo ASC instead of guessing. */
    static boolean supportedConfig(byte[] data){
        if(data==null||data.length<2||data.length>64)return false;
        int object=(data[0]&255)>>3,frequency=((data[0]&7)<<1)|((data[1]&255)>>7);
        int channels=((data[1]&255)>>3)&15;
        return object==2&&frequency==3&&channels==2;
    }
}
