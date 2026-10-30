package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.sources.sfc.SfcAdapter
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.util.zip.ZipFile

/**
 * An sfc book of picture chapters (each bought VIP chapter is one image), larger than the test JVM's
 * 256 MB heap (pipeline/build.gradle.kts), the same limit Android gave the app when it ran out of
 * memory at chapter 557 of 1571. The pictures must wait on disk, not in memory.
 */
class DownloadPipelineMemoryTest {
    private val host = SfcAdapter.BOOK_HOST
    private val chapters = 1000
    private val pictureBytes = 300 * 1024

    private inner class PictureBook : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse {
            val url = request.url
            val body: ByteArray = when {
                url.endsWith("/Novel/12345/MainIndex/") -> index().encodeToByteArray()
                url.endsWith("/Novel/12345/") -> "<html><body><span class=\"author-name\">A</span></body></html>".encodeToByteArray()
                "common.ashx" in url -> picture(url.substringAfterLast('=').toInt())
                "/Novel/12345/600/" in url -> {
                    val id = url.trimEnd('/').substringAfterLast('/')
                    "<div class=\"article-content\"><img id=\"vipImage\" src=\"https://$host/ajax/ashx/common.ashx?cid=$id\"></div>".encodeToByteArray()
                }
                else -> return HttpResponse(url, 404, emptyMap(), ByteArray(0))
            }
            return HttpResponse(url, 200, emptyMap(), body)
        }

        private fun index() = buildString {
            append("<html><body><h1 class=\"story-title\">Picture book</h1><div class=\"story-catalog\"><div class=\"catalog-list\"><ul>")
            for (i in 1..chapters) append("<li><a href=\"/Novel/12345/600/${7000 + i}/\" title=\"Ch $i\"><span class=\"icn_vip\"></span>Ch $i</a></li>")
            append("</ul></div></div></body></html>")
        }

        /** A GIF header and bytes that differ per chapter, so nothing is shared between pictures. */
        private fun picture(id: Int) = ByteArray(pictureBytes) { i -> ((i * 31 + id) and 0xFF).toByte() }.also {
            "GIF89a".encodeToByteArray().copyInto(it)
        }
    }

    @Test
    fun aBookOfPictureChaptersLargerThanTheHeapIsWritten() = runBlocking {
        val heap = Runtime.getRuntime().maxMemory()
        assertTrue("the test needs a heap smaller than the book (${heap / 1_048_576} MB)", heap < chapters.toLong() * pictureBytes)
        val output = Files.createTempDirectory("memory-out").toFile()
        try {
            val settings = AppSettings(
                shared = SharedSettings(threads = 8, intervalMin = 0.0, intervalMax = 0.0, formats = listOf(OutputFormat.EPUB)),
            )
            val outcome = DownloadPipeline(InMemorySecretStore(), output, httpFactory = { PictureBook() })
                .run("https://$host/Novel/12345/", settings)
            assertEquals(emptyList<Any>(), outcome.skipped)
            val epub = outcome.files.single()
            ZipFile(epub).use { zip ->
                val pictures = zip.entries().toList().filter { it.name.startsWith("OEBPS/images/") }
                assertEquals(chapters, pictures.size)
                assertTrue(pictures.all { it.size == pictureBytes.toLong() })
            }
        } finally {
            output.deleteRecursively()
        }
    }
}
