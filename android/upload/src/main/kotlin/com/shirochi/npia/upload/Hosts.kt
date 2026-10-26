package com.shirochi.npia.upload

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import okhttp3.Credentials
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import java.io.File

private const val DAY = 86_400L

/** Returns the first line of a plain-text reply when it is an http(s) URL. */
private fun plainLink(reply: HttpReply, host: String): HttpUrl {
    reply.requireSuccess(host)
    val line = reply.text.lineSequence().firstOrNull().orEmpty().trim()
    if (!line.startsWith("http")) throw UploadException.Rejected(host, reply.snippet)
    return line.toHttpUrlOrNull() ?: throw UploadException.Rejected(host, reply.snippet)
}

private fun https(url: HttpUrl): HttpUrl = if (url.scheme == "http") url.newBuilder().scheme("https").build() else url

// temp.sh

/** `curl -F file=@x https://temp.sh/upload`; 4 GB, files expire after 3 days. */
class TempShUploader : Uploader {
    override val id = "tempsh"
    override val displayName = "temp.sh"
    override val maxBytes: Long? = 4_000_000_000
    override val retentionSeconds: Long? = 3 * DAY
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val reply = context.postMultipart(ENDPOINT, MultipartForm("file", file), progress = progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val ENDPOINT = "https://temp.sh/upload".toHttpUrl()

        internal fun parse(reply: HttpReply): String = plainLink(reply, "temp.sh").toString()
    }
}

// Gofile

/**
 * Gofile's global upload endpoint (the `getServer` lookup bot.py used is retired). Guest uploads
 * need no token; inactive guest content is removed after about 10 days.
 */
class GofileUploader : Uploader {
    override val id = "gofile"
    override val displayName = "Gofile"
    override val maxBytes: Long? = null
    override val retentionSeconds: Long? = 10 * DAY
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val headers = buildMap {
            val token = context.secrets.get(UploadSecrets.GOFILE_TOKEN)
            if (!token.isNullOrEmpty()) put("Authorization", "Bearer $token")
        }
        val reply = context.postMultipart(ENDPOINT, MultipartForm("file", file), headers, progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val ENDPOINT = "https://upload.gofile.io/uploadfile".toHttpUrl()

        internal fun parse(reply: HttpReply): String {
            val json = reply.jsonObject()
            if (json == null) {
                reply.requireSuccess("Gofile")
                throw UploadException.Rejected("Gofile", reply.snippet)
            }
            val page = json.obj("data")?.string("downloadPage")?.toHttpUrlOrNull()
            if (json.string("status") != "ok" || page == null) {
                throw UploadException.Rejected("Gofile", json.string("status") ?: reply.snippet)
            }
            return page.toString()
        }
    }
}

// Catbox

/** catbox.moe user API; 200 MB per file, kept permanently. */
class CatboxUploader : Uploader {
    override val id = "catbox"
    override val displayName = "Catbox"
    override val maxBytes: Long? = 200_000_000
    override val retentionSeconds: Long? = null
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val fields = buildList {
            add("reqtype" to "fileupload")
            val hash = context.secrets.get(UploadSecrets.CATBOX_USER_HASH)
            if (!hash.isNullOrEmpty()) add("userhash" to hash)
        }
        val reply = context.postMultipart(ENDPOINT, MultipartForm("fileToUpload", file, fields = fields), progress = progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val ENDPOINT = "https://catbox.moe/user/api.php".toHttpUrl()

        internal fun parse(reply: HttpReply): String = plainLink(reply, "Catbox").toString()
    }
}

// Pixeldrain

/**
 * Pixeldrain no longer accepts anonymous uploads: the API key (sent as the Basic-auth password)
 * lives in `SecretStore` under `UploadSecrets.PIXELDRAIN_API_KEY`. The per-file limit depends on
 * the account plan; free files are removed 90 days after the last view.
 */
class PixeldrainUploader : Uploader {
    override val id = "pixeldrain"
    override val displayName = "Pixeldrain"
    override val maxBytes: Long? = null
    override val retentionSeconds: Long? = 90 * DAY
    override val requiresAccount = true

    override fun isReady(context: UploadContext): Boolean = !context.secrets.get(UploadSecrets.PIXELDRAIN_API_KEY).isNullOrEmpty()

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val key = context.secrets.get(UploadSecrets.PIXELDRAIN_API_KEY)
        if (key.isNullOrEmpty()) throw UploadException.NotSignedIn(displayName)
        val reply = context.client.send(request(file, key, progress))
        progress?.invoke(UploadProgress(file.length(), file.length()))
        return result(file, parse(reply), context)
    }

    companion object {
        const val BASE = "https://pixeldrain.com/api/file/"

        /** `PUT /api/file/{name}`, with `/?#;:` escaped in the name. */
        internal fun url(name: String): HttpUrl = "$BASE${percentEncode(name, extra = "!\$&'()*+,=@")}".toHttpUrl()

        internal fun request(file: File, apiKey: String, progress: ((UploadProgress) -> Unit)? = null): Request =
            Request.Builder()
                .url(url(uploadName(file)))
                .header("Authorization", Credentials.basic("", apiKey))
                .put(
                    StreamingBody(
                        "application/octet-stream".toMediaType(),
                        listOf(StreamingBody.Part.FileRange(file)),
                        progress = progress,
                    ),
                )
                .build()

        internal fun parse(reply: HttpReply): String {
            val json = reply.jsonObject()
            val id = json?.string("id")
            if (!reply.isSuccess || id.isNullOrEmpty()) {
                if (reply.status == 401) throw UploadException.NotSignedIn("Pixeldrain")
                val message = json?.string("message") ?: json?.string("value") ?: reply.snippet
                throw UploadException.Rejected("Pixeldrain", message)
            }
            return "https://pixeldrain.com/u/$id"
        }
    }
}

