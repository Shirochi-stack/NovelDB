package com.shirochi.npia.upload

import com.shirochi.npia.core.SecretKey
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.file.Files
import kotlin.coroutines.coroutineContext

/**
 * Uploads into the user's Drive with the `drive.file` scope (the app only sees files it created),
 * then shares the file as "anyone with the link".
 */
class GoogleDriveUploader(
    /** 8 MiB; Drive wants chunks in multiples of 256 KiB. */
    val chunkSize: Int = 32 * 256 * 1024,
    internal val maxChunkRetries: Int = 5,
    /** Back-off before asking Drive where an interrupted session stands. */
    internal val retryDelayMillis: (failures: Int) -> Long = { failures -> minOf(30L, 1L shl failures) * 200 },
) : Uploader {
    override val id = "gdrive"
    override val displayName = "Google Drive"
    /** Drive's per-file limit; the account quota usually binds first. */
    override val maxBytes: Long? = 5_000_000_000_000
    override val retentionSeconds: Long? = null
    override val requiresAccount = true

    override fun isReady(context: UploadContext): Boolean =
        context.googleAccessToken != null || (context.googleOAuth != null && !context.secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN).isNullOrEmpty())

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        // A 308 from Drive means "resume incomplete", never a redirect to follow.
        val client = context.client.newBuilder().followRedirects(false).build()
        val auth: suspend (Boolean) -> String = context.googleAccessToken ?: run {
            val config = context.googleOAuth ?: throw UploadException.NotConfigured("Google Drive client ID")
            val legacy = GoogleDriveAuth(config, context.secrets, client, context.now)
            val provider: suspend (Boolean) -> String = { force -> legacy.accessToken(force) }
            provider
        }
        if (file.isDirectory) return uploadFolder(file, context, client, auth, progress)
        val metadata = uploadFile(file, context.settings.googleDriveFolderId, client, auth, progress)
        val fileId = metadata.string("id") ?: throw UploadException.Rejected(displayName, "no file id")
        authorized(client, auth) { permissionRequest(fileId, it) }.requireSuccess(displayName)
        val link = metadata.string("webViewLink")?.toHttpUrlOrNull()?.toString()
            ?: "https://drive.google.com/file/d/$fileId/view?usp=sharing"
        return result(file, link, context)
    }

    private suspend fun authorized(client: OkHttpClient, auth: suspend (Boolean) -> String, request: (String) -> Request): HttpReply {
        coroutineContext.ensureActive()
        val reply = client.send(request(auth(false)))
        return if (reply.status == 401) client.send(request(auth(true))) else reply
    }

    private suspend fun uploadFile(file: File, folderId: String?, client: OkHttpClient, auth: suspend (Boolean) -> String, progress: ((UploadProgress) -> Unit)?): JsonObject {
        val size = file.length()
        check(file)
        val started = authorized(client, auth) { startRequest(file, size, folderId, it) }
        started.requireSuccess(displayName)
        val session = started.header("Location")?.toHttpUrlOrNull()
            ?: throw UploadException.Rejected(displayName, "no upload session")

        val metadata = sendChunks(file, size, session, client, auth, progress)
        if (metadata.string("id") == null) throw UploadException.Rejected(displayName, "no file id")
        return metadata
    }

    private suspend fun uploadFolder(file: File, context: UploadContext, client: OkHttpClient, auth: suspend (Boolean) -> String, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val root = file.absoluteFile
        val operationContext = coroutineContext
        val entries = root.walkTopDown().onEnter { directory ->
            operationContext.ensureActive()
            if (Files.isSymbolicLink(directory.toPath())) throw UploadException.Rejected(displayName, "folder contains a symbolic link")
            true
        }.onFail { _, error -> throw error }.toList()
        for (entry in entries) {
            coroutineContext.ensureActive()
            if (Files.isSymbolicLink(entry.toPath())) throw UploadException.Rejected(displayName, "folder contains a symbolic link")
            if (entry.isFile) check(entry)
        }
        val total = entries.filter { it.isFile }.sumOf { it.length() }
        val folders = mutableMapOf<File, String>()
        var sent = 0L
        try {
            for (entry in entries) {
                val parent = if (entry == root) context.settings.googleDriveFolderId else folders.getValue(entry.parentFile)
                if (entry.isDirectory) {
                    val metadata = buildJsonObject {
                        put("name", uploadName(entry))
                        put("mimeType", "application/vnd.google-apps.folder")
                        if (!parent.isNullOrBlank()) put("parents", JsonArray(listOf(JsonPrimitive(parent))))
                    }
                    val reply = authorized(client, auth) { metadataRequest(null, metadata, it) }
                    reply.requireSuccess(displayName)
                    folders[entry] = reply.jsonObject()?.string("id") ?: throw UploadException.Rejected(displayName, "no folder id")
                } else {
                    uploadFile(entry, parent, client, auth) { value -> progress?.invoke(UploadProgress(sent + value.sentBytes, total)) }
                    sent += entry.length()
                }
            }
            authorized(client, auth) { permissionRequest(folders.getValue(root), it) }.requireSuccess(displayName)
        } catch (error: Exception) {
            folders[root]?.let { id ->
                withContext(NonCancellable) {
                    runCatching {
                        authorized(client, auth) { metadataRequest(id, buildJsonObject { put("trashed", true) }, it) }
                    }
                }
            }
            throw error
        }
        progress?.invoke(UploadProgress(total, total))
        return result(file, "https://drive.google.com/drive/folders/${folders.getValue(root)}?usp=sharing", context)
    }

    /**
     * Sends the file in `chunkSize` pieces, holding one chunk in memory at a time. Network errors
     * and 5xx replies resume from the offset Drive reports.
     */
    private suspend fun sendChunks(
        file: File,
        size: Long,
        session: HttpUrl,
        client: OkHttpClient,
        auth: suspend (Boolean) -> String,
        progress: ((UploadProgress) -> Unit)?,
    ): JsonObject {
        RandomAccessFile(file, "r").use { reader ->
            var offset = 0L
            var failures = 0
            while (true) {
                coroutineContext.ensureActive()
                val chunk = ByteArray(minOf(chunkSize.toLong(), size - offset).toInt())
                reader.seek(offset)
                reader.readFully(chunk)

                val reply = try {
                    val token = auth(false)
                    client.send(chunkRequest(session, offset, chunk, size, token, progress))
                } catch (_: IOException) {
                    null
                }

                if (reply != null) {
                    when (reply.status) {
                        200, 201 -> {
                            progress?.invoke(UploadProgress(size, size))
                            return reply.jsonObject() ?: JsonObject(emptyMap())
                        }
                        308 -> {
                            offset = nextOffset(reply)
                            failures = 0
                            continue
                        }
                        401 -> auth(true)
                        404, 410 -> throw UploadException.Rejected(displayName, "upload session expired")
                        in 500..599 -> Unit
                        else -> throw UploadException.BadResponse(displayName, reply.status, reply.snippet)
                    }
                }

                failures += 1
                if (failures > maxChunkRetries) throw UploadException.Rejected(displayName, "too many interrupted chunks")
                delay(retryDelayMillis(failures))
                val status = try {
                    client.send(statusRequest(session, size, auth(false)))
                } catch (_: IOException) {
                    continue
                }
                when (status.status) {
                    200, 201 -> return status.jsonObject() ?: JsonObject(emptyMap())
                    308 -> offset = nextOffset(status)
                    404, 410 -> throw UploadException.Rejected(displayName, "upload session expired")
                }
            }
        }
    }

    companion object {
        val UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files".toHttpUrl()
        val FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files".toHttpUrl()
        private val JSON_TYPE = "application/json; charset=UTF-8".toMediaType()

        private fun metadataRequest(fileId: String?, metadata: JsonObject, token: String): Request {
            val url = FILES_ENDPOINT.newBuilder().apply { if (fileId != null) addPathSegment(fileId) }
                .addQueryParameter("supportsAllDrives", "true").addQueryParameter("fields", "id").build()
            return Request.Builder().url(url).header("Authorization", "Bearer $token")
                .method(if (fileId == null) "POST" else "PATCH", metadata.toString().toRequestBody(JSON_TYPE)).build()
        }

        internal fun startRequest(file: File, size: Long, folderId: String?, token: String): Request {
            val url = UPLOAD_ENDPOINT.newBuilder()
                .addQueryParameter("uploadType", "resumable")
                .addQueryParameter("supportsAllDrives", "true")
                .addQueryParameter("fields", "id,name,webViewLink")
                .build()
            val metadata = buildJsonObject {
                put("name", uploadName(file))
                val folder = folderId?.trim()
                if (!folder.isNullOrEmpty()) put("parents", JsonArray(listOf(JsonPrimitive(folder))))
            }
            return Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $token")
                .header("X-Upload-Content-Type", mimeType(file))
                .header("X-Upload-Content-Length", size.toString())
                .post(metadata.toString().toByteArray(Charsets.UTF_8).toRequestBody(JSON_TYPE))
                .build()
        }

        internal fun chunkRequest(
            session: HttpUrl,
            offset: Long,
            chunk: ByteArray,
            total: Long,
            token: String,
            progress: ((UploadProgress) -> Unit)? = null,
        ): Request = Request.Builder()
            .url(session)
            .header("Authorization", "Bearer $token")
            .apply { if (total > 0) header("Content-Range", "bytes $offset-${offset + chunk.size - 1}/$total") }
            .put(StreamingBody(null, listOf(StreamingBody.Part.Bytes(chunk)), base = offset, total = total, progress = progress))
            .build()

        /** Asks how much of an interrupted session Drive already has. */
        internal fun statusRequest(session: HttpUrl, total: Long, token: String): Request =
            Request.Builder()
                .url(session)
                .header("Authorization", "Bearer $token")
                .header("Content-Range", "bytes */$total")
                .put(ByteArray(0).toRequestBody(null))
                .build()

        internal fun permissionRequest(fileId: String, token: String): Request {
            val url = FILES_ENDPOINT.newBuilder()
                .addPathSegment(fileId)
                .addPathSegment("permissions")
                .addQueryParameter("supportsAllDrives", "true")
                .build()
            return Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $token")
                .post("""{"role":"reader","type":"anyone"}""".toByteArray(Charsets.UTF_8).toRequestBody(JSON_TYPE))
                .build()
        }

        /** Next byte Drive expects after a 308 reply (`Range: bytes=0-N`), or 0 when it has none. */
        internal fun nextOffset(reply: HttpReply): Long =
            reply.header("Range")?.substringAfterLast('-')?.trim()?.toLongOrNull()?.plus(1) ?: 0

        internal fun mimeType(file: File): String = when (file.extension.lowercase()) {
            "epub" -> "application/epub+zip"
            "pdf" -> "application/pdf"
            "txt" -> "text/plain"
            "html", "htm" -> "text/html"
            "zip" -> "application/zip"
            else -> "application/octet-stream"
        }
    }
}
