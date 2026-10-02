package local.remoteandroid.direct;

import android.view.MotionEvent;
import android.view.SurfaceView;
import org.json.JSONObject;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Native phone touchscreen snapshots. No mouse events, TCP or shell input.
 * Sender must encrypt under the shared session lock with a fresh AES sequence
 * for EVERY call, including retries of the same logical touch sequence.
 */
public final class UdpTouchControl implements AutoCloseable {
    public interface Sender { void send(byte[] plaintext) throws Exception; }
    private static final int MAGIC=0x48475554,ACK_MAGIC=0x48475441,VERSION=1;
    private static final int DOWN=0,UP=1,MOVE=2,CANCEL=3,HOLD=4,HEADER=28,POINT=10;
    private static final long[] RETRY_DELAYS_NS={0,20_000_000L,40_000_000L,80_000_000L};
    private final SurfaceView surface;
    private final Sender sender;
    private final Object lock=new Object(),sendLock=new Object();
    private final ScheduledExecutorService executor=Executors.newSingleThreadScheduledExecutor(r->{
        Thread thread=new Thread(r,"udp-native-touch");thread.setDaemon(true);return thread;
    });
    private final LinkedHashMap<Integer,Contact> contacts=new LinkedHashMap<>();
    private final LinkedHashMap<Long,Pending> pending=new LinkedHashMap<>();
    private byte[] latestMove;
    private long sequence,nextToken=1,lastSnapshotNs;
    private int streamWidth,streamHeight,rotation;
    private boolean closed;
    private long motionEvents,payloadsSent,sendFailures,retries,acknowledged,invalidAcks;
    private long coalescedMoves,exhaustedEdges,ignoredBlackBorder,geometryCancels;

    private static final class Contact {
        final int token; int x,y,pressure;
        Contact(int token){this.token=token;}
    }
    private static final class Pending {
        final byte[] payload; int attempts; long nextNs;
        Pending(byte[] payload,long now){this.payload=payload;nextNs=now;}
    }

    /** Construct/install on the UI thread after creating the probe SurfaceView. */
    public UdpTouchControl(SurfaceView surface,Sender sender){
        if(surface==null||sender==null)throw new IllegalArgumentException("touch dependencies");
        this.surface=surface;this.sender=sender;
        surface.setOnTouchListener((view,event)->onTouch(event));
        executor.scheduleAtFixedRate(this::tick,0,5,TimeUnit.MILLISECONDS);
    }

    /** Actual source image dimensions; inverse quarter-turn to guest coordinates.
     * If the SurfaceView is already fitted to the video, letterbox bounds equal
     * that view. In a larger view, touches in the black border are ignored.
     */
    public void setGeometry(int width,int height,int rotationDegrees){
        if(width<1||height<1||width>65535||height>65535
                ||rotationDegrees%90!=0||rotationDegrees<0||rotationDegrees>270)
            throw new IllegalArgumentException("touch geometry");
        synchronized(lock){
            if(closed)return;
            int nextRotation=rotationDegrees/90;
            if(!contacts.isEmpty()&&(streamWidth!=width||streamHeight!=height||rotation!=nextRotation)){
                contacts.clear();pending.clear();latestMove=null;geometryCancels++;enqueue(CANCEL,0);
            }
            streamWidth=width;streamHeight=height;rotation=nextRotation;
        }
    }

