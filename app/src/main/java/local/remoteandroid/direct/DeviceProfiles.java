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

    // 方案一：一加 15 / 高通骁龙旗舰 60 FPS 上限方案
    static final Profile ONEPLUS_60FPS = new Profile(
        "oneplus_60fps",
        "一加 15 · 60 FPS 上限",
        "高通旗舰专属",
        "540P · 4 Mbps · 60 FPS 上限 · 60 ms 缓冲。优先硬解，实际流畅度需结合片源和网络测量。",
        960,       // 540P at a 16:9 source aspect ratio
        4000000,   // 4 Mbps
        60,        // 60 FPS
        60,        // 60 ms
        "VBR",
        1.0f,
        0,         // Calibrate only after observing actual A/V skew
        true,
        true
    );

    // 方案二：真我 V50 / 联发科天玑 30 FPS 上限方案
    static final Profile REALME_V50_30FPS = new Profile(
        "realme_v50_30fps",
        "真我 V50 · 30 FPS 上限",
        "联发科天玑专属",
        "540P · 自适应 VBR · 4 Mbps 目标上限 · 30 FPS 上限 · 80 ms 缓冲 · 声音 1.5 倍。实际效果以 V50 检测为准。",
        960,       // 540P at a 16:9 source aspect ratio
        4000000,   // 4 Mbps
        30,        // 30 FPS
        80,        // 80 ms (严格 <= 80ms)
        "ADAPTIVE_VBR",
        1.5f,
        0,         // Calibrate only after observing actual A/V skew
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
