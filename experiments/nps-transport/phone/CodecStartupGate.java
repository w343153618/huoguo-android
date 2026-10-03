package local.remoteandroid.direct;

/** Owner-only, bounded startup policy. Caller holds the VideoInbox monitor.
 * No codec, socket, clock mapping or frame byte storage is owned by this class.
 * A PREPARE AU is metadata only: it must never be submitted or clock-anchored.
 */
final class CodecStartupGate {
    static final int DROP=0,PREPARE=1,ADMIT=2;
    static final int DISABLED=0,WAIT_BOOTSTRAP=1,PREPARING=2,WAIT_FRESH_IDR=3,
        CHAIN_PENDING=4,STREAMING=5,FAILED=6,CLOSED=7;
    static final int BOOTSTRAP_TIMEOUT=1,PREPARE_TIMEOUT=2,FRESH_IDR_TIMEOUT=3,
        GEOMETRY_CHANGED=4,CONFIGURE_FAILED=5,BOOTSTRAP_EXPIRED=6;
    static final long MAX_AGE_NS=80_000_000L;
    static final long BOOTSTRAP_TIMEOUT_NS=5_000_000_000L,PREPARE_TIMEOUT_NS=2_500_000_000L,
        FRESH_TIMEOUT_NS=2_500_000_000L;
    static final int MAX_BODY_BYTES=2*1024*1024,MAX_REQUESTS=3;
    static final String[] STAT_NAMES={"enabled","phase","phase_before_close","failure_code",
        "started_ns","bootstrap_received_ns","prepare_started_ns","ready_ns","fresh_received_ns","committed_ns",
        "bootstrap_pts_us","fresh_pts_us","width","height","bootstrap_admissions","preparing_drops",
        "waiting_drops","fresh_expired_drops","before_ready_drops","requests_reserved","chain_losses"};
    private final boolean enabled;
    private final long startedNs;
    private int phase,phaseBeforeClose,failure;
    private int width,height;
    private long bootstrapReceivedNs,prepareStartedNs,readyNs,freshReceivedNs,committedNs;
    private long bootstrapPts=-1,freshPts=-1,nextRequestNs;
    private long bootstrapAdmissions,preparingDrops,waitingDrops,freshExpiredDrops,beforeReadyDrops,requestsSent,chainLosses;

    CodecStartupGate(boolean enabled,long startedNs){
        this.enabled=enabled;this.startedNs=startedNs;phase=enabled?WAIT_BOOTSTRAP:DISABLED;
        phaseBeforeClose=phase;
    }
    int phase(){return phase;}
    boolean enabled(){return enabled;}
    int failureCode(){return failure;}
    private static boolean fresh(long receivedNs,long nowNs){
        return receivedNs>=0&&nowNs>=receivedNs&&nowNs-receivedNs<MAX_AGE_NS;
    }
    private void fail(int reason){if(phase!=CLOSED&&phase!=FAILED){failure=reason;phase=FAILED;}}
    void configureFailed(){fail(CONFIGURE_FAILED);}
    void bootstrapExpired(){fail(BOOTSTRAP_EXPIRED);}
    void checkTimeout(long nowNs){
        if(phase==WAIT_BOOTSTRAP&&nowNs-startedNs>=BOOTSTRAP_TIMEOUT_NS)fail(BOOTSTRAP_TIMEOUT);
        else if(phase==PREPARING&&nowNs-bootstrapReceivedNs>=PREPARE_TIMEOUT_NS)fail(PREPARE_TIMEOUT);
        else if((phase==WAIT_FRESH_IDR||phase==CHAIN_PENDING)&&nowNs-readyNs>=FRESH_TIMEOUT_NS)fail(FRESH_IDR_TIMEOUT);
    }
    int admission(int w,int h,long pts,long receivedNs,boolean recoveryIdr,int bodyBytes,long nowNs){
        checkTimeout(nowNs);
        if(phase==CLOSED||phase==FAILED)return DROP;
        if(!enabled||phase==STREAMING||phase==CHAIN_PENDING)return ADMIT;
        if(phase==PREPARING){preparingDrops++;return DROP;}
        if(!recoveryIdr||bodyBytes<25||bodyBytes>MAX_BODY_BYTES||w<16||h<16||w>4096||h>4096||pts<0){waitingDrops++;return DROP;}
        if(!fresh(receivedNs,nowNs)){freshExpiredDrops++;return DROP;}
        if(phase==WAIT_BOOTSTRAP){
            width=w;height=h;bootstrapPts=pts;bootstrapReceivedNs=receivedNs;bootstrapAdmissions++;
            phase=PREPARING;return PREPARE;
        }
        if(receivedNs<readyNs||pts<=bootstrapPts){beforeReadyDrops++;return DROP;}
        if(w!=width||h!=height){fail(GEOMETRY_CHANGED);return DROP;}
        freshPts=pts;freshReceivedNs=receivedNs;phase=CHAIN_PENDING;return ADMIT;
    }
    boolean preparing(long pts,int w,int h){return phase==PREPARING&&pts==bootstrapPts&&w==width&&h==height;}
    void preparationStarted(long pts,long nowNs){if(phase==PREPARING&&pts==bootstrapPts)prepareStartedNs=nowNs;}
    boolean prepared(long pts,int w,int h,long nowNs){
        checkTimeout(nowNs);
        if(!preparing(pts,w,h)||nowNs<bootstrapReceivedNs||prepareStartedNs>0&&nowNs<prepareStartedNs)return false;
        readyNs=nowNs;nextRequestNs=nowNs;phase=WAIT_FRESH_IDR;return true;
    }
    /** Called only by RX; a successful result reserves one authenticated send. */
    boolean requestDue(long nowNs){
        checkTimeout(nowNs);
        if(phase!=WAIT_FRESH_IDR||requestsSent>=MAX_REQUESTS||nowNs<nextRequestNs)return false;
        requestsSent++;nextRequestNs=nowNs+(500_000_000L<<(requestsSent-1));return true;
    }
    void chainLost(long nowNs){
        if(phase==CHAIN_PENDING){chainLosses++;phase=WAIT_FRESH_IDR;freshPts=-1;freshReceivedNs=0;checkTimeout(nowNs);}
    }
    /** Inbox must have committed this complete IDR in the current epoch first. */
    boolean committed(long pts,long nowNs){
        checkTimeout(nowNs);
        if(phase==DISABLED||phase==STREAMING)return true;
        if(phase==CHAIN_PENDING&&pts==freshPts&&nowNs>=freshReceivedNs){committedNs=nowNs;phase=STREAMING;return true;}
        return false;
    }
    void close(long nowNs){if(phase!=CLOSED){phaseBeforeClose=phase;phase=CLOSED;}}
    long[] snapshot(){
        return new long[]{enabled?1:0,phase,phaseBeforeClose,failure,startedNs,bootstrapReceivedNs,
            prepareStartedNs,readyNs,freshReceivedNs,committedNs,bootstrapPts,freshPts,width,height,
            bootstrapAdmissions,preparingDrops,waitingDrops,freshExpiredDrops,beforeReadyDrops,requestsSent,chainLosses};
    }
}
