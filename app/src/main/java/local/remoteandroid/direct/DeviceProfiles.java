package local.remoteandroid.direct;

import android.os.Build;
import java.util.Locale;

final class DeviceProfiles {

    static final class Profile {
        final String id;
        final String title;
        final String subtitle;
        final String summary;
        final int maxSize;
        final int bitrate;
        final int fps;
        final int bufferMs;
        final String mode;
        final float audioGain;
        final int avSyncOffsetMs;
        final boolean showMetrics;
        final boolean lowLatencyVendor;

        Profile(String id, String title, String subtitle, String summary,
                int maxSize, int bitrate, int fps, int bufferMs,
                String mode, float audioGain, int avSyncOffsetMs, boolean showMetrics, boolean lowLatencyVendor) {
            this.id = id;
            this.title = title;
            this.subtitle = subtitle;
            this.summary = summary;
            this.maxSize = maxSize;
            this.bitrate = bitrate;
            this.fps = fps;
            this.bufferMs = bufferMs;
            this.mode = mode;
            this.audioGain = audioGain;
            this.avSyncOffsetMs = avSyncOffsetMs;
            this.showMetrics = showMetrics;
            this.lowLatencyVendor = lowLatencyVendor;
        }
    }

    // 方案一：一加 15 / 高通骁龙旗舰满血 60 帧方案
    static final Profile ONEPLUS_60FPS = new Profile(
        "oneplus_60fps",
        "一加 15 · 满血 60 帧",
        "高通旗舰专属",
        "60 FPS 满血输出 · 8.0 Mbps 极清码率 · 60ms 极低延时缓冲 · QTI 极低延时硬解。实测刷抖音、快手满 60 帧丝滑跟手。",
        1200,      // 540P (540x1200)
        8000000,   // 8 Mbps
        60,        // 60 FPS
        60,        // 60 ms
        "ADAPTIVE_VBR",
        1.0f,
        -25,       // 声音超前 25ms 抵消扬声器硬件延迟
        true,
        true
    );

    // 方案二：真我 V50 / 联发科天玑稳定 30 帧方案
    static final Profile REALME_V50_30FPS = new Profile(
        "realme_v50_30fps",
        "真我 V50 · 稳定 30 帧",
        "联发科天玑专属",
        "30 FPS 锁定恒稳 · 3.5 Mbps 均衡码率 · 80ms 弹性平滑缓冲 · MTK 安全硬解 · 声音 1.5 倍增强。久刷不热、不卡、不跳帧。",
        1200,      // 540P (540x1200)
        3500000,   // 3.5 Mbps
        30,        // 30 FPS
        80,        // 80 ms (严格 <= 80ms)
        "ADAPTIVE_VBR",
        1.5f,
        -25,       // 声音超前 25ms 抵消扬声器硬件延迟
        false,
        false
    );

    static boolean isMediaTek() {
        String hw = (Build.HARDWARE == null ? "" : Build.HARDWARE).toLowerCase(Locale.ROOT);
        String board = (Build.BOARD == null ? "" : Build.BOARD).toLowerCase(Locale.ROOT);
        String mfg = (Build.MANUFACTURER == null ? "" : Build.MANUFACTURER).toLowerCase(Locale.ROOT);
        String model = (Build.MODEL == null ? "" : Build.MODEL).toUpperCase(Locale.ROOT);
        String soc = "";
        if (Build.VERSION.SDK_INT >= 31) {
            soc = (Build.SOC_MODEL == null ? "" : Build.SOC_MODEL).toLowerCase(Locale.ROOT);
        }
        return hw.contains("mt") || hw.contains("mediatek")
            || board.contains("mt") || board.contains("mediatek")
            || soc.contains("mt") || soc.contains("dimensity")
            || model.startsWith("RMX378") // Realme V50 / V50s
            || (mfg.contains("realme") && !isQualcomm());
    }

    static boolean isQualcomm() {
        String hw = (Build.HARDWARE == null ? "" : Build.HARDWARE).toLowerCase(Locale.ROOT);
        String board = (Build.BOARD == null ? "" : Build.BOARD).toLowerCase(Locale.ROOT);
        String mfg = (Build.MANUFACTURER == null ? "" : Build.MANUFACTURER).toLowerCase(Locale.ROOT);
        String soc = "";
        if (Build.VERSION.SDK_INT >= 31) {
            soc = (Build.SOC_MODEL == null ? "" : Build.SOC_MODEL).toLowerCase(Locale.ROOT);
        }
        return hw.contains("qcom") || hw.contains("qualcomm")
            || board.contains("sm") || board.contains("qcom")
            || soc.contains("sm") || soc.contains("snapdragon")
            || mfg.contains("oneplus");
    }

    static Profile detectDefaultProfile() {
        if (isMediaTek()) {
            return REALME_V50_30FPS;
        }
        return ONEPLUS_60FPS;
    }
}
