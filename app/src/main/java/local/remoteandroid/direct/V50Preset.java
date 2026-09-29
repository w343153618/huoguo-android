package local.remoteandroid.direct;

/** Conservative starting point; real-phone sustained playback remains the acceptance test. */
final class V50Preset {
    final int maxSize,bitrate,fps,bufferMs;
    final String mode="VBR";
    V50Preset(boolean hardwareAvc,boolean hardwareAvc60){
        maxSize=hardwareAvc?1200:960;
        bitrate=hardwareAvc?2500000:1500000;
        // Advertised 60 FPS support is not sustained V50 playback validation.
        fps=30;
        bufferMs=120;
    }
}
