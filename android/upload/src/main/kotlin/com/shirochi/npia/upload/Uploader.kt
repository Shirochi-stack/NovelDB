package com.shirochi.npia.upload

import com.shirochi.npia.core.UploadSettings
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import java.io.File

@Serializable
data class UploadResult(
    val hostId: String,
    val fileName: String,
    val link: String,
    /** Page or token that deletes the upload, when the host returns one. */
    val deleteLink: String? = null,
    /** Epoch millis. */
    val expiresAt: Long? = null,
    val uploadedAt: Long = System.currentTimeMillis(),
)

data class UploadProgress(val sentBytes: Long, val totalBytes: Long)

sealed class UploadException(message: String) : Exception(message) {
    class NotImplemented(host: String) : UploadException("$host upload is not available in this build yet.")
    class TooLarge(host: String, val limitBytes: Long) : UploadException("$host accepts files up to $limitBytes bytes.")
    class NotSignedIn(host: String) : UploadException("Sign in to $host first.")
    class Rejected(host: String, detail: String) : UploadException("$host rejected the upload: $detail")
}

/**
 * One share-link host. Ports of the `_upload_to_*` helpers in the desktop `bot.py` (and the iOS
 * `NpiaUpload` hosts), plus account-based hosts such as Google Drive.
 */
interface Uploader {
    /** Stable id stored in `UploadSettings.hosts` (e.g. `tempsh`, `gofile`, `gdrive`). */
    val id: String
    val displayName: String
    /** null when the host has no published limit. */
    val maxBytes: Long?
    /** How long anonymous uploads stay up, in seconds; null for permanent or account storage. */
    val retentionSeconds: Long?
    val requiresAccount: Boolean

    suspend fun upload(file: File, progress: ((UploadProgress) -> Unit)? = null): UploadResult

    fun check(file: File) {
        val limit = maxBytes ?: return
        if (file.length() > limit) throw UploadException.TooLarge(displayName, limit)
    }
}

/** Placeholder until the upload workstream ports the host. */
class PendingUploader(
    override val id: String,
    override val displayName: String,
    override val maxBytes: Long?,
    override val retentionSeconds: Long?,
    override val requiresAccount: Boolean = false,
) : Uploader {
    override suspend fun upload(file: File, progress: ((UploadProgress) -> Unit)?): UploadResult =
        throw UploadException.NotImplemented(displayName)
}

object UploaderCatalog {
    // TODO(upload workstream): replace each placeholder with a real uploader; the iOS `NpiaUpload`
    // module has current endpoints and limits (sendcm was renamed sendnow).
    val all: List<Uploader> = listOf(
        PendingUploader("tempsh", "temp.sh", 4_000_000_000, 3 * 86_400),
        PendingUploader("gofile", "Gofile", null, null),
        PendingUploader("catbox", "Catbox", 200_000_000, null),
        PendingUploader("pixeldrain", "Pixeldrain", null, null, requiresAccount = true),
        PendingUploader("tmpfiles", "tmpfiles", 100_000_000, 3_600),
        PendingUploader("krakenfiles", "KrakenFiles", null, null),
        PendingUploader("sendnow", "Send.now", null, null),
        PendingUploader("gdrive", "Google Drive", null, null, requiresAccount = true),
    )

    private val aliases = mapOf("sendcm" to "sendnow", "google_drive" to "gdrive")

    fun uploader(id: String): Uploader? = (aliases[id] ?: id).let { key -> all.firstOrNull { it.id == key } }

    /** Tries each configured host in order and returns the first success. */
    suspend fun upload(file: File, settings: UploadSettings, progress: ((UploadProgress) -> Unit)? = null): UploadResult {
        var last: Exception = UploadException.NotImplemented("upload")
        for (id in settings.hosts) {
            val uploader = uploader(id) ?: continue
            try {
                uploader.check(file)
                return uploader.upload(file, progress)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                last = e
            }
        }
        throw last
    }
}
