package com.shirochi.npia.core

import java.util.concurrent.ConcurrentHashMap

/** Credentials never live in `AppSettings`; they go through this store. The app uses EncryptedSharedPreferences. */
interface SecretStore {
    fun get(key: SecretKey): String?
    fun set(key: SecretKey, value: String?)
}

@JvmInline
value class SecretKey(val raw: String) {
    companion object {
        val NPIA_EMAIL = SecretKey("npia.email")
        val NPIA_PASSWORD = SecretKey("npia.password")
        val NPIA_LOGIN_KEY = SecretKey("npia.loginkey")
        val GOOGLE_DRIVE_REFRESH_TOKEN = SecretKey("upload.gdrive.refresh")

        fun source(id: SourceId, name: String) = SecretKey("${id.raw}.$name")
    }
}

class InMemorySecretStore(initial: Map<SecretKey, String> = emptyMap()) : SecretStore {
    private val values = ConcurrentHashMap(initial)

    override fun get(key: SecretKey): String? = values[key]

    override fun set(key: SecretKey, value: String?) {
        if (value.isNullOrEmpty()) values.remove(key) else values[key] = value
    }
}
