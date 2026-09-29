package local.remoteandroid.direct;

import android.content.*;
import android.database.*;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.*;

/** Only the verified APK in this app's private cache can be granted to the installer. */
public final class UpdateProvider extends ContentProvider {
    public boolean onCreate() { return true; }
    private File apk(Uri uri) throws FileNotFoundException {
        if (!"/update.apk".equals(uri.getPath()) || uri.getQuery() != null
                || !(getContext().getPackageName() + ".updates").equals(uri.getAuthority()))
            throw new FileNotFoundException("Invalid update URI");
        File file = new File(getContext().getCacheDir(), "update.apk");
        if (!file.isFile()) throw new FileNotFoundException("Update missing");
        return file;
    }
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(apk(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }
    public String getType(Uri uri) { return "application/vnd.android.package-archive"; }
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        try {
            File file = apk(uri);
            String[] columns = projection == null ? new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
            MatrixCursor cursor = new MatrixCursor(columns); Object[] values = new Object[columns.length];
            for (int i = 0; i < columns.length; i++) {
                if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) values[i] = "HuoguoAndroid-update.apk";
                else if (OpenableColumns.SIZE.equals(columns[i])) values[i] = file.length();
            }
            cursor.addRow(values); return cursor;
        } catch (FileNotFoundException error) { return null; }
    }
    public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
    public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
