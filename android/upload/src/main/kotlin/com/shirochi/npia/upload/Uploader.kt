package com.shirochi.npia.upload

import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.UploadSettings
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import okhttp3.OkHttpClient
import java.io.File
import java.util.concurrent.TimeUnit

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
    /** For a ZIP: the original file names inside it, in order. Empty for a single file. */
    val contents: List<String> = emptyList(),
) {
    fun isExpired(at: Long = System.currentTimeMillis()): Boolean = expiresAt?.let { it <= at } ?: false

    /** True for a ZIP made of several files. */
    val isArchive: Boolean get() = contents.isNotEmpty()

    /** "NovelDB_3_EPUBs.zip (3 books)" for a ZIP, else just the file name. */
    val label: String get() = if (isArchive) "$fileName (${ZipBundling.describe(contents)})" else fileName
}

data class UploadProgress(val sentBytes: Long, val totalBytes: Long) {
    val fraction: Double get() = if (totalBytes > 0) minOf(1.0, sentBytes.toDouble() / totalBytes) else 0.0
}

sealed class UploadException(message: String) : Exception(message) {
    data class NotImplemented(val host: String) : UploadException("$host upload is not available in this build yet.")
    data class TooLarge(val host: String, val limitBytes: Long) : UploadException("$host accepts files up to $limitBytes bytes.")
    data class NotSignedIn(val host: String) : UploadException("Sign in to $host first.")
    data class NotConfigured(val what: String) : UploadException("$what is not configured in this build.")
    data class Rejected(val host: String, val detail: String) : UploadException("$host rejected the upload: $detail")
    data class BadResponse(val host: String, val status: Int, val body: String) : UploadException("$host returned HTTP $status: $body")
}

/** Credentials for account-based hosts, kept in `SecretStore` next to the core keys. */
object UploadSecrets {
    /** Pixeldrain API key (pixeldrain.com/user/api_keys). Anonymous uploads were removed. */
    val PIXELDRAIN_API_KEY = SecretKey("upload.pixeldrain.key")
    /** Optional Gofile account token; guest uploads work without it. */
    val GOFILE_TOKEN = SecretKey("upload.gofile.token")
    /** Optional Catbox user hash so uploads show up in the account. */
    val CATBOX_USER_HASH = SecretKey("upload.catbox.userhash")
    val GOOGLE_DRIVE_REFRESH_TOKEN = SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN
}

/** Everything an uploader needs besides the file. Tests swap the client for one that talks to MockWebServer. */
data class UploadContext(
    val secrets: SecretStore,
    val settings: UploadSettings = UploadSettings(),
    val client: OkHttpClient = defaultClient,
    /** null until the Google OAuth client ID is filled in (see `GoogleOAuthConfig`). */
    val googleOAuth: GoogleOAuthConfig? = GoogleOAuthConfig.configured(),
    /** Where folder zips are staged. */
    val temporaryDirectory: File = File(System.getProperty("java.io.tmpdir")),
    /** Epoch millis. */
    val now: () -> Long = System::currentTimeMillis,
    /**
     * Stem for file names hosts would otherwise mangle (`npia_123456`): a name that is not plain
     * ASCII and whose title keeps fewer than three letters or digits is uploaded as
     * `<fileId>.<extension>`. Set from the job; the display name is kept in results.
     */
    val fileId: String? = null,
) {
    companion object {
        /** Idle timeouts only, so multi-GB uploads on slow links are not cut off. */
        val defaultClient: OkHttpClient by lazy {
            OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .writeTimeout(120, TimeUnit.SECONDS)
                .readTimeout(300, TimeUnit.SECONDS)
                .build()
        }
    }
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

    /** False when an account host has no credentials yet, so the UI can show "Sign in". */
    fun isReady(context: UploadContext): Boolean = true

    suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)? = null): UploadResult

    fun check(file: File) {
        val limit = maxBytes ?: return
        if (file.length() > limit) throw UploadException.TooLarge(displayName, limit)
    }

    fun result(file: File, link: String, context: UploadContext, deleteLink: String? = null): UploadResult {
        val now = context.now()
        return UploadResult(
            hostId = id,
            fileName = uploadName(file),
            link = link,
            deleteLink = deleteLink,
            expiresAt = retentionSeconds?.let { now + it * 1000 },
            uploadedAt = now,
        )
    }
}

object UploaderCatalog {
    // Limits checked against each host's docs in October 2026. 0x0.st, file.io and transfer.sh
    // from bot.py are gone or closed to anonymous uploads and are not ported.
    val all: List<Uploader> = listOf(
        TempShUploader(),
        GofileUploader(),
        CatboxUploader(),
        PixeldrainUploader(),
        TmpfilesUploader(),
        KrakenFilesUploader(),
        SendNowUploader(),
        GoogleDriveUploader(),
    )

    /** Ids that were renamed; old settings keep working. */
    private val aliases = mapOf("sendcm" to "sendnow", "google_drive" to "gdrive")

    /**
     * A host that only debug builds set, to point uploads at a local test server (see
     * `LocalTestUploader`). It is found by id but never listed in `all`.
     */
    @Volatile
    var testHost: Uploader? = null

    fun uploader(id: String): Uploader? =
        (aliases[id] ?: id).let { key -> testHost?.takeIf { it.id == key } ?: all.firstOrNull { it.id == key } }

