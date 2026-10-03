package local.remoteandroid.direct;

/** Owned host JNI fixture: no Android, codec, socket or media connection. */
public final class NativeMappingJniProbe {
    private static int checks;
    private static void require(boolean ok){checks++;if(!ok)throw new AssertionError("actual JNI fixture");}
    public static void main(String[] args)throws Exception {
        NativeUdpFec.load(args[0]);long handle=NativeUdpFec.nativeCreate();require(handle>0);
        try{
            long[] disabled=NativeUdpFec.mappingDetailsChecked(handle);
            require(disabled.length==292&&disabled[0]==1&&disabled[1]==0&&disabled[2]==15);
            require(disabled[17]==0&&disabled[33]==0&&disabled[34]==0);
            NativeUdpFec.setMappingDiagnosticsChecked(handle,true);
            long[] enabled=NativeUdpFec.mappingDetailsChecked(handle);
            require(enabled[1]==1&&enabled[26]==1&&enabled[27]==0);
            require(NativeUdpFec.nativeExpire(handle,1000).length==0);
            long[] tick=NativeUdpFec.mappingDetailsChecked(handle);
            require(tick[33]==1000&&tick[17]==0);
            long[] legacy=NativeUdpFec.nativeStats(handle);require(legacy.length==21);
            NativeUdpFec.setMappingDiagnosticsChecked(handle,false);
            long[] off=NativeUdpFec.mappingDetailsChecked(handle);
            require(off[1]==0&&off[26]==1&&off[27]==1&&off[33]==1000);
            require(java.util.Arrays.equals(legacy,NativeUdpFec.nativeStats(handle)));
        }finally{NativeUdpFec.nativeDestroy(handle);}
        try{NativeUdpFec.mappingDetailsChecked(handle);throw new AssertionError("closed handle read accepted");}
        catch(IllegalStateException wanted){checks++;}
        try{NativeUdpFec.setMappingDiagnosticsChecked(handle,true);throw new AssertionError("closed handle setter accepted");}
        catch(IllegalStateException wanted){checks++;}
        System.out.println("PASS "+checks+" actual host JNI mapping bridge checks (offline; no phone)");
    }
}
