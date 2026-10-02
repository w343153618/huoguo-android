package local.remoteandroid.direct;

/** Long-edge choices; keep an accepted historical value selected during upgrades. */
final class StreamQuality {
    private static final int[] STANDARD = {768, 960, 1280, 1920};
    private static final String[] LABELS = {
        "432P · 流畅画质 · 16:9 时 432 × 768",
        "540P · DVD清晰度 · 16:9 时 540 × 960",
        "720P · 主流高清 · 16:9 时 720 × 1280",
        "1080P · 蓝光 · 16:9 时 1080 × 1920"
    };
    final int[] sizes;
    final String[] labels;
    final int selected;

    StreamQuality(int remembered) {
        boolean historical = remembered == 1200 || remembered == 1600 || remembered == 2400;
        sizes = new int[STANDARD.length + (historical ? 1 : 0)];
        labels = new String[sizes.length];
        System.arraycopy(STANDARD, 0, sizes, 0, STANDARD.length);
        System.arraycopy(LABELS, 0, labels, 0, LABELS.length);
        if (historical) {
            sizes[STANDARD.length] = remembered;
            labels[STANDARD.length] = "历史设置 · 长边 " + remembered + " · 保留上次清晰度";
        }
        int selection = 1;
        for (int index = 0; index < sizes.length; index++)
            if (sizes[index] == remembered) selection = index;
        selected = selection;
    }

    static int legacySize(int qualityIndex) {
        return new int[]{960, 1600, 2400}[Math.max(0, Math.min(2, qualityIndex))];
    }
}
