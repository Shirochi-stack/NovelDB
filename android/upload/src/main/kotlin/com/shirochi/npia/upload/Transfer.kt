package com.shirochi.npia.upload

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.MediaType
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response
import okio.BufferedSink
import java.io.EOFException
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.text.Normalizer
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Status, headers, and body of one finished request. */
internal class HttpReply(val status: Int, headers: Map<String, String>, val body: ByteArray) {
    private val headers = headers.mapKeys { it.key.lowercase() }

    fun header(name: String): String? = headers[name.lowercase()]

    val text: String get() = body.toString(Charsets.UTF_8).trim()

    val isSuccess: Boolean get() = status in 200..299

    fun json(): JsonElement? = runCatching { Json.parseToJsonElement(body.toString(Charsets.UTF_8)) }.getOrNull()

    fun jsonObject(): JsonObject? = json() as? JsonObject

    /** Short body excerpt for error messages. */
    val snippet: String get() = text.take(200)

    fun requireSuccess(host: String) {
        if (!isSuccess) throw UploadException.BadResponse(host, status, snippet)
    }
}

internal fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

internal fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

internal fun JsonObject.array(key: String): JsonArray? = this[key] as? JsonArray

/** Runs `request` on OkHttp's dispatcher; cancelling the coroutine cancels the call. */
internal suspend fun OkHttpClient.send(request: Request): HttpReply = suspendCancellableCoroutine { cont ->
    val call = newCall(request)
    cont.invokeOnCancellation { call.cancel() }
    call.enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (cont.isActive) cont.resumeWithException(e)
        }

        override fun onResponse(call: Call, response: Response) {
            val reply = try {
                response.use { HttpReply(it.code, it.headers.associate { (k, v) -> k to v }, it.body?.bytes() ?: ByteArray(0)) }
            } catch (e: IOException) {
                if (cont.isActive) cont.resumeWithException(e)
                return
            }
            cont.resume(reply)
        }
    })
}

/**
 * Request body made of byte blocks and file ranges, streamed from disk in 64 KiB pieces and never
 * loaded whole. `base` offsets the reported progress for chunked uploads.
 */
internal class StreamingBody(
    private val contentType: MediaType?,
    private val parts: List<Part>,
    private val base: Long = 0,
    private val total: Long = parts.sumOf { it.length },
    private val progress: ((UploadProgress) -> Unit)? = null,
) : RequestBody() {
    sealed class Part {
        abstract val length: Long

        class Bytes(val data: ByteArray) : Part() {
            override val length: Long get() = data.size.toLong()
        }

        class FileRange(val file: File, val offset: Long = 0, override val length: Long = file.length()) : Part()
    }

    override fun contentType(): MediaType? = contentType

    override fun contentLength(): Long = parts.sumOf { it.length }

    override fun writeTo(sink: BufferedSink) {
        var sent = 0L
        fun report() {
            progress?.invoke(UploadProgress(minOf(base + sent, total), total))
        }
        val buffer = ByteArray(64 * 1024)
        for (part in parts) {
            when (part) {
                is Part.Bytes -> {
                    sink.write(part.data)
                    sent += part.data.size
                    report()
                }
                is Part.FileRange -> RandomAccessFile(part.file, "r").use { reader ->
                    reader.seek(part.offset)
                    var remaining = part.length
                    while (remaining > 0) {
                        val count = reader.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                        if (count < 0) throw EOFException("${part.file.name} shrank during upload")
                        sink.write(buffer, 0, count)
                        remaining -= count
                        sent += count
                        report()
                    }
                }
            }
        }
    }
}