    /**
     * The largest ZIP this upload may build: the setting, or the first usable host's own maximum
     * when that is smaller (the hosts are tried in order, so the first one decides).
     */
    fun archiveLimit(settings: UploadSettings, context: UploadContext): Long {
        val first = settings.hosts.firstNotNullOfOrNull { id -> uploader(id)?.takeIf { it.isReady(context) } }
        return ZipBundling.limit(settings, first?.maxBytes)
    }

    /**
     * What goes up for `items`: with bundling on and two or more files, ZIPs of several files
     * (and the files too big for one, alone); otherwise one upload per file.
     */
    fun plan(items: List<UploadItem>, settings: UploadSettings, context: UploadContext): List<PlannedUpload> =
        if (settings.zipBundle && items.size >= 2) {
            ZipBundling.plan(items, archiveLimit(settings, context))
        } else {
            items.map { PlannedUpload.Single(it) }
        }

    /**
     * Uploads every entry of `plan` one after another, building each ZIP in the temporary
     * directory and deleting it afterwards. A failed upload does not stop the rest; its outcome
     * carries the error. Results (with `contents` for ZIPs) are added to `history`.
     */
    suspend fun uploadAll(
        plan: List<PlannedUpload>,
        settings: UploadSettings,
        context: UploadContext,
        history: UploadHistoryStore? = UploadHistoryStore.shared,
        listener: UploadListener = UploadListener.None,
    ): List<UploadOutcome> {
        val archiveCount = plan.count { it is PlannedUpload.Archive }
        var archiveIndex = 0
        val outcomes = mutableListOf<UploadOutcome>()
        plan.forEachIndexed { index, planned ->
            val position = index + 1
            val outcome = when (planned) {
                is PlannedUpload.Single -> {
                    val name = uploadName(planned.item.file)
                    listener.onStart(position, plan.size, planned, name)
                    try {
                        val result = upload(
                            planned.item.file, settings, context, history,
                            progress = listener::onProgress, onHost = listener::onHost, bookId = planned.item.stem,
                        )
                        UploadOutcome(planned, name, result, null)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        UploadOutcome(planned, name, null, e.message ?: e.toString())
                    }
                }
                is PlannedUpload.Archive -> {
                    archiveIndex++
                    listener.onStart(position, plan.size, planned, planned.name)
                    listener.onPreparing(archiveIndex, archiveCount)
                    var staging: File? = null
                    try {
                        staging = ZipBundling.stagingDirectory(context.temporaryDirectory)
                        val zip = File(staging, planned.name)
                        val entries = ZipBundling.write(planned.items, zip)
                        val result = upload(
                            zip, settings, context, history = null,
                            progress = listener::onProgress, onHost = listener::onHost,
                        ).copy(contents = entries)
                        history?.add(result)
                        UploadOutcome(planned, planned.name, result, null)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        UploadOutcome(planned, planned.name, null, e.message ?: e.toString())
                    } finally {
                        staging?.deleteRecursively()
                    }
                }
            }
            listener.onFinished(position, outcome)
            outcomes += outcome
        }
        return outcomes
    }

    /**
     * Tries each configured host in order and returns the first success. Folders (separate TXT
     * output) are zipped first. Successful uploads are added to `history`. `onHost` gets the
     * display name of each host just before it is tried, so the UI can show the fallback order.
     * `bookId` is the stem (`npia_123456`) for uploads whose file name is not plain ASCII (see `UploadContext.fileId`).
     */
    suspend fun upload(
        file: File,
        settings: UploadSettings,
        context: UploadContext,
        history: UploadHistoryStore? = UploadHistoryStore.shared,
        progress: ((UploadProgress) -> Unit)? = null,
        onHost: ((String) -> Unit)? = null,
        bookId: String? = null,
    ): UploadResult {
        val ctx = context.copy(settings = settings, fileId = bookId ?: context.fileId)
        var payload = file
        var staged: File? = null
        if (file.isDirectory) {
            payload = FolderArchiver.zip(file, ctx.temporaryDirectory)
            staged = payload.parentFile
        }
        try {
            var last: Exception = UploadException.NotConfigured("No upload host")
            for (id in settings.hosts) {
                val uploader = uploader(id) ?: continue
                if (!uploader.isReady(ctx)) {
                    last = UploadException.NotSignedIn(uploader.displayName)
                    continue
                }
                try {
                    uploader.check(payload)
                    onHost?.invoke(uploader.displayName)
                    val result = uploader.upload(payload, ctx, progress)
                    history?.add(result)
                    return result
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    last = e
                }
            }
            throw last
        } finally {
            staged?.deleteRecursively()
        }
    }
}

/** What became of one entry of an upload plan. */
data class UploadOutcome(
    val upload: PlannedUpload,
    /** The name shown for it: the file's name, or the ZIP's. */
    val name: String,
    val result: UploadResult?,
    val error: String?,
) {
    val succeeded: Boolean get() = result != null

    /** Original file names this upload carried. */
    val fileNames: List<String> get() = upload.items.map { ZipBundling.entryName(it.file) }
}

/** Progress of `UploaderCatalog.uploadAll`, for the screens and notifications. */
interface UploadListener {
    /** `upload` is about to start; `position` counts from 1 among `total` uploads. */
    fun onStart(position: Int, total: Int, upload: PlannedUpload, name: String) {}

    /** A ZIP is being built: ZIP `index` of `total`, from 1. */
    fun onPreparing(index: Int, total: Int) {}

    /** The display name of the host being tried. */
    fun onHost(name: String) {}

    fun onProgress(progress: UploadProgress) {}

    fun onFinished(position: Int, outcome: UploadOutcome) {}

    companion object {
        val None = object : UploadListener {}
    }
}
