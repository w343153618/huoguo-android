package local.remoteandroid.direct;

/** Upgrade regressions: standard choices and historical selections are distinct. */
public final class StreamQualityCheck {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        int[] standard = {768, 960, 1280, 1920};
        for (int index = 0; index < standard.length; index++) {
            StreamQuality choices = new StreamQuality(standard[index]);
            check(choices.sizes.length == 4, "standard choice unexpectedly duplicated");
            check(choices.selected == index, "saved standard selection changed");
            check(choices.sizes[choices.selected] == standard[index], "selected size changed");
        }
        for (int historical : new int[]{1200, 1600, 2400}) {
            StreamQuality choices = new StreamQuality(historical);
            check(choices.sizes.length == 5 && choices.selected == 4, "missing historical item");
            check(choices.sizes[choices.selected] == historical, "upgrade overwrote accepted historical size");
            check(choices.labels[4].contains("历史设置") && choices.labels[4].contains(Integer.toString(historical)),
                    "historical choice is misleadingly labeled as a standard size");
        }
        check(StreamQuality.legacySize(0) == 960, "old low-quality index changed");
        check(StreamQuality.legacySize(1) == 1600, "old medium-quality index changed");
        check(StreamQuality.legacySize(2) == 2400, "old high-quality index changed");
        check(StreamQuality.legacySize(-5) == 960 && StreamQuality.legacySize(9) == 2400, "old index bounds changed");
        StreamQuality invalid = new StreamQuality(-1);
        check(invalid.sizes[invalid.selected] == 960, "invalid saved size lacks a safe default");
        System.out.println("StreamQualityCheck PASS: standard sizes, legacy index and exact historical selection preservation");
    }
}
