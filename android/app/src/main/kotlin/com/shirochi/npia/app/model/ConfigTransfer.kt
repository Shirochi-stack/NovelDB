package com.shirochi.npia.app.model

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.DesktopConfig
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SettingsCoding
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/**
 * Desktop `config.json` and app settings files (iOS `ConfigTransferView` and `AppModel`).
 * Credentials found in a desktop file go to the secret store and are never kept or exported.
 */
object ConfigTransfer {
    /** Keys that hold credentials in the desktop file. */
    val credentialKeys = setOf("email", "wd", "loginkey")

    private val pretty = Json { prettyPrint = true }

    sealed class Imported {
        data class AppFile(val settings: AppSettings) : Imported()

        /** `base` is the desktop file without credentials, kept so exports preserve unknown keys. */
        data class Desktop(val settings: AppSettings, val base: JsonObject, val storedSecrets: Int) : Imported()
    }

    /** App exports carry `schemaVersion` and a `shared` object; desktop files are flat. */
    fun isAppSettingsFile(root: JsonObject): Boolean = "schemaVersion" in root && root["shared"] is JsonObject

    fun import(text: String, current: AppSettings, secrets: SecretStore): Imported {
        val root = Json.parseToJsonElement(text).jsonObject
        if (isAppSettingsFile(root)) return Imported.AppFile(SettingsCoding.decode(text))
        val (settings, found) = DesktopConfig.import(root, current)
        var stored = 0
        found.email?.let { secrets.set(SecretKey.NPIA_EMAIL, it); stored += 1 }
        found.password?.let { secrets.set(SecretKey.NPIA_PASSWORD, it); stored += 1 }
        found.loginKey?.let { secrets.set(SecretKey.NPIA_LOGIN_KEY, it); stored += 1 }
        return Imported.Desktop(settings, JsonObject(root - credentialKeys), stored)
    }

    /** Settings as a desktop `config.json` layered on the last imported file, keys sorted. No credentials. */
    fun exportDesktop(settings: AppSettings, base: JsonObject?): String {
        val exported = DesktopConfig.export(settings, JsonObject((base ?: JsonObject(emptyMap())) - credentialKeys))
        return pretty.encodeToString(JsonObject.serializer(), JsonObject(exported.toSortedMap()))
    }

    fun exportApp(settings: AppSettings): String = SettingsCoding.encode(settings)
}
