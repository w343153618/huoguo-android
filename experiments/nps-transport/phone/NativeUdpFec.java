package local.remoteandroid.direct;

import java.io.File;
import java.io.IOException;

/** JNI component loaded only by instrumentation or the opt-in isolated UDP App. */
public final class NativeUdpFec {
    private static boolean loaded;
    private NativeUdpFec() { }

    static synchronized void load(String instrumentationNativeDirectory) {
        if(!loaded){
            System.load(new File(instrumentationNativeDirectory,"libhuoguo_udp_fec.so").getAbsolutePath());
            loaded=true;
        }
    }
    static synchronized void loadApp(){
        if(!loaded){System.loadLibrary("huoguo_udp_fec");loaded=true;}
    }
    public static native long nativeCreate();
    public static native byte[][] nativeAccept(long handle,byte[] authenticatedPacket,long arrivalUs);
    public static native byte[][] nativeExpire(long handle,long nowUs);
    public static native long[] nativeStats(long handle);
    public static native void nativeSetDiagnostics(long handle,boolean enabled);
    public static native long[][] nativeDrainEvents(long handle);
    public static native long[] nativeEventStats(long handle);
    public static native void nativeSetMappingDiagnostics(long handle,boolean enabled);
    public static native long[] nativeMappingDetails(long handle);
    public static native void nativeDestroy(long handle);

    static final int MAPPING_DETAIL_SCHEMA=1,MAPPING_DETAIL_HEADER=36,MAPPING_DETAIL_CAPACITY=32,
        MAPPING_DETAIL_COLUMNS=8,MAPPING_DETAIL_LENGTH=MAPPING_DETAIL_HEADER+MAPPING_DETAIL_CAPACITY*MAPPING_DETAIL_COLUMNS;
    static final int MAPPING_DETAIL_VALID=1,MAPPING_DETAIL_NOT_READ=-1,
        MAPPING_DETAIL_JNI_UNAVAILABLE=-2,MAPPING_DETAIL_INVALID=-3;
    private static final int[] MAPPING_DEPTH_INDEXES={8,9,11,12,14,15,16};
    static final String[] MAPPING_DETAIL_HEADER_NAMES={"schema_version","enabled","coverage_mask",
        "mapping_rejected_observed","rejected_old_frame","rejected_capacity","rejected_header_mismatch",
        "rejections_unobserved","capacity_adapter_active_last","capacity_core_pending_last","capacity_oldest_age_us_last",
        "capacity_adapter_active_max","capacity_core_pending_max","capacity_oldest_age_us_max","adapter_active_max_observed",
        "adapter_active_current","core_pending_current","events_total","events_retained","events_evicted",
        "event_capacity","event_columns","first_event_sequence","last_event_sequence","first_event_arrival_us",
        "last_event_arrival_us","enable_transitions","disable_transitions","new_mappings_observed",
        "admissions_after_capacity_reject","capacity_tracking_active","capacity_tracking_evicted",
        "capacity_tracking_capacity","snapshot_phone_us","legacy_mapping_rejected","events_cleared"};
    static final String[] MAPPING_DETAIL_EVENT_NAMES={"sequence","frame_id","arrival_phone_us","reason_code",
        "adapter_active","core_pending","oldest_mapping_age_us","first_capacity_reject_phone_us"};

