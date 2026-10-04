package local.huoguo.sourceprobe;

import android.os.Process;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** Closed numeric normal-return receipt; not global service quiescence. */
final class RetirementReceipt {
    private final File directory;
    private final StructStat before;
    private final long start;
    private boolean written;

    private static void require(boolean ok) {
        if (!ok) throw new IllegalStateException("retirement_identity_rejected");
    }
    private static boolean same(StructStat a, StructStat b) {
        return a.st_dev == b.st_dev && a.st_ino == b.st_ino
            && a.st_uid == b.st_uid && a.st_mode == b.st_mode;
    }
    private static void privateDirectory(StructStat s) {
        require(OsConstants.S_ISDIR(s.st_mode) && s.st_uid == Process.myUid()
            && (s.st_mode & 07777) == 0700);
    }
    private static void absent(String path) throws ErrnoException {
        try { Os.lstat(path); }
        catch (ErrnoException error) {
            if (error.errno == OsConstants.ENOENT) return;
            throw error;
        }
        throw new IllegalStateException("retirement_file_exists");
    }
    private static long startTicks() throws Exception {
        byte[] bytes = new byte[8192]; int used;
        try (FileInputStream in = new FileInputStream("/proc/self/stat")) {
            used = in.read(bytes);
            require(used > 0 && used < bytes.length && in.read() == -1);
        }
        String raw = new String(bytes, 0, used, StandardCharsets.US_ASCII).trim();
        require(raw.startsWith(Process.myPid() + " (") && raw.lastIndexOf(')') > 0);
        String[] fields = raw.substring(raw.lastIndexOf(')') + 2).split(" +");
        require(fields.length >= 20 && fields[19].matches("[0-9]{1,19}"));
        long value = Long.parseLong(fields[19]); require(value > 0); return value;
    }
    private RetirementReceipt(String relative) throws Exception {
        directory = new File("/data/local/tmp", SnapshotPath.directory(relative));
        require(directory.getCanonicalPath().equals(directory.getPath()));
        before = Os.lstat(directory.getPath()); privateDirectory(before);
        absent(new File(directory, "retired").getPath());
        start = startTicks();
    }
    static RetirementReceipt prepare(String relative) {
        try { return new RetirementReceipt(relative); }
        catch (Exception e) { throw new IllegalStateException("retirement_prepare_failed", e); }
    }
    void normalStartReturned() {
        require(!written);
        try {
            StructStat after = Os.lstat(directory.getPath()); privateDirectory(after);
            require(same(before, after) && start == startTicks());
            File target = new File(directory, "retired");
            absent(target.getPath());
            String row = "1 " + Process.myPid() + " " + Process.myUid() + " "
                + start + " " + before.st_dev + " " + before.st_ino + " 1\n";
            require(row.length() <= 192);
            FileDescriptor fd = Os.open(target.getPath(), OsConstants.O_WRONLY
                | OsConstants.O_CREAT | OsConstants.O_EXCL | OsConstants.O_NOFOLLOW, 0600);
            try (FileOutputStream out = new FileOutputStream(fd)) {
                StructStat file = Os.fstat(fd);
                require(OsConstants.S_ISREG(file.st_mode) && file.st_uid == Process.myUid()
                    && (file.st_mode & 07777) == 0600 && file.st_nlink == 1);
                out.write(row.getBytes(StandardCharsets.US_ASCII)); out.flush(); Os.fsync(fd);
                StructStat finalFile = Os.fstat(fd);
                require(same(file, finalFile) && same(file, Os.lstat(target.getPath()))
                    && finalFile.st_size == row.length() && finalFile.st_nlink == 1);
            }
            require(same(before, Os.lstat(directory.getPath())) && start == startTicks());
            written = true;
        } catch (Exception e) {
            throw new IllegalStateException("retirement_write_failed", e);
        }
    }
}
