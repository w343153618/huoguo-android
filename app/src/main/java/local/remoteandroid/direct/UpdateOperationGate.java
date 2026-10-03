package local.remoteandroid.direct;

/** Process-wide operation identity. No I/O, sleeps, Android calls or callbacks under its monitor. */
public final class UpdateOperationGate {
    public static final int IDLE=0,SELECTING=1,CHECKING=2,DIALOG=3,DOWNLOADING=4,
        VERIFYING=5,WAIT_PERMISSION=6,INSTALLER=7;
    private long serial,token;
    private int phase=IDLE;
    private long nextToken(){if(serial==Long.MAX_VALUE)throw new IllegalStateException("update_operation_identity_exhausted");return ++serial;}
    private static void validPhase(int phase){if(phase<SELECTING||phase>INSTALLER)throw new IllegalArgumentException("update_operation_phase");}
    public synchronized long acquire(int phase){
        validPhase(phase);if(this.phase!=IDLE)return 0;
        token=nextToken();this.phase=phase;return token;
    }
    public synchronized boolean current(long token){return token!=0&&this.token==token&&phase!=IDLE;}
    public synchronized boolean transition(long token,int expected,int next){
        validPhase(expected);validPhase(next);
        if(!current(token)||phase!=expected)return false;phase=next;return true;
    }
    public synchronized boolean release(long token){
        if(!current(token))return false;phase=IDLE;this.token=0;return true;
    }
    public synchronized int phase(){return phase;}
    public synchronized int phase(long token){return current(token)?phase:IDLE;}
    /** Only UI handoffs can be adopted; an old worker never transfers network/file ownership. */
    public synchronized long resume(int expected,int next){
        if(!((expected==WAIT_PERMISSION&&(next==VERIFYING||next==IDLE))
                ||expected==INSTALLER&&next==IDLE))throw new IllegalArgumentException("update_resume_phase");
        if(phase!=expected)return 0;
        long replacement=nextToken();phase=next;token=next==IDLE?0:replacement;return replacement;
    }
}