// tmpfiles

/** tmpfiles.org API; 100 MB, `expire` in seconds up to 48 hours (default is one hour). */
class TmpfilesUploader : Uploader {
    override val id = "tmpfiles"
    override val displayName = "tmpfiles"
    override val maxBytes: Long? = 100_000_000
    override val retentionSeconds: Long? = 2 * DAY
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val form = MultipartForm("file", file, fields = listOf("expire" to (2 * DAY).toString()))
        val reply = context.postMultipart(ENDPOINT, form, progress = progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val ENDPOINT = "https://tmpfiles.org/api/v1/upload".toHttpUrl()

        internal fun parse(reply: HttpReply): String {
            reply.requireSuccess("tmpfiles")
            reply.jsonObject()?.obj("data")?.string("url")?.toHttpUrlOrNull()?.let { return https(it).toString() }
            return https(plainLink(reply, "tmpfiles")).toString()
        }
    }
}

// KrakenFiles

/**
 * KrakenFiles anonymous uploads: ask for a server, then post `files[]` to its gallery uploader.
 * 1 GB per file; files with no downloads for 30 days are removed.
 */
class KrakenFilesUploader : Uploader {
    override val id = "krakenfiles"
    override val displayName = "KrakenFiles"
    override val maxBytes: Long? = 1_000_000_000
    override val retentionSeconds: Long? = 30 * DAY
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val lookup = Request.Builder().url(SERVER_LOOKUP).header("Accept", "application/json").build()
        val endpoint = uploadEndpoint(context.client.send(lookup))
        val reply = context.postMultipart(endpoint, MultipartForm("files[]", file), progress = progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val SERVER_LOOKUP = "https://krakenfiles.com/api/server/available".toHttpUrl()
        val SITE = "https://krakenfiles.com".toHttpUrl()

        /** `{"data": {"url": "https://sN.krakenfiles.com/_uploader/upload/..."}}` → `https://sN.krakenfiles.com/_uploader/gallery/upload`. */
        internal fun uploadEndpoint(reply: HttpReply): HttpUrl {
            reply.requireSuccess("KrakenFiles")
            val server = reply.jsonObject()?.obj("data")?.string("url")?.toHttpUrlOrNull()
                ?: throw UploadException.Rejected("KrakenFiles", "no upload server: ${reply.snippet}")
            return "${server.scheme}://${server.host}/_uploader/gallery/upload".toHttpUrl()
        }

        internal fun parse(reply: HttpReply): String {
            reply.requireSuccess("KrakenFiles")
            val entry = reply.jsonObject()?.array("files")?.firstOrNull() as? JsonObject
                ?: throw UploadException.Rejected("KrakenFiles", reply.snippet)
            val error = entry.string("error")
            if (!error.isNullOrEmpty()) throw UploadException.Rejected("KrakenFiles", error)
            val path = entry.string("url") ?: throw UploadException.Rejected("KrakenFiles", reply.snippet)
            return (SITE.resolve(path) ?: throw UploadException.Rejected("KrakenFiles", reply.snippet)).toString()
        }
    }
}

// Send.now (formerly send.cm)

/**
 * send.cm became send.now, an XFileSharing site: the home page names the upload server, which
 * takes `file_0` anonymously. 100 GB per file, kept 15 days after the last download.
 */
class SendNowUploader : Uploader {
    override val id = "sendnow"
    override val displayName = "Send.now"
    override val maxBytes: Long? = 100_000_000_000
    override val retentionSeconds: Long? = 15 * DAY
    override val requiresAccount = false

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val page = Request.Builder().url(HOME)
            .header("User-Agent", "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36")
            .build()
        val endpoint = uploadEndpoint(context.client.send(page))
        val reply = context.postMultipart(endpoint, MultipartForm("file_0", file), progress = progress)
        return result(file, parse(reply), context)
    }

    companion object {
        val HOME = "https://send.now/".toHttpUrl()
        private val serverPatterns = listOf(
            Regex("""https://([A-Za-z0-9-]+)\.send\.now/cgi-bin/upload\.cgi"""),
            Regex("""https://([A-Za-z0-9]{4,})\.send\.now"""),
        )

        internal fun uploadEndpoint(reply: HttpReply): HttpUrl {
            reply.requireSuccess("Send.now")
            val html = reply.body.toString(Charsets.UTF_8)
            for (pattern in serverPatterns) {
                val server = pattern.find(html)?.groupValues?.get(1) ?: continue
                return "https://$server.send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon".toHttpUrl()
            }
            throw UploadException.Rejected("Send.now", "upload server not found on the home page")
        }

        /** `[{"file_code": "abc123", "file_status": "OK"}]` */
        internal fun parse(reply: HttpReply): String {
            reply.requireSuccess("Send.now")
            val entry = (reply.json() as? JsonArray)?.firstOrNull() as? JsonObject
                ?: throw UploadException.Rejected("Send.now", reply.snippet)
            val code = entry.string("file_code")
            val status = entry.string("file_status")
            if (code.isNullOrEmpty() || code == "undef" || (status ?: "OK").uppercase() != "OK") {
                throw UploadException.Rejected("Send.now", status ?: reply.snippet)
            }
            return "https://send.now/$code"
        }
    }
}
