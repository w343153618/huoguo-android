package local.huoguo.sourceprobe;

/** Closed namespace only; this class has no Android or filesystem side effects. */
public final class SnapshotPath {
    private SnapshotPath() { }
    public static String directory(String relative) {
        if (relative == null || !relative.matches(
                "huoguo-source-ui-[0-9a-f]{24}/window\\.xml")) {
            throw new IllegalArgumentException("snapshot_relative_path_rejected");
        }
        return relative.substring(0, relative.indexOf('/'));
    }
}