    static final class MappingDetailsException extends IOException {
        final int status;
        MappingDetailsException(int status,String message){super(message);this.status=status;}
        MappingDetailsException(int status,String message,Throwable cause){super(message,cause);this.status=status;}
    }
    static void setMappingDiagnosticsChecked(long handle,boolean enabled)throws MappingDetailsException {
        try{nativeSetMappingDiagnostics(handle,enabled);}
        catch(UnsatisfiedLinkError absent){throw new MappingDetailsException(MAPPING_DETAIL_JNI_UNAVAILABLE,
            "native_mapping_details_jni_unavailable",absent);}
    }
    static long[] mappingDetailsChecked(long handle)throws MappingDetailsException {
        final long[] values;
        try{values=nativeMappingDetails(handle);}
        catch(UnsatisfiedLinkError absent){throw new MappingDetailsException(MAPPING_DETAIL_JNI_UNAVAILABLE,
            "native_mapping_details_jni_unavailable",absent);}
        validateMappingDetails(values);return values;
    }
    private static void requireMapping(boolean condition)throws MappingDetailsException {
        if(!condition)throw new MappingDetailsException(MAPPING_DETAIL_INVALID,"native_mapping_details_contract");
    }
    private static long mappingSum(long a,long b)throws MappingDetailsException {
        requireMapping(a<=Long.MAX_VALUE-b);return a+b;
    }
    /** Fixed native snapshot, never substitutes missing or malformed JNI data with zero counters. */
    static void validateMappingDetails(long[] v)throws MappingDetailsException {
        requireMapping(v!=null&&v.length==MAPPING_DETAIL_LENGTH);
        for(long value:v)requireMapping(value>=0);
        requireMapping(v[0]==MAPPING_DETAIL_SCHEMA&&(v[1]==0||v[1]==1)&&v[2]==15);
        requireMapping(v[20]==MAPPING_DETAIL_CAPACITY&&v[21]==MAPPING_DETAIL_COLUMNS&&v[18]<=MAPPING_DETAIL_CAPACITY);
        requireMapping(v[32]==32&&v[30]<=v[32]);
        requireMapping(mappingSum(v[3],v[7])==v[34]);
        requireMapping(mappingSum(mappingSum(v[4],v[5]),v[6])==v[3]);
        requireMapping(mappingSum(mappingSum(v[18],v[19]),v[35])==v[17]);
        requireMapping(v[29]<=v[28]);
        for(int i:MAPPING_DEPTH_INDEXES)requireMapping(v[i]<=8);
        requireMapping(v[8]<=v[11]&&v[9]<=v[12]&&v[10]<=v[13]&&(v[1]==0||v[15]<=v[14]));
        long previousSequence=0,previousArrival=0;
        for(int row=0;row<MAPPING_DETAIL_CAPACITY;row++){
            int at=MAPPING_DETAIL_HEADER+row*MAPPING_DETAIL_COLUMNS;
            if(row>=v[18]){for(int col=0;col<MAPPING_DETAIL_COLUMNS;col++)requireMapping(v[at+col]==0);continue;}
            long sequence=v[at],arrival=v[at+2],reason=v[at+3],firstCapacity=v[at+7];
            requireMapping(sequence==v[17]-v[18]+row+1&&sequence>previousSequence
                &&v[at+1]>0&&arrival>0&&arrival>=previousArrival);
            requireMapping(reason>=1&&reason<=4&&v[at+4]<=8&&v[at+5]<=8&&arrival<=v[33]);
            requireMapping(reason==2||reason==4?firstCapacity>0&&firstCapacity<=arrival:firstCapacity==0);
            previousSequence=sequence;previousArrival=arrival;
        }
        if(v[18]==0){for(int i=22;i<=25;i++)requireMapping(v[i]==0);}
        else{int last=MAPPING_DETAIL_HEADER+((int)v[18]-1)*MAPPING_DETAIL_COLUMNS;
            requireMapping(v[22]==v[MAPPING_DETAIL_HEADER]&&v[23]==v[last]&&v[23]==v[17]
                &&v[24]==v[MAPPING_DETAIL_HEADER+2]&&v[25]==v[last+2]);}
    }

    static final String[] STAT_NAMES={"packets","wire_bytes","invalid","duplicate","settled_packets",
        "expired_packets","frames_expired","frames_delivered","recovered_shards","reference_lost",
        "keyframe_requests","dependency_dropped","memory_rejected","clock_mapping_rejected",
        "clock_mappings_active","clock_mapping_evictions","clock_mapping_expired","logical_body_rejected",
        "completed_bodies","needs_keyframe","max_assembly_latency_us"};
    static final String[] EVENT_NAMES={"type","frame_id","host_capture_us","reference_id","flags",
        "first_arrival_us","last_arrival_us","fec_quorum_ready_us","event_phone_us","deadline_phone_us",
        "logical_bytes","reason_code","event_sequence","pts_us"};
    static final String[] EVENT_TYPES={"invalid","fec_quorum_ready","frame_delivered","frame_expired",
        "settled_by_delivered_frame","logical_rejected"};
    static final String[] EVENT_REASONS={"none","keyframe","predictive","assembly_deadline",
        "superseded_by_delivered_frame","malformed_logical_body","logical_chain_blocked"};
}
