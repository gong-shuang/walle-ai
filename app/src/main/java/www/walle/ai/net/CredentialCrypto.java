package www.walle.ai.net;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * MQTT 用户名 / 密码的本地加密。
 * <p>
 * 密钥由 Android Keystore 生成并保存在 TEE/StrongBox（不可导出），明文不会落盘：
 * SharedPreferences 里只存 {@code base64(iv):base64(cipher)}。
 * minSdk 26，Keystore AES-GCM（API 23+）可直接使用，无需额外依赖。
 * <p>
 * 失败（如换机恢复备份、系统升级导致密钥失效）时返回 null，
 * 调用方按「本地无凭据」处理，由用户重新输入即可。
 */
public final class CredentialCrypto {

    private static final String TAG = "CredentialCrypto";
    private static final String ANDROID_KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "walle_mqtt_credentials";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int GCM_TAG_BITS = 128;
    private static final String SEP = ":";
    private static final Object KEY_LOCK = new Object();

    private CredentialCrypto() {}

    /** 加密；失败返回 null（调用方应放弃写入，避免明文落盘）。 */
    public static String encrypt(String plain) {
        if (plain == null) return null;
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, secretKey());
            byte[] iv = cipher.getIV();
            byte[] cipherText = cipher.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            return Base64.encodeToString(iv, Base64.NO_WRAP)
                    + SEP
                    + Base64.encodeToString(cipherText, Base64.NO_WRAP);
        } catch (Exception e) {
            Log.w(TAG, "encrypt failed", e);
            return null;
        }
    }

    /** 解密；数据缺失或密钥失效返回 null。 */
    public static String decrypt(String stored) {
        if (stored == null || stored.isEmpty()) return null;
        int idx = stored.indexOf(SEP);
        if (idx <= 0 || idx >= stored.length() - 1) return null;
        try {
            byte[] iv = Base64.decode(stored.substring(0, idx), Base64.NO_WRAP);
            byte[] cipherText = Base64.decode(stored.substring(idx + 1), Base64.NO_WRAP);
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, secretKey(),
                    new GCMParameterSpec(GCM_TAG_BITS, iv));
            return new String(cipher.doFinal(cipherText), StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.w(TAG, "decrypt failed", e);
            return null;
        }
    }

    /** 取出（必要时生成）Keystore 中的 AES-256 密钥。 */
    private static SecretKey secretKey() throws Exception {
        synchronized (KEY_LOCK) {
            KeyStore ks = KeyStore.getInstance(ANDROID_KEYSTORE);
            ks.load(null);
            if (ks.containsAlias(KEY_ALIAS)) {
                return ((KeyStore.SecretKeyEntry) ks.getEntry(KEY_ALIAS, null)).getSecretKey();
            }
            KeyGenerator kg = KeyGenerator.getInstance(
                    KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE);
            kg.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build());
            return kg.generateKey();
        }
    }
}
