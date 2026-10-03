package local.remoteandroid.direct;

import java.util.Locale;

/** Beta-only conservative starting point, not a claim of V50 performance. */
final class UdpLowLoadProfile {
    private UdpLowLoadProfile(){}
    // Append 30 FPS: alpha6/7 persisted indices 0=60, 1=120 stay unchanged.
    static int fpsForIndex(int index){
        if(index==0)return 60;if(index==1)return 120;if(index==2)return 30;
        throw new IllegalArgumentException("fps_index_bound");
    }
    static int displayHint(int fps){return Math.max(60,fps);}
    static boolean preferLowLoad(String manufacturer,String model,String hardware,String board,String soc){
        String identity=lower(hardware)+" "+lower(board)+" "+lower(soc);
        boolean mediatek=identity.contains("mediatek")||identity.contains("dimensity")
            ||identity.matches(".*\\bmt[0-9]{4}[a-z0-9]*\\b.*");
        // Market model text is a hint only; never infer V50 from an RMX number.
        String name=lower(model);
        return mediatek||lower(manufacturer).contains("realme")&&name.matches(".*\\bv50\\b.*");
    }
    private static String lower(String value){return value==null?"":value.toLowerCase(Locale.ROOT);}
}
