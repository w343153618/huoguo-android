package local.huoguo.sourceprobe;

import android.os.Process;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import com.android.uiautomator.testrunner.UiAutomatorTestCase;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** Explicit standalone readonly hierarchy snapshot; no source-App attachment. */
@SuppressWarnings("deprecation")
public final class SourceSnapshot extends UiAutomatorTestCase {
    private static final long MAX_BYTES = 1048576;

    private static void require(boolean value) {
        if (!value) throw new IllegalStateException("snapshot_identity_rejected");
    }

    private static boolean absent(String path) throws ErrnoException {
        try { Os.lstat(path); return false; }
        catch (ErrnoException error) {
            if (error.errno == OsConstants.ENOENT) return true;
            throw error;
        }
    }

    private static boolean same(StructStat first, StructStat second) {
        return first.st_dev == second.st_dev && first.st_ino == second.st_ino
            && first.st_uid == second.st_uid && first.st_mode == second.st_mode;
    }

    private static void ownedDirectory(StructStat stat) {
        require(OsConstants.S_ISDIR(stat.st_mode) && stat.st_uid == Process.myUid()
            && (stat.st_mode & 07777) == 0700);
    }

    private static void ownedFile(StructStat stat) {
        require(OsConstants.S_ISREG(stat.st_mode) && stat.st_uid == Process.myUid()
            && (stat.st_mode & 07777) == 0600 && stat.st_nlink == 1
            && stat.st_size > 0 && stat.st_size < MAX_BYTES);
    }

    private static long startTicks() throws Exception {
        byte[] buffer = new byte[8192];
        int used;
        try (FileInputStream input = new FileInputStream("/proc/self/stat")) {
            used = input.read(buffer);
            require(used > 0 && used < buffer.length && input.read() == -1);
        }
        String raw = new String(buffer, 0, used, StandardCharsets.US_ASCII).trim();
        require(raw.startsWith(Process.myPid() + " (") && raw.lastIndexOf(')') > 0);
        String[] fields = raw.substring(raw.lastIndexOf(')') + 2).split(" +");
        require(fields.length >= 20 && fields[19].matches("[0-9]{1,19}"));
        long result = Long.parseLong(fields[19]);
        require(result > 0);
        return result;
    }

    public void testSnapshot() throws Exception {
        String relative = getParams().getString("relative");
        String namespace = SnapshotPath.directory(relative);
        File base = new File("/data/local/tmp");
        File directory = new File(base, namespace);
        File window = new File(base, relative);
        File completed = new File(directory, "completed");
        // Reject aliases/symlinks before asking the framework to serialize.
        require(directory.getCanonicalPath().equals(directory.getPath())
            && window.getCanonicalPath().equals(window.getPath()));
        StructStat before = Os.lstat(directory.getPath());
        ownedDirectory(before);
        require(absent(window.getPath()) && absent(completed.getPath()));
        long start = startTicks();

        // This installed-platform path lacks DumpCommand's explicit idle wait.
        // Runner setup/root traversal/serialization can still block or fail.
        getUiDevice().dumpWindowHierarchy(relative);

        StructStat after = Os.lstat(directory.getPath());
        ownedDirectory(after);
        require(same(before, after));
        StructStat file = Os.lstat(window.getPath());
        ownedFile(file);
        FileDescriptor check = Os.open(window.getPath(),
            OsConstants.O_RDONLY | OsConstants.O_NOFOLLOW, 0);
        try { require(same(file, Os.fstat(check))); }
        finally { Os.close(check); }
        require(start == startTicks());
        String receipt = "1 " + Process.myPid() + " " + Process.myUid() + " "
            + start + " " + before.st_dev + " " + before.st_ino + " "
            + file.st_dev + " " + file.st_ino + " " + file.st_size + "\n";
        require(receipt.length() <= 256);
        FileDescriptor output = Os.open(completed.getPath(), OsConstants.O_WRONLY
            | OsConstants.O_CREAT | OsConstants.O_EXCL | OsConstants.O_NOFOLLOW, 0600);
        try (FileOutputStream stream = new FileOutputStream(output)) {
            stream.write(receipt.getBytes(StandardCharsets.US_ASCII));
        }
        ownedFile(Os.lstat(completed.getPath()));
        require(same(before, Os.lstat(directory.getPath())));
        // Completion is a serialization receipt, not process exit or source identity.
    }
}
