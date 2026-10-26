package com.shirochi.npia.app

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore

/** Credentials in Keystore-backed encrypted preferences (the Android counterpart of the iOS Keychain store). */
class EncryptedSecretStore(context: Context) : SecretStore {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "npia_secrets",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    override fun get(key: SecretKey): String? = prefs.getString(key.raw, null)

    override fun set(key: SecretKey, value: String?) {
        prefs.edit().apply {
            if (value.isNullOrEmpty()) remove(key.raw) else putString(key.raw, value)
        }.apply()
    }
}
