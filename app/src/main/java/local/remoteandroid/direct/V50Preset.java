package local.remoteandroid.direct;

/** Conservative starting point; real-phone sustained playback remains the acceptance test. */
final class V50Preset {
    final int maxSize,bitrate,fps,bufferMs;
    final String mode;
    V50Preset(boolean hardwareAvc,boolean hardwareAvc60){
        maxSize=hardwareAvc?960:768;
        bitrate=hardwareAvc?4000000:1500000;
        mode=hardwareAvc?"ADAPTIVE_VBR":"VBR";
        // Advertised 60 FPS support is not sustained V50 playback validation.
        fps=30;
        bufferMs=80;
    }
}
