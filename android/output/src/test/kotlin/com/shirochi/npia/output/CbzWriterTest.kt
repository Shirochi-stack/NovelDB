package com.shirochi.npia.output

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

class CbzWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test
    fun laysOutCoverInfoAndChapterImagesLikeDesktop() = runBlocking {
        val file = CbzWriter().write(makeWriterBundle(numbered = false), "book", tmp.root)
        ZipFile(file).use { zip ->
            val names = zip.entries().toList().map { it.name }
            assertEquals(listOf("0000_cover.jpg", "ComicInfo.xml", "0002_Pictures/0001.png"), names)
            assertEquals(ZipEntry.STORED, zip.getEntry("0002_Pictures/0001.png").method)
            val info = zip.getInputStream(zip.getEntry("ComicInfo.xml")).use { it.readBytes().decodeToString() }
            assertTrue(info.contains("<Title>Tower &amp; Me</Title>"))
            assertTrue(info.contains("<Writer>Writer &lt;Kim&gt;</Writer>"))
        }
    }

    @Test(expected = OutputSkipped::class)
    fun skipsBooksWithoutImages() {
        val bundle = makeWriterBundle()
        runBlocking { CbzWriter().write(BookBundle(bundle.book, bundle.chapters, emptyMap(), null, bundle.settings), "book", tmp.root) }
    }
}
