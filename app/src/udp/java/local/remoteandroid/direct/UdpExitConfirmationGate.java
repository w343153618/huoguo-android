package local.remoteandroid.direct;

/** At most one dialog; only its captured generation and object may request cancellation. */
public final class UdpExitConfirmationGate {
    public static final int CONTINUE=0,EXIT_CURRENT=1,STALE=2;
    public static final class Token {
        private final long generation;private final Object attempt;
        private Token(long generation,Object attempt){this.generation=generation;this.attempt=attempt;}
    }
    private Token pending;
    public synchronized Token open(long generation,Object attempt){
        if(generation<0||attempt==null)throw new IllegalArgumentException("live_attempt_required");
        if(pending!=null)return null;pending=new Token(generation,attempt);return pending;
    }
    public synchronized int resolve(Token token,boolean exit,long currentGeneration,Object currentAttempt){
        if(token==null||pending!=token)return STALE;pending=null;
        if(token.generation!=currentGeneration||token.attempt!=currentAttempt)return STALE;
        return exit?EXIT_CURRENT:CONTINUE;
    }
    public synchronized boolean dismiss(Token token){if(token==null||pending!=token)return false;pending=null;return true;}
    public synchronized boolean pending(){return pending!=null;}
}
