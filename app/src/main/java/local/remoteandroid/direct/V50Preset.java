package local.remoteandroid.direct;

/** Legacy wrapper referencing DeviceProfiles.REALME_V50_30FPS. */
final class V50Preset {
    final int maxSize,bitrate,fps,bufferMs;
    final String mode;
    V50Preset(boolean hardwareAvc,boolean hardwareAvc60){
        DeviceProfiles.Profile p = DeviceProfiles.REALME_V50_30FPS;
        maxSize = hardwareAvc ? p.maxSize : 960;
        bitrate = hardwareAvc ? p.bitrate : 1500000;
        mode = p.mode;
        fps = p.fps;
        bufferMs = p.bufferMs;
    }
}
