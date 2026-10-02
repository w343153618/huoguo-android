package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.util.ArrayDeque;

/** Experimental PCM ownership handoff. No Android calls and no retained logs.
 * Five fixed buffers cover four queued records and one consumer-owned record.
 * Queue age is measured from codec dequeue, not from source/network timestamps.
 * An already-consumed record keeps the caller's unchanged scheduling policy.
 */
final class BoundedPcmQueue implements AutoCloseable {
    static final int MAX_QUEUED_FRAMES=4,MAX_FRAME_BYTES=16*1024;
    static final int MAX_QUEUED_BYTES=32*1024;
    static final int POOL_FRAMES=MAX_QUEUED_FRAMES+1;
    static final long MAX_QUEUE_AGE_NS=80_000_000L;
    private static final int FREE=0,QUEUED=1,CONSUMING=2;

    static final class Frame {
        private final BoundedPcmQueue owner;
        private final ByteBuffer storage=ByteBuffer.allocateDirect(MAX_FRAME_BYTES);
        private int state,size;
        long ptsUs,targetNs,dequeuedNs;float gain;
        private Frame(BoundedPcmQueue owner){this.owner=owner;}
        ByteBuffer pcm(){if(state!=CONSUMING)throw new IllegalStateException("PCM ownership");
            ByteBuffer view=storage.duplicate();view.position(0);view.limit(size);return view;}
        int size(){return size;}
    }
    static final class Snapshot {
        final int pendingFrames,pendingBytes,maximumFrames,maximumBytes,consumingFrames;
        final long accepted,consumed,frameLimitDrops,byteLimitDrops,oversizeDrops,ageDrops,admissionAgeDrops,closedDrops,closingCleared;
        Snapshot(BoundedPcmQueue q){pendingFrames=q.ready.size();pendingBytes=q.pendingBytes;
            maximumFrames=q.maximumFrames;maximumBytes=q.maximumBytes;consumingFrames=q.consumingFrames;
            accepted=q.accepted;consumed=q.consumed;frameLimitDrops=q.frameLimitDrops;byteLimitDrops=q.byteLimitDrops;
            oversizeDrops=q.oversizeDrops;ageDrops=q.ageDrops;admissionAgeDrops=q.admissionAgeDrops;closedDrops=q.closedDrops;closingCleared=q.closingCleared;}
    }
    private final Frame[] pool=new Frame[POOL_FRAMES];
    private final ArrayDeque<Frame> ready=new ArrayDeque<>(MAX_QUEUED_FRAMES);
    private int pendingBytes,maximumFrames,maximumBytes,consumingFrames;
    private long accepted,consumed,frameLimitDrops,byteLimitDrops,oversizeDrops,ageDrops,admissionAgeDrops,closedDrops,closingCleared;
    private boolean closed;

    BoundedPcmQueue(){for(int i=0;i<pool.length;i++)pool[i]=new Frame(this);}
    synchronized boolean offerCopy(ByteBuffer pcm,long ptsUs,long targetNs,long dequeuedNs,float gain,long nowNs){
        if(closed){closedDrops++;return false;}
        expire(nowNs);
        int bytes=pcm==null?0:pcm.remaining();
        if(bytes<=0||bytes>MAX_FRAME_BYTES||bytes%4!=0){oversizeDrops++;return false;}
        if(expired(dequeuedNs,nowNs)){admissionAgeDrops++;return false;}
        if(ready.size()>=MAX_QUEUED_FRAMES){frameLimitDrops++;return false;}
        if(bytes>MAX_QUEUED_BYTES-pendingBytes){byteLimitDrops++;return false;}
        Frame free=null;for(Frame frame:pool)if(frame.state==FREE){free=frame;break;}
        // Only one consumer may own a frame. Refuse admission if ownership was
        // violated, rather than allocating an unbounded replacement buffer.
        if(free==null){frameLimitDrops++;return false;}
        free.storage.clear();free.storage.put(pcm.duplicate());free.storage.flip();
        free.size=bytes;free.ptsUs=ptsUs;free.targetNs=targetNs;free.dequeuedNs=dequeuedNs;free.gain=gain;
        free.state=QUEUED;ready.addLast(free);pendingBytes+=bytes;accepted++;
        maximumFrames=Math.max(maximumFrames,ready.size());maximumBytes=Math.max(maximumBytes,pendingBytes);
        notifyAll();return true;
    }
    synchronized Frame poll(long nowNs){
        expire(nowNs);if(closed||ready.isEmpty())return null;
        if(consumingFrames!=0)throw new IllegalStateException("one PCM consumer");
        Frame frame=ready.removeFirst();pendingBytes-=frame.size;frame.state=CONSUMING;consumingFrames++;consumed++;
        return frame;
    }
    synchronized Frame awaitFrame(long timeoutNs)throws InterruptedException{
        if(timeoutNs<0||timeoutNs>20_000_000L)throw new IllegalArgumentException("bounded PCM poll");
        long start=System.nanoTime();
        while(true){Frame frame=poll(System.nanoTime());if(frame!=null||closed)return frame;
            long left=timeoutNs-(System.nanoTime()-start);if(left<=0)return null;
            wait(left/1_000_000L,(int)(left%1_000_000L));}
    }
    synchronized void release(Frame frame){
        if(frame==null||frame.owner!=this||frame.state!=CONSUMING)throw new IllegalArgumentException("PCM release ownership");
        consumingFrames--;clear(frame,closed);notifyAll();
    }
    synchronized Snapshot snapshot(){return new Snapshot(this);}
    synchronized boolean isClosed(){return closed;}
    private static boolean expired(long dequeue,long now){return dequeue<=0||now<dequeue||now-dequeue>=MAX_QUEUE_AGE_NS;}
    private void expire(long now){
        // At most four records; inspect all records so a future/invalid clock
        // observation cannot hide behind an otherwise fresh head record.
        for(java.util.Iterator<Frame> it=ready.iterator();it.hasNext();){Frame frame=it.next();
            if(expired(frame.dequeuedNs,now)){it.remove();pendingBytes-=frame.size;ageDrops++;clear(frame,false);}}
    }
    private void clear(Frame frame,boolean wipe){
        if(wipe){frame.storage.clear();while(frame.storage.hasRemaining())frame.storage.put((byte)0);}
        frame.storage.clear();frame.state=FREE;frame.size=0;frame.ptsUs=frame.targetNs=frame.dequeuedNs=0;frame.gain=0;
    }
    public synchronized void close(){
        if(closed)return;closed=true;
        while(!ready.isEmpty()){Frame frame=ready.removeFirst();closingCleared++;clear(frame,true);}
        pendingBytes=0;for(Frame frame:pool)if(frame.state==FREE)clear(frame,true);
        // A consumer-owned buffer must not be zeroed concurrently with write.
        // Its final release wipes it after the consumer has stopped using it.
        notifyAll();
    }
}