    private boolean onTouch(MotionEvent event){
        synchronized(lock){
            if(closed)return true;
            motionEvents++;
            int action=event.getActionMasked(),index=event.getActionIndex();
            if(action==MotionEvent.ACTION_CANCEL){contacts.clear();pending.clear();latestMove=null;enqueue(CANCEL,0);return true;}
            if(streamWidth==0||streamHeight==0||surface.getWidth()==0||surface.getHeight()==0)return true;
            // Refresh every existing pointer from the same native MotionEvent.
            for(int i=0;i<event.getPointerCount();i++){
                Contact contact=contacts.get(event.getPointerId(i));
                if(contact!=null)update(contact,event,i,false);
            }
            if(action==MotionEvent.ACTION_DOWN||action==MotionEvent.ACTION_POINTER_DOWN){
                int id=event.getPointerId(index);
                if(action==MotionEvent.ACTION_DOWN&&!contacts.isEmpty()){
                    contacts.clear();pending.clear();latestMove=null;enqueue(CANCEL,0);
                }
                if(contacts.size()>=10||contacts.containsKey(id)||nextToken>0xffffffffL)return true;
                Contact contact=new Contact((int)nextToken++);
                if(!update(contact,event,index,true)){ignoredBlackBorder++;return true;}
                contacts.put(id,contact);enqueue(DOWN,contact.token);
            }else if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_POINTER_UP){
                Contact removed=contacts.remove(event.getPointerId(index));
                if(removed!=null)enqueue(UP,removed.token);
            }else if(action==MotionEvent.ACTION_MOVE){enqueue(MOVE,0);}
            return true;
        }
    }

    private boolean update(Contact contact,MotionEvent event,int index,boolean rejectBorder){
        double shownWidth=(rotation%2==0?streamWidth:streamHeight);
        double shownHeight=(rotation%2==0?streamHeight:streamWidth);
        double scale=Math.min(surface.getWidth()/shownWidth,surface.getHeight()/shownHeight);
        double width=shownWidth*scale,height=shownHeight*scale;
        double x=(event.getX(index)-(surface.getWidth()-width)/2)/width;
        double y=(event.getY(index)-(surface.getHeight()-height)/2)/height;
        double pressure=event.getPressure(index);
        if(Double.isNaN(x)||Double.isNaN(y)||Double.isInfinite(x)||Double.isInfinite(y)
                ||(rejectBorder&&(x<0||x>1||y<0||y>1)))return false;
        contact.x=(int)Math.round(Math.max(0,Math.min(1,x))*65535);
        contact.y=(int)Math.round(Math.max(0,Math.min(1,y))*65535);
        contact.pressure=(int)Math.round((Double.isNaN(pressure)?0:Math.max(0,Math.min(1,pressure)))*65535);
        return true;
    }

    private byte[] encode(int action,int changed,long now){
        if(sequence==Long.MAX_VALUE)throw new IllegalStateException("touch sequence exhausted");
        ByteBuffer data=ByteBuffer.allocate(HEADER+POINT*contacts.size()).order(ByteOrder.BIG_ENDIAN);
        data.putInt(MAGIC).put((byte)VERSION).put((byte)action).put((byte)contacts.size()).put((byte)rotation)
            .putLong(++sequence).putLong(now/1000).putInt(changed);
        for(Contact contact:contacts.values())
            data.putInt(contact.token).putShort((short)contact.x).putShort((short)contact.y).putShort((short)contact.pressure);
        lastSnapshotNs=now;return data.array();
    }

    private void enqueue(int action,int changed){
        long now=System.nanoTime();byte[] data=encode(action,changed,now);
        if(action==MOVE||action==HOLD){
            if(latestMove!=null)coalescedMoves++;
            latestMove=data;
        }else{
            if(pending.size()>=64){
                Iterator<Map.Entry<Long,Pending>> oldest=pending.entrySet().iterator();
                oldest.next();oldest.remove();exhaustedEdges++;
            }
            pending.put(sequence,new Pending(data,now));
        }
    }

    private void tick(){
        ArrayList<byte[]> sends=new ArrayList<>();long now=System.nanoTime();
        synchronized(lock){
            if(closed)return;
            if(now-lastSnapshotNs>=200_000_000L)enqueue(HOLD,0);
            Iterator<Map.Entry<Long,Pending>> iterator=pending.entrySet().iterator();
            while(iterator.hasNext()){
                Pending item=iterator.next().getValue();
                if(now<item.nextNs)continue;
                if(item.attempts==RETRY_DELAYS_NS.length){iterator.remove();exhaustedEdges++;continue;}
                sends.add(item.payload);if(item.attempts>0)retries++;
                item.attempts++;
                item.nextNs=now+(item.attempts<RETRY_DELAYS_NS.length?RETRY_DELAYS_NS[item.attempts]:80_000_000L);
            }
            if(latestMove!=null){sends.add(latestMove);latestMove=null;}
        }
        for(byte[] data:sends)send(data);
    }

    private void send(byte[] data){
        synchronized(sendLock){
            try{sender.send(data);synchronized(lock){payloadsSent++;}}
            catch(Exception failure){synchronized(lock){sendFailures++;}}
        }
    }

    /** Return true only for an HGTA payload. Caller already authenticated it. */
    public boolean onAck(byte[] payload){
        if(payload==null||payload.length<4||ByteBuffer.wrap(payload).getInt()!=ACK_MAGIC)return false;
        synchronized(lock){
            if(payload.length!=16){invalidAcks++;return true;}
            ByteBuffer data=ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN);data.getInt();
            int version=data.get()&255,status=data.get()&255,reserved=data.getShort()&65535;long seq=data.getLong();
            if(version!=VERSION||status>2||reserved!=0||seq<=0){invalidAcks++;return true;}
            if(pending.remove(seq)!=null)acknowledged++;
            return true;
        }
    }

    /** Aggregate counters only: no touch coordinates or per-event history. */
    public JSONObject snapshot(){
        JSONObject result=new JSONObject();synchronized(lock){
            try{result.put("motion_events",motionEvents).put("active_pointers",contacts.size())
                .put("pending_edges",pending.size()).put("payloads_sent",payloadsSent).put("send_failures",sendFailures)
                .put("edge_retries",retries).put("edge_acks",acknowledged).put("invalid_acks",invalidAcks)
                .put("coalesced_moves",coalescedMoves).put("exhausted_edges",exhaustedEdges)
                .put("ignored_black_border",ignoredBlackBorder).put("geometry_cancels",geometryCancels)
                .put("touch_to_photon_latency_measured",false);
            }catch(Exception impossible){throw new IllegalStateException(impossible);}
        }return result;
    }

    /** Final cancel copies share one logical seq but use fresh outer AES nonces.
     * No sleeps/ACK waits; the host lease also releases fingers on link failure.
     */
    @Override public void close(){
        byte[] cancel;
        synchronized(lock){
            if(closed)return;
            contacts.clear();pending.clear();latestMove=null;cancel=encode(CANCEL,0,System.nanoTime());closed=true;
        }
        executor.shutdownNow();surface.post(()->surface.setOnTouchListener(null));
        for(int i=0;i<3;i++)send(cancel);
    }
}
