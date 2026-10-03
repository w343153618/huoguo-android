package local.remoteandroid.direct;

/** Actual Java contract only; no Android, native library, phone or network. */
public final class NativeMappingDetailsProbe {
    private static int checks;
    private static void require(boolean ok){checks++;if(!ok)throw new AssertionError("mapping detail fixture");}
    static long[] empty(){
        long[] v=new long[NativeUdpFec.MAPPING_DETAIL_LENGTH];v[0]=1;v[2]=15;v[20]=32;v[21]=8;v[32]=32;return v;
    }
    static long[] full(){
        long[] v=empty();v[1]=1;v[26]=1;v[3]=24;v[4]=v[5]=v[6]=8;v[34]=24;
        v[8]=v[9]=v[11]=v[12]=v[14]=v[15]=v[16]=8;
        v[10]=v[13]=80_000;v[17]=v[18]=32;v[22]=1;v[23]=32;
        v[24]=300_000_000_000L;v[25]=v[24]+31;v[33]=v[25];v[28]=v[29]=8;
        for(int row=0;row<32;row++){
            int at=36+row*8;long reason=row%4+1;
            v[at]=row+1;v[at+1]=row==31?Long.MAX_VALUE:1000+row;v[at+2]=v[24]+row;
            v[at+3]=reason;v[at+4]=v[at+5]=8;v[at+6]=80_000;
            if(reason==2||reason==4)v[at+7]=v[at+2]-1;
        }
        return v;
    }
    private static void accepts(long[] v)throws Exception {NativeUdpFec.validateMappingDetails(v);checks++;}
    private static void rejects(long[] v)throws Exception {
        try{NativeUdpFec.validateMappingDetails(v);throw new AssertionError("invalid snapshot accepted");}
        catch(NativeUdpFec.MappingDetailsException wanted){require(wanted.status==NativeUdpFec.MAPPING_DETAIL_INVALID);
            require(wanted.getMessage().equals("native_mapping_details_contract"));}
    }
    private static void invalidHeader(int index,long value)throws Exception {long[] v=full();v[index]=value;rejects(v);}
    private static void invalidEvent(int row,int column,long value)throws Exception {long[] v=full();v[36+row*8+column]=value;rejects(v);}
    public static void main(String[] args)throws Exception {
        require(NativeUdpFec.STAT_NAMES.length==21);require(NativeUdpFec.MAPPING_DETAIL_HEADER_NAMES.length==36);
        require(NativeUdpFec.MAPPING_DETAIL_EVENT_NAMES.length==8);
        accepts(empty());accepts(full());
        long[] disabled=empty();disabled[7]=disabled[34]=3;disabled[15]=8;disabled[16]=8;accepts(disabled);
        long[] evicted=full();evicted[19]=1000;evicted[17]+=1000;evicted[22]+=1000;evicted[23]+=1000;
        for(int row=0;row<32;row++)evicted[36+row*8]+=1000;accepts(evicted);
        long[] notTail=full();notTail[19]=1;notTail[17]++;rejects(notTail);
        long[] gap=full();gap[19]=1;gap[17]++;gap[23]++;
        for(int row=1;row<32;row++)gap[36+row*8]++;rejects(gap);
        rejects(null);rejects(new long[291]);rejects(new long[293]);
        invalidHeader(0,2);invalidHeader(1,2);invalidHeader(2,0);invalidHeader(3,25);invalidHeader(4,7);
        invalidHeader(7,1);invalidHeader(8,9);invalidHeader(9,9);invalidHeader(10,80_001);invalidHeader(11,7);
        invalidHeader(12,7);invalidHeader(14,7);invalidHeader(15,9);invalidHeader(16,9);
        invalidHeader(17,33);invalidHeader(18,33);invalidHeader(19,1);invalidHeader(20,31);invalidHeader(21,7);
        invalidHeader(22,2);invalidHeader(23,31);invalidHeader(24,1);invalidHeader(25,1);
        invalidHeader(29,9);invalidHeader(30,33);invalidHeader(32,31);invalidHeader(33,1);invalidHeader(35,1);
        invalidHeader(26,-1);
        invalidEvent(0,0,0);invalidEvent(1,0,1);invalidEvent(0,1,0);invalidEvent(0,2,0);
        invalidEvent(2,2,300_000_000_000L);invalidEvent(0,3,0);invalidEvent(0,3,5);
        invalidEvent(0,4,9);invalidEvent(0,5,9);invalidEvent(0,6,-1);
        invalidEvent(1,7,0);invalidEvent(3,7,300_000_000_004L);invalidEvent(0,7,1);
        long[] cleared=full();cleared[17]=33;cleared[18]=1;cleared[19]=31;cleared[35]=1;
        cleared[22]=cleared[23]=cleared[36]=33;cleared[25]=cleared[24];
        java.util.Arrays.fill(cleared,44,cleared.length,0);accepts(cleared);
        cleared[44]=1;rejects(cleared);
        long[] overflow=empty();overflow[3]=Long.MAX_VALUE;overflow[4]=Long.MAX_VALUE;overflow[7]=1;overflow[34]=Long.MAX_VALUE;
        rejects(overflow);
        try{NativeUdpFec.setMappingDiagnosticsChecked(1,true);throw new AssertionError("missing setter JNI accepted");}
        catch(NativeUdpFec.MappingDetailsException missing){require(missing.status==NativeUdpFec.MAPPING_DETAIL_JNI_UNAVAILABLE);
            require(missing.getCause() instanceof UnsatisfiedLinkError);}
        try{NativeUdpFec.mappingDetailsChecked(1);throw new AssertionError("missing read JNI accepted");}
        catch(NativeUdpFec.MappingDetailsException missing){require(missing.status==NativeUdpFec.MAPPING_DETAIL_JNI_UNAVAILABLE);
            require(missing.getCause() instanceof UnsatisfiedLinkError);}
        System.out.println("PASS "+checks+" Java native mapping detail contract checks (offline; missing JNI explicit)");
    }
}
