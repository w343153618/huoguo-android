package local.remoteandroid.direct;

import java.util.ArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.ReentrantLock;

/** Actual allocation/call-path references, including setup that never publishes.
 * No resource operation is performed here. Past calls are not a resource lease,
 * Android release verification, captured Attempt qualification or permission.
 */
final class OwnerResourceObservation {
    static final int CODEC=1,TRACK=2,STOP=1,RELEASE=2;
    private static final int MAX_RESOURCES=128,MAX_CALLS=512;
    private final long endNs;
    private final ReentrantLock lock=new ReentrantLock();
    private final AtomicBoolean unknown=new AtomicBoolean();
    private final ArrayList<Resource> resources=new ArrayList<>();
    private int calls;
    OwnerResourceObservation(long endNs){
        long now=System.nanoTime();
        if(endNs<=now||endNs-now>30_000_000_000L)throw new IllegalArgumentException("resource_observation_budget");
        this.endNs=endNs;
    }
    void invalidate(){unknown.set(true);}
    boolean isUnknown(){return unknown.get();}
    private boolean enter(){
        if(unknown.get()||System.nanoTime()>=endNs||!lock.tryLock()){invalidate();return false;}
        if(unknown.get()||System.nanoTime()>=endNs){invalidate();lock.unlock();return false;}
        return true;
    }
    private Resource find(Object source,Object value){
        for(Resource resource:resources)if(resource.value==value)return resource.source==source?resource:null;
        return null;
    }
    void allocated(Object source,Object value,int kind){
        if(!enter())return;
        try{
            if(source==null||value==null||(kind!=CODEC&&kind!=TRACK)||resources.size()>=MAX_RESOURCES){invalidate();return;}
            for(Resource resource:resources)if(resource.value==value){invalidate();return;}
            try{resources.add(new Resource(source,value,kind,Thread.currentThread()));}
            catch(RuntimeException|OutOfMemoryError failure){invalidate();}
        }finally{lock.unlock();}
    }
    Call begin(Object source,Object value,int operation){
        if(!enter())return null;
        try{
            Resource resource=find(source,value);
            if(resource==null||(operation!=STOP&&operation!=RELEASE)||resource.pending!=null||calls>=MAX_CALLS){invalidate();return null;}
            // Allocate the diagnostic token before altering counters; inability
            // to retain it invalidates observation without changing normal calls.
            Call call;
            try{call=new Call(this,resource,operation,Thread.currentThread());}
            catch(RuntimeException|OutOfMemoryError failure){invalidate();return null;}
            resource.pending=call;calls++;
            if(operation==STOP)resource.stopAttempts++;else resource.releaseAttempts++;
            return call;
        }finally{lock.unlock();}
    }
    private void finish(Call call,boolean returned){
        if(!enter())return;
        try{
            if(call.thread!=Thread.currentThread()||call.finished||call.resource.pending!=call){invalidate();return;}
            call.finished=true;call.resource.pending=null;
            if(call.operation==STOP){if(returned)call.resource.stopReturns++;else call.resource.stopFailures++;}
            else {if(returned)call.resource.releaseReturns++;else call.resource.releaseFailures++;}
        }finally{lock.unlock();}
    }
    static final class Call {
        private final OwnerResourceObservation observer;
        private final Resource resource;
        private final int operation;
        private final Thread thread;
        private boolean finished;
        private Call(OwnerResourceObservation observer,Resource resource,int operation,Thread thread){
            this.observer=observer;this.resource=resource;this.operation=operation;this.thread=thread;
        }
        void finish(boolean returned){observer.finish(this,returned);}
    }
    private static final class Resource {
        final Object source,value;final int kind;final Thread allocationThread;
        int stopAttempts,stopReturns,stopFailures,releaseAttempts,releaseReturns,releaseFailures;
        Call pending;
        Resource(Object source,Object value,int kind,Thread thread){this.source=source;this.value=value;this.kind=kind;allocationThread=thread;}
    }
    static final class Past {
        final Object source,value;final int kind;final Thread allocationThread;
        final int stopAttempts,stopReturns,stopFailures,releaseAttempts,releaseReturns,releaseFailures;
        final boolean pending;
        final boolean resourceReleasedQualified=false,attemptQualified=false,releaseEligible=false;
        Past(Resource resource){source=resource.source;value=resource.value;kind=resource.kind;allocationThread=resource.allocationThread;
            stopAttempts=resource.stopAttempts;stopReturns=resource.stopReturns;stopFailures=resource.stopFailures;
            releaseAttempts=resource.releaseAttempts;releaseReturns=resource.releaseReturns;releaseFailures=resource.releaseFailures;
            pending=resource.pending!=null;}
    }
    Past[] snapshot(){
        long begin=System.nanoTime();if(!enter())return null;
        try{
            Past[] result;
            try{result=new Past[resources.size()];for(int i=0;i<result.length;i++)result[i]=new Past(resources.get(i));}
            catch(RuntimeException|OutOfMemoryError failure){invalidate();return null;}
            if(unknown.get()||System.nanoTime()-begin>=3_000_000_000L||System.nanoTime()>=endNs){invalidate();return null;}
            return result;
        }finally{lock.unlock();}
    }
}
