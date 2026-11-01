package com.shirochi.npia.upload

import com.shirochi.npia.core.SecretKey
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
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
        context.googleOAuth != null && !context.secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN).isNullOrEmpty()

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val config = context.googleOAuth ?: throw UploadException.NotConfigured("Google Drive client ID")
        // A 308 from Drive means "resume incomplete", never a redirect to follow.
        val client = context.client.newBuilder().followRedirects(false).build()
        val auth = GoogleDriveAuth(config, context.secrets, client, context.now)
        val size = file.length()
        val folderId = context.settings.googleDriveFolderId

        var token = auth.accessToken()
        var started = client.send(startRequest(file, size, folderId, token))
        if (started.status == 401) {
            token = auth.accessToken(forceRefresh = true)
            started = client.send(startRequest(file, size, folderId, token))
        }
        started.requireSuccess(displayName)
        val session = started.header("Location")?.toHttpUrlOrNull()
            ?: throw UploadException.Rejected(displayName, "no upload session")

        val metadata = sendChunks(file, size, session, client, auth, progress)
        val fileId = metadata.string("id") ?: throw UploadException.Rejected(displayName, "no file id")

        token = auth.accessToken()
        client.send(permissionRequest(fileId, token)).requireSuccess(displayName)

        val link = metadata.string("webViewLink")?.toHttpUrlOrNull()?.toString()
            ?: "https://drive.google.com/file/d/$fileId/view?usp=sharing"
        return result(file, link, context)
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
        auth: GoogleDriveAuth,
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
                    val token = auth.accessToken()
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
                        401 -> auth.accessToken(forceRefresh = true)
                        404, 410 -> throw UploadException.Rejected(displayName, "upload session expired")
                        in 500..599 -> Unit
                        else -> throw UploadException.BadResponse(displayName, reply.status, reply.snippet)
                    }
                }

                failures += 1
                if (failures > maxChunkRetries) throw UploadException.Rejected(displayName, "too many interrupted chunks")
                delay(retryDelayMillis(failures))
                val status = try {
                    client.send(statusRequest(session, size, auth.accessToken()))
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
