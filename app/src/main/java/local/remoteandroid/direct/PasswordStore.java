package local.remoteandroid.direct;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** One explicitly saved account; new saves bind full endpoint identity in AAD. Legacy keys are read explicitly. */
final class PasswordStore {
    private static final String ALIAS = "huoguo.saved-password.v1";
    private final SharedPreferences saved;
    PasswordStore(Context context) { saved = context.getSharedPreferences("saved_password", Context.MODE_PRIVATE); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return (SecretKey) store.getKey(ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).setRandomizedEncryptionRequired(true).build());
        return generator.generateKey();
    }
    private static byte[] account(String host, String user) {
        return ("huoguo-password-v1\n" + host + "\n" + user).getBytes(StandardCharsets.UTF_8);
    }
    void save(String host, String user, String password) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD(account(host, user));
        byte[] encrypted = cipher.doFinal(password.getBytes(StandardCharsets.UTF_8));
        if (!saved.edit().putString("host", host).putString("username", user)
                .putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .putString("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit())
            throw new java.io.IOException("无法保存密码，请重试");
    }
    String load(String host, String user) throws Exception {
        if (!host.equals(saved.getString("host", null)) || !user.equals(saved.getString("username", null))) return "";
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128,
                Base64.decode(saved.getString("iv", ""), Base64.NO_WRAP)));
        cipher.updateAAD(account(host, user));
        return new String(cipher.doFinal(Base64.decode(saved.getString("ciphertext", ""), Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }
    void clear() throws Exception {
        if (!saved.edit().clear().commit()) throw new java.io.IOException("清除失败，请重试");
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS);
    }
}
