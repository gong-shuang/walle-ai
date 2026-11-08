package www.walle.ai.net;

import android.content.Context;
import android.content.SharedPreferences;
import android.text.TextUtils;
import android.util.Log;

import java.util.Random;

/**
 * MqttConfig 的 SharedPreferences 持久化。
 * <p>
 * 用户名 / 密码不再有代码默认值：首次为空，用户输入后写入本地；
 * 凭据用 {@link CredentialCrypto}（Keystore AES-GCM）加密后保存，prefs 里没有明文。
 * 旧版本留下的明文 username / password 会在首次读取时自动迁移并删除。
 */
public class MqttConfigStore {

    private static final String TAG = "MqttConfigStore";
    private static final String PREFS = "mqtt_config";
    private static final String K_HOST = "host";
    private static final String K_PORT = "port";
    /** 旧版本的明文凭据键，仅用于升级迁移 */
    private static final String K_LEGACY_USER = "username";
    private static final String K_LEGACY_PASS = "password";
    /** 加密后的凭据 */
    private static final String K_USER_ENC = "username_enc";
    private static final String K_PASS_ENC = "password_enc";
    private static final String K_CRED_MIGRATED = "credential_migrated";
    private static final String K_CID = "client_id";
    private static final String K_AUTO_RECONN = "auto_reconnect";
    private static final String K_AUTO_CONN = "auto_connect";
    private static final String K_INTERVAL = "status_interval";
    private static final String K_TLS = "tls";

    private final SharedPreferences sp;

    public MqttConfigStore(Context ctx) {
        sp = ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public MqttConfig load() {
        migrateLegacyCredentials();
        MqttConfig c = new MqttConfig();
        c.host = sp.getString(K_HOST, c.host);
        c.port = sp.getInt(K_PORT, c.port);
        // 老配置没有 tls 键：按端口推断（8883/8084 是 EMQX 的 TLS 监听），
        // 避免用户「只把端口改成 8883」后仍然被当成明文去连。
        c.tls = sp.getBoolean(K_TLS, MqttConfig.isTlsPort(c.port));
        c.username = orEmpty(CredentialCrypto.decrypt(sp.getString(K_USER_ENC, null)));
        c.password = orEmpty(CredentialCrypto.decrypt(sp.getString(K_PASS_ENC, null)));
        c.clientId = sp.getString(K_CID, c.clientId);
        // v1.2：clientId 必须是「每次安装唯一」。旧版本固定用 android-controller，
        // 多台设备/多份安装同时在线时会被 broker 互相顶掉，表现为连接状态反复跳变。
        if (c.clientId == null || c.clientId.trim().isEmpty()
                || Topics.DEFAULT_CLIENT_ID.equals(c.clientId.trim())) {
            c.clientId = newClientId();
            sp.edit().putString(K_CID, c.clientId).apply();
        }
        c.autoReconnect = sp.getBoolean(K_AUTO_RECONN, true);
        c.autoConnectOnLaunch = sp.getBoolean(K_AUTO_CONN, true);
        c.statusIntervalSec = sp.getInt(K_INTERVAL, 5);
        return c;
    }

    /** 本地是否已有可用的用户名 + 密码（首次安装为 false）。 */
    public boolean hasCredentials() {
        migrateLegacyCredentials();
        String user = CredentialCrypto.decrypt(sp.getString(K_USER_ENC, null));
        String pass = CredentialCrypto.decrypt(sp.getString(K_PASS_ENC, null));
        return !TextUtils.isEmpty(user) && !TextUtils.isEmpty(pass);
    }

    /** 生成每次安装唯一的 clientId：android-controller-xxxxxx */
    public static String newClientId() {
        final String chars = "abcdefghijklmnopqrstuvwxyz0123456789";
        Random r = new Random();
        StringBuilder sb = new StringBuilder(Topics.CLIENT_ID_PREFIX);
        for (int i = 0; i < 6; i++) sb.append(chars.charAt(r.nextInt(chars.length())));
        return sb.toString();
    }

    public void save(MqttConfig c) {
        SharedPreferences.Editor e = sp.edit()
                .putString(K_HOST, c.host)
                .putInt(K_PORT, c.port)
                .putString(K_CID, c.clientId)
                .putBoolean(K_AUTO_RECONN, c.autoReconnect)
                .putBoolean(K_AUTO_CONN, c.autoConnectOnLaunch)
                .putBoolean(K_TLS, c.tls)
                .putInt(K_INTERVAL, c.statusIntervalSec)
                // 迁移标记：从此不再读写旧明文键
                .remove(K_LEGACY_USER)
                .remove(K_LEGACY_PASS)
                .putBoolean(K_CRED_MIGRATED, true);
        putEncrypted(e, K_USER_ENC, c.username);
        putEncrypted(e, K_PASS_ENC, c.password);
        e.apply();
    }

    /** 把旧版本的明文凭据加密迁移到新键，并删除明文（只执行一次）。 */
    private void migrateLegacyCredentials() {
        if (sp.getBoolean(K_CRED_MIGRATED, false)) return;
        String legacyUser = sp.getString(K_LEGACY_USER, null);
        String legacyPass = sp.getString(K_LEGACY_PASS, null);
        SharedPreferences.Editor e = sp.edit();
        if (!TextUtils.isEmpty(legacyUser)) {
            String enc = CredentialCrypto.encrypt(legacyUser);
            if (enc != null) e.putString(K_USER_ENC, enc);
        }
        if (!TextUtils.isEmpty(legacyPass)) {
            String enc = CredentialCrypto.encrypt(legacyPass);
            if (enc != null) e.putString(K_PASS_ENC, enc);
        }
        e.remove(K_LEGACY_USER)
                .remove(K_LEGACY_PASS)
                .putBoolean(K_CRED_MIGRATED, true)
                .apply();
    }

    /** 加密写入；加密失败时不写（保留旧值），绝不明文回退。 */
    private static void putEncrypted(SharedPreferences.Editor e, String key, String plain) {
        String enc = CredentialCrypto.encrypt(plain == null ? "" : plain);
        if (enc != null) e.putString(key, enc);
        else Log.w(TAG, "encrypt failed, keep previous value for " + key);
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }
}
