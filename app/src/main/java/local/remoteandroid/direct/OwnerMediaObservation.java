package local.remoteandroid.direct;

import java.util.ArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.ReentrantLock;

/** Same-App source observation only. Strong references come from the actual
 * resource owner paths; no command, JSON, PID, closer, join or lease interface.
 * All writes are nonblocking so a reader cannot delay normal media cleanup.
 */
final class OwnerMediaObservation {
    private final Object owner;
    private final long endNs;
    private final ReentrantLock lock=new ReentrantLock();
    private final AtomicBoolean unknown=new AtomicBoolean();
    private Thread runner;
    private Object activity,socket,audio,touch,inbox;
    private Thread video;
    private long nativeHandle;
    private int generation=-1;
    private boolean published,closing,closeObserved,videoCloseReturned,socketCloseReturned;
    private boolean nativeDestroyReturned,listenerEntered,listenerReturned,runReturned;
    private int audioCloseClaim=-1;
    private Object audioOwner;
    private Thread audioInput;
    private boolean audioCloseEntered,audioCloseReturned;
    private final ArrayList<AudioEpoch> audioEpochs=new ArrayList<>();
    private static final int MAX_AUDIO_EPOCHS=64;

    private OwnerMediaObservation(Object owner,long endNs){this.owner=owner;this.endNs=endNs;}
    static OwnerMediaObservation prepare(Object owner,long endNs){
        long now=System.nanoTime();
        if(owner==null||endNs<=now||endNs-now>30_000_000_000L)
            throw new IllegalArgumentException("owner_media_observation_budget");
        return new OwnerMediaObservation(owner,endNs);
    }
    void invalidate(){unknown.set(true);}
    private boolean enter(){
        if(unknown.get()||System.nanoTime()>=endNs){invalidate();return false;}
        if(!lock.tryLock()){invalidate();return false;}
        if(unknown.get()||System.nanoTime()>=endNs){invalidate();lock.unlock();return false;}
        return true;
    }
    private boolean runner(Object source){
        if(source!=owner||runner!=Thread.currentThread()||runReturned){invalidate();return false;}
        return true;
    }
    void started(Object source){
        if(!enter())return;
        try{if(source!=owner||runner!=null){invalidate();return;}runner=Thread.currentThread();}
        finally{lock.unlock();}
    }
    void published(Object source,Object activity,int generation,Object socket,long nativeHandle,
            Object audio,Object touch,Object inbox,Thread video){
        if(!enter())return;
        try{
            if(!runner(source)||published||closing||activity==null||generation<0||socket==null||nativeHandle==0
                    ||audioOwner!=audio||(video==null)!=(inbox==null)){
                invalidate();return;
            }
            this.activity=activity;this.generation=generation;this.socket=socket;this.nativeHandle=nativeHandle;
            this.audio=audio;this.touch=touch;this.inbox=inbox;this.video=video;published=true;
        }finally{lock.unlock();}
    }
    void closing(Object source){
        if(!enter())return;
        try{if(!runner(source)||!published||closing){invalidate();return;}closing=true;}
        finally{lock.unlock();}
    }
    /** A returned normal close call is recorded separately from thread exit and
     * codec/audio/input/PM quiescence. No null-pointer inference is made.
     */
    void closeReturns(Object source,boolean videoReturn,int audioClaim,boolean socketReturn){
        if(!enter())return;
        try{if(!runner(source)||!closing||closeObserved||audioClaim<0||audioClaim>2){invalidate();return;}
            closeObserved=true;videoCloseReturned=videoReturn;audioCloseClaim=audioClaim;socketCloseReturned=socketReturn;}
        finally{lock.unlock();}
    }
    void nativeDestroyed(Object source,long handle){
        if(!enter())return;
        try{if(!runner(source)||!closing||!closeObserved||nativeDestroyReturned||handle!=nativeHandle){invalidate();return;}
            nativeDestroyReturned=true;}
        finally{lock.unlock();}
    }
    void listener(Object source,boolean returned){
        if(!enter())return;
        try{if(!runner(source)||!closing||!nativeDestroyReturned||(!returned&&listenerEntered)||(returned&&(!listenerEntered||listenerReturned))){invalidate();return;}
            if(returned)listenerReturned=true;else listenerEntered=true;}
        finally{lock.unlock();}
    }
    void returned(Object source,boolean normalReturn){
        if(!enter())return;
        try{if(!runner(source)||!closing||!normalReturn||!listenerReturned){invalidate();return;}runReturned=true;}
        finally{lock.unlock();}
    }
    void audioCreated(Object receiver,Thread input){
        if(!enter())return;
        try{if(runner!=Thread.currentThread()||audioOwner!=null||receiver==null||input==null||closing){invalidate();return;}
            audioOwner=receiver;audioInput=input;}
        finally{lock.unlock();}
    }
    /** Called by the actual audio input worker before starting its output/PCM
     * workers. Each published epoch keeps its original resource references.
     * Failed setup before publication remains unqualified, never absent proof.
     */
    void audioEpoch(Object receiver,Object codec,Object track,Object handoff,Thread drain,Thread pcm){
        if(!enter())return;
        try{
            if(receiver!=audioOwner||Thread.currentThread()!=audioInput||closing||audioCloseEntered
                    ||codec==null||track==null||drain==null||(pcm==null)!=(handoff==null)
                    ||audioEpochs.size()>=MAX_AUDIO_EPOCHS){invalidate();return;}
            for(AudioEpoch epoch:audioEpochs)if(epoch.codec==codec||epoch.track==track||epoch.drain==drain){invalidate();return;}
            try{audioEpochs.add(new AudioEpoch(codec,track,handoff,drain,pcm));}
            catch(RuntimeException|OutOfMemoryError failure){invalidate();}
        }finally{lock.unlock();}
    }
    void audioClose(Object receiver,boolean returned){
        if(!enter())return;
        try{if(receiver!=audioOwner||runner!=Thread.currentThread()||!closing
                    ||(!returned&&audioCloseEntered)||(returned&&(!audioCloseEntered||audioCloseReturned))){invalidate();return;}
            if(returned)audioCloseReturned=true;else audioCloseEntered=true;}
        finally{lock.unlock();}
    }
    private static final class AudioEpoch {
        final Object codec,track,handoff;final Thread drain,pcm;
        AudioEpoch(Object codec,Object track,Object handoff,Thread drain,Thread pcm){
            this.codec=codec;this.track=track;this.handoff=handoff;this.drain=drain;this.pcm=pcm;
        }
    }
    /** Past reference/path observations only. Every qualification field remains
     * false, even when all observed workers have actually terminated.
     */
    static final class Snapshot {
        final boolean runnerTerminated,videoTerminated,audioThreadsTerminated;
        final boolean videoCloseReturned,socketCloseReturned,nativeDestroyReturned,audioCloseReturned;
        final int generation,audioCloseClaim,audioEpochCount;
        final boolean codecAudioInputQualified=false,attemptQualified=false,releaseEligible=false;
        Snapshot(OwnerMediaObservation value){
            runnerTerminated=value.runReturned&&value.runner.getState()==Thread.State.TERMINATED;
            videoTerminated=value.video==null||value.video.getState()==Thread.State.TERMINATED;
            boolean audioStopped=value.audioInput==null||value.audioInput.getState()==Thread.State.TERMINATED;
            for(AudioEpoch epoch:value.audioEpochs)audioStopped&=epoch.drain.getState()==Thread.State.TERMINATED
                &&(epoch.pcm==null||epoch.pcm.getState()==Thread.State.TERMINATED);
            audioThreadsTerminated=audioStopped;generation=value.generation;audioEpochCount=value.audioEpochs.size();
            videoCloseReturned=value.videoCloseReturned;socketCloseReturned=value.socketCloseReturned;
            nativeDestroyReturned=value.nativeDestroyReturned;audioCloseReturned=value.audioCloseReturned;
            audioCloseClaim=value.audioCloseClaim;
        }
    }
    Snapshot observe(Object source){
        long begin=System.nanoTime();
        if(source!=owner){invalidate();return null;}
        if(!enter())return null;
        try{
            if(runner==null||!published||!runReturned)return null;
            Snapshot snapshot=new Snapshot(this);
            if(unknown.get()||System.nanoTime()-begin>=3_000_000_000L||System.nanoTime()>=endNs){invalidate();return null;}
            return snapshot;
        }finally{lock.unlock();}
    }
}