/** `multipart/form-data` with text fields followed by one file part. */
internal class MultipartForm(
    val fileField: String,
    val file: File,
    val name: UploadName = UploadName.of(file),
    val fields: List<Pair<String, String>> = emptyList(),
    val boundary: String = "NpiaBoundary-${UUID.randomUUID()}",
) {
    val mimeType = "application/octet-stream"

    val contentType: String get() = "multipart/form-data; boundary=$boundary"

    val head: ByteArray
        get() {
            val text = StringBuilder()
            for ((name, value) in fields) {
                text.append("--$boundary\r\n")
                text.append("Content-Disposition: form-data; name=\"${quoted(name)}\"\r\n\r\n")
                text.append("$value\r\n")
            }
            text.append("--$boundary\r\n")
            // Only the ASCII name goes on the wire. An RFC 5987 `filename*` with the real title
            // overrides it on hosts that read it, and they then strip the Korean down to `-_.epub`.
            text.append("Content-Disposition: form-data; name=\"${quoted(fileField)}\"; filename=\"${quoted(name.ascii)}\"")
            text.append("\r\n")
            text.append("Content-Type: $mimeType\r\n\r\n")
            return text.toString().toByteArray(Charsets.UTF_8)
        }

    val tail: ByteArray get() = "\r\n--$boundary--\r\n".toByteArray(Charsets.UTF_8)

    fun body(progress: ((UploadProgress) -> Unit)? = null): StreamingBody =
        StreamingBody(
            contentType.toMediaType(),
            listOf(StreamingBody.Part.Bytes(head), StreamingBody.Part.FileRange(file), StreamingBody.Part.Bytes(tail)),
            progress = progress,
        )

    companion object {
        fun quoted(value: String): String = value.replace("\r", "").replace("\n", "").replace("\"", "%22")
    }
}

/** Streams the multipart body to `url` and reports the finished upload. */
internal suspend fun UploadContext.postMultipart(
    url: HttpUrl,
    form: MultipartForm,
    headers: Map<String, String> = emptyMap(),
    progress: ((UploadProgress) -> Unit)?,
): HttpReply {
    val body = form.body(progress)
    val request = Request.Builder().url(url).post(body).apply {
        for ((key, value) in headers) header(key, value)
    }.build()
    val reply = client.send(request)
    val size = body.contentLength()
    progress?.invoke(UploadProgress(size, size))
    return reply
}

/**
 * The names one upload goes by. `display` is the real (NFC) file name, shown in the Links list.
 * Hosts strip non-ASCII from multipart file names, so `ascii` is what goes in `filename="..."` and
 * in URLs: the display name itself when it is plain printable ASCII, otherwise the ASCII-safe
 * title when it keeps three ASCII letters, otherwise `<stem>.<extension>` (see `UploadNames`).
 */
internal data class UploadName(val display: String, val ascii: String) {
    val isPlain: Boolean get() = display == ascii

    companion object {
        fun of(file: File, stem: String? = null): UploadName = of(uploadName(file), stem)

        fun of(name: String, stem: String?): UploadName = UploadName(name, UploadNames.ascii(name, stem))
    }
}

/** The names for `file` in this upload; `fileId` supplies the stem for names that are not plain ASCII. */
internal fun UploadContext.nameFor(file: File): UploadName = UploadName.of(file, fileId)

/**
 * The file's name in NFC. File systems and pickers can hand back decomposed Hangul and accents,
 * which hosts would otherwise show as broken names.
 */
internal fun uploadName(file: File): String = Normalizer.normalize(file.name, Normalizer.Form.NFC)

private const val UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
private const val HEX = "0123456789ABCDEF"

/** Percent-encodes the UTF-8 bytes of `value`, keeping unreserved characters and `extra`. */
internal fun percentEncode(value: String, extra: String = ""): String {
    val out = StringBuilder()
    for (byte in value.toByteArray(Charsets.UTF_8)) {
        val code = byte.toInt() and 0xff
        val char = code.toChar()
        if (code < 0x80 && (UNRESERVED.indexOf(char) >= 0 || extra.indexOf(char) >= 0)) {
            out.append(char)
        } else {
            out.append('%').append(HEX[code shr 4]).append(HEX[code and 0xf])
        }
    }
    return out.toString()
}

/** `application/x-www-form-urlencoded` body text. */
internal fun formEncoded(pairs: List<Pair<String, String>>): String =
    pairs.joinToString("&") { (key, value) -> "${percentEncode(key)}=${percentEncode(value)}" }
