package local.remoteandroid.direct;

import android.media.*;
import android.os.Build;

/** Read-only advertised capabilities; does not instantiate codecs or change the OS. */
final class UdpDeviceCapabilities {
    private UdpDeviceCapabilities(){}
    static boolean lowLoadDefault(){return UdpLowLoadProfile.preferLowLoad(Build.MANUFACTURER,Build.MODEL,
        Build.HARDWARE,Build.BOARD,Build.VERSION.SDK_INT>=31?Build.SOC_MODEL:"");}
    static String summary(){
        StringBuilder text=new StringBuilder("本机硬件解码能力（厂商声明）\n");
        for(String mime:new String[]{"video/avc","video/hevc"}){
            boolean found=false;
            try{for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()){
                if(info.isEncoder()||info.isAlias()||!info.isHardwareAccelerated()
                    ||info.isSoftwareOnly()||MainActivity.virtualCodec(info.getName()))continue;
                for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase(mime)){
                    try{
                        MediaCodecInfo.CodecCapabilities cap=info.getCapabilitiesForType(type);
                        if(cap.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_SecurePlayback)
                            ||cap.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_TunneledPlayback))continue;
                        MediaCodecInfo.VideoCapabilities video=cap.getVideoCapabilities();
                        boolean thirty=video.areSizeAndRateSupported(540,960,30);
                        boolean sixty=video.areSizeAndRateSupported(540,960,60);
                        text.append(mime.equals("video/avc")?"H.264":"H.265").append(" · ").append(info.getName())
                            .append("\n540P：30 FPS ").append(thirty?"支持":"未声明")
                            .append("，60 FPS ").append(sixty?"支持":"未声明").append("\n");found=true;
                    }catch(RuntimeException ignored){}
                }
            }}catch(RuntimeException ignored){}
            if(!found)text.append(mime.equals("video/avc")?"H.264":"H.265").append("：未找到可用的硬解声明\n");
        }
        return text.append("当前 UDP 串流使用 H.264。声明支持不代表持续播放已测通过；正在使用的解码器以连接后的报告为准。").toString();
    }
}
