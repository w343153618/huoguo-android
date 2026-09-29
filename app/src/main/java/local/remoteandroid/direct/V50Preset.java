package local.remoteandroid.direct;

/** Conservative starting point; real-phone sustained playback remains the acceptance test. */
final class V50Preset {
    final int maxSize,bitrate,fps,bufferMs;
    final String mode="VBR";
    V50Preset(boolean hardwareAvc,boolean hardwareAvc60){
        maxSize=hardwareAvc?1200:960;
        bitrate=hardwareAvc?2500000:1500000;
        fps=hardwareAvc&&hardwareAvc60?60:30;
        bufferMs=100;
    }
}
