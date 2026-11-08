package com.shirochi.npia.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.output.BookBundle
import com.shirochi.npia.output.FolderWriter
import com.shirochi.npia.output.Writers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

@RunWith(AndroidJUnit4::class)
class ChapterExportTest {
    @Test
    fun mangaExportsOriginalsAndWebtoonStitchesWithinTheConfiguredLimit() = runBlocking {
        val cache = InstrumentationRegistry.getInstrumentation().targetContext.cacheDir
        val directory = Files.createTempDirectory(cache.toPath(), "chapter-export-test-").toFile()
        try {
            val panels = listOf(panel(Color.RED, 24), panel(Color.BLUE, 12), panel(Color.GREEN, 24))
            val chapter = ChapterRef("episode", 8, "Episode")
            val pages = panels.indices.map { ImageRef("https://example.test/$it.png", "$it.png") }
            val book = BookInfo(SourceId.READER, "book", "https://example.test/book", "Book")
            val settings = AppSettings()
            fun bundle(options: AppSettings) = BookBundle(
                book, listOf(ChapterContent(chapter, "", pages)),
                pages.mapIndexed { index, page -> page.filename to panels[index] }.toMap(), settings = options,
            )
            val folder = Writers.writer(OutputFormat.FOLDER).write(bundle(settings), "Book", directory)
            val files = folder.listFiles()!!.sortedBy { it.name }
            assertEquals((1..3).map { "0008 Episode - 00$it.png" }, files.map { it.name })
            files.forEachIndexed { index, file -> assertArrayEquals(panels[index], file.readBytes()) }

            val webtoon = settings.copy(images = settings.images.copy(
                webtoonExport = true, stitchHeight = 2_001, compress = false,
            ))
            val archive = Writers.writer(OutputFormat.ZIP).write(bundle(webtoon), "Book", directory)
            ZipFile(archive).use { zip ->
                assertEquals(listOf("0008 Episode/001.png", "0008 Episode/002.png"), zip.entries().toList().map { it.name })
                assertEquals(ZipEntry.STORED, zip.getEntry("0008 Episode/001.png").method)
                val strip = zip.getInputStream(zip.getEntry("0008 Episode/001.png")).use { BitmapFactory.decodeStream(it) }
                try {
                    assertEquals(2_000, strip.height)
                    assertEquals(24, strip.width)
                    assertEquals(Color.RED, strip.getPixel(12, 500))
                    assertEquals(Color.BLUE, strip.getPixel(12, 1_500))
                    assertEquals(Color.WHITE, strip.getPixel(0, 1_500))
                } finally {
                    strip.recycle()
                }
                val last = zip.getInputStream(zip.getEntry("0008 Episode/002.png")).use { it.readBytes() }
                assertArrayEquals(panels.last(), last)
            }

            val fullPage = panel(Color.MAGENTA, 180)
            val longPages = List(167) { ImageRef("https://example.test/full.png", "full.png") }
            val full = BookBundle(
                book, listOf(ChapterContent(chapter, "", longPages)), mapOf("full.png" to fullPage),
                settings = webtoon.copy(images = webtoon.images.copy(stitchHeight = 0, stitchChapterFolders = true)),
            )
            val fullFolder = Writers.writer(OutputFormat.FOLDER).write(full, "Unlimited", directory)
            val strip = fullFolder.resolve("0008 Episode").listFiles()!!.single()
            assertEquals("001.png", strip.name)
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(strip.absolutePath, bounds)
            assertEquals(180, bounds.outWidth)
            assertEquals(167_000, bounds.outHeight)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun unlimitedWebpEncodesFinishedStripsWithinTheCodecLimit() = runBlocking {
        val cache = InstrumentationRegistry.getInstrumentation().targetContext.cacheDir
        val directory = Files.createTempDirectory(cache.toPath(), "chapter-webp-test-").toFile()
        try {
            val options = AppSettings().images.copy(
                webtoonExport = true, stitchHeight = 0, compress = true, format = ImageFormat.WEBP,
            )
            val refs = List(18) { ImageRef("https://example.test/full.png", "full.png") }
            val chapter = ChapterContent(ChapterRef("episode", 1, "Episode"), "", refs)
            FolderWriter.Prepared(directory, options, AndroidImageProcessor(options)).use { prepared ->
                prepared.append(chapter, mapOf("full.png" to panel(Color.RED, 24)))
                val folder = prepared.publish(OutputFormat.FOLDER, "Book")
                val files = folder.resolve("0001 Episode").listFiles()!!.sortedBy { it.name }
                assertEquals(listOf("001.webp", "002.webp"), files.map { it.name })
                assertEquals(listOf(16_000, 2_000), files.map { file ->
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeFile(file.absolutePath, bounds)
                    assertEquals("image/webp", bounds.outMimeType)
                    bounds.outHeight
                })
            }
        } finally {
            directory.deleteRecursively()
        }
    }

    private fun panel(color: Int, width: Int): ByteArray {
        val bitmap = Bitmap.createBitmap(width, 1_000, Bitmap.Config.ARGB_8888)
        try {
            bitmap.eraseColor(color)
            return ByteArrayOutputStream().use {
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
                it.toByteArray()
            }
        } finally {
            bitmap.recycle()
        }
    }
}
