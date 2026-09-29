package local.remoteandroid.direct;

import android.media.*;

/** Enumerate the actual device's advertised hardware decoder capabilities, without decoding a stream. */
final class DeviceCodecs {
    static boolean hardware(String mime,int width,int height){
        return hardwareCheck(mime,width,height,0);
    }
    static boolean hardwareRate(String mime,int width,int height,int fps){
        return hardwareCheck(mime,width,height,fps);
    }
    private static boolean hardwareCheck(String mime,int width,int height,int fps){
        for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()){
            if(info.isEncoder()||info.isAlias()||!info.isHardwareAccelerated()||MainActivity.virtualCodec(info.getName()))continue;
            for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase(mime)){
                try{
                    MediaCodecInfo.CodecCapabilities caps=info.getCapabilitiesForType(type);
                    if(!caps.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_SecurePlayback)
                            &&!caps.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_TunneledPlayback)
                            &&caps.isFormatSupported(MediaFormat.createVideoFormat(mime,width,height))
                            &&(fps==0||caps.getVideoCapabilities().areSizeAndRateSupported(width,height,fps)))return true;
                }catch(Exception ignored){}
            }
        }
        return false;
    }
}
