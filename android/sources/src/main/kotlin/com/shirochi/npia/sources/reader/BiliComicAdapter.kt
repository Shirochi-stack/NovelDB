package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.parseConcurrently
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import java.net.URLDecoder
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Bilibili Manga: the desktop's `bilicomic` engine (the site's app API) in the Python engines' page.
 *
 * The engine returns a chapter's page addresses, which the site signs for about fifteen minutes. The
 * image step runs after every chapter is read, far later than that, so a download fetches a chapter's
 * pages here, as soon as the chapter is read, and hands the bytes over (`SourceContext.keep` moves them
 * to disk). Each page is reported as it arrives (`SourceContext.onImage`), so the log shows the pages
 * coming in between the chapter lines; the other chapters' addresses are read meanwhile.
 * The Catalog reader shows a chapter at once and takes the addresses (`parseChapter`).
 *
 * A chapter the signed-in account has not bought is refused; nothing is bought.
 */
internal class BiliComicAdapter : SourceAdapter {
    override val id = SourceId.BILICOMIC
    override val tier = SourceTier.LIGHT_BROWSER
    override val sites = setOf("manga.bilibili.com")

    /** One chapter's pages in memory at a time (a chapter is tens of megabytes). */
    private val pages = Mutex()

    override fun matches(url: String): Boolean = PythonEngines.biliComicMatches(url)

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo =
        ReaderSessions.openBuiltIn(url, context, id).book

    /** The chapter with its page addresses only. */
    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        ReaderSessions.session(book, context).chapter(ref)

    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> =
        parseConcurrently(refs, context, spaced = true) { ref -> withPages(parseChapter(ref, book, context), context) }

    /** `result` with every page's bytes, kept; `Failed` when a page cannot be fetched (the chapter is read again). */
    internal suspend fun withPages(result: ChapterResult, context: SourceContext): ChapterResult {
        if (result !is ChapterResult.Content || result.content.images.none { it.data == null }) return result
        return pages.withLock {
            val images = result.content.images
            val limit = Semaphore(context.shared(id).imageWorkers.coerceIn(1, 8))
            val fetched = coroutineScope {
                images.mapIndexed { index, image ->
                    async {
                        image.data ?: limit.withPermit {
                            val began = System.nanoTime()
                            // Reported as it arrives: the log shows the pages coming in while the chapters are read.
                            page(image.url, context)?.also {
                                context.onImage(result.ref, index, image.filename, it.size, (System.nanoTime() - began) / 1e9)
                            }
                        }
                    }
                }.awaitAll()
            }
            val missing = fetched.indexOfFirst { it == null }
            if (missing >= 0) {
                ChapterResult.Failed(result.ref, "Page ${missing + 1} of ${images.size} could not be downloaded.")
            } else {
                context.keep(ChapterResult.Content(result.content.copy(
                    images = images.mapIndexed { index, image -> image.copy(data = fetched[index]) },
                )))
            }
        }
    }

    /** One page's image bytes, or null: two tries (the image host needs no sign-in). */
    private suspend fun page(url: String, context: SourceContext): ByteArray? {
        repeat(2) { attempt ->
            val body = try {
                val response = context.http.send(HttpRequest(url, headers = mapOf("Referer" to "https://manga.bilibili.com/")))
                response.body.takeIf { response.status in 200..299 && it.isNotEmpty() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            body?.let { runCatching { BiliComicPages.decode(it, url) }.getOrNull() }?.let { return it }
            if (attempt == 0) delay(1000)
        }
        return null
    }
}

/** A page as the site stores it: protected pages ("cpx" in the address) are decoded (the desktop's `decrypt_page`). */
internal object BiliComicPages {
    /** The encrypted start of a protected page; the rest is stored as it is. */
    private const val HEAD = 20496

    fun isImage(data: ByteArray): Boolean {
        fun starts(vararg bytes: Int, at: Int = 0) =
            data.size >= at + bytes.size && bytes.indices.all { data[at + it] == bytes[it].toByte() }
        return starts(0xFF, 0xD8, 0xFF) || starts(0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) ||
            (starts(0x52, 0x49, 0x46, 0x46) && starts(0x57, 0x45, 0x42, 0x50, at = 8)) ||
            starts(0x47, 0x49, 0x46, 0x38) || starts(0x66, 0x74, 0x79, 0x70, 0x61, 0x76, 0x69, at = 4)
    }

    /** The image in `data`. Throws when the bytes are neither an image nor a protected page that decodes to one. */
    fun decode(data: ByteArray, url: String): ByteArray {
        if (isImage(data)) return data
        val cpx = requireNotNull(cpx(url)) { "the page is not an image" }
        // 0x01, the image's length (4 bytes, big-endian), the image with its first 20496 bytes AES-CBC
        // encrypted, then the key. The IV is bytes 60-76 of the decoded "cpx" value.
        require(data.size >= 5 && data[0] == 1.toByte()) { "a protected page has an unknown layout" }
        val length = ((data[1].toInt() and 0xFF) shl 24) or ((data[2].toInt() and 0xFF) shl 16) or
            ((data[3].toInt() and 0xFF) shl 8) or (data[4].toInt() and 0xFF)
        val end = 5 + length
        require(length > 0 && end <= data.size) { "a protected page has an invalid length" }
        val key = data.copyOfRange(end, data.size)
        val head = minOf(HEAD, length)
        require(cpx.size >= 76 && key.size in setOf(16, 24, 32) && head % 16 == 0) { "a protected page has an invalid key" }
        val cipher = Cipher.getInstance("AES/CBC/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(cpx.copyOfRange(60, 76)))
        var plain = cipher.doFinal(data, 5, head)
        val pad = plain.last().toInt() and 0xFF
        if (pad in 1..16 && (plain.size - pad until plain.size).all { plain[it] == pad.toByte() }) {
            plain = plain.copyOf(plain.size - pad)
        }
        val image = plain + data.copyOfRange(5 + head, end)
        require(isImage(image)) { "a protected page did not decode to an image" }
        return image
    }

    /** The decoded "cpx" value of a page address, or null when it has none. */
    private fun cpx(url: String): ByteArray? {
        val raw = url.substringAfter('?', "").substringBefore('#').split('&')
            .firstOrNull { it.startsWith("cpx=") }?.substring(4)?.takeIf { it.isNotEmpty() } ?: return null
        val text = runCatching { URLDecoder.decode(raw, "UTF-8") }.getOrDefault(raw)
            .filter { it in 'A'..'Z' || it in 'a'..'z' || it in '0'..'9' || it == '+' || it == '/' }
        return runCatching { Base64.getDecoder().decode(text + "=".repeat((4 - text.length % 4) % 4)) }.getOrNull()
    }
}
