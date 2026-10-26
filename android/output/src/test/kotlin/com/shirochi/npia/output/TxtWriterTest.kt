package com.shirochi.npia.output

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.NamingMode
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceId
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class TxtWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun bundle(settings: AppSettings = AppSettings()) = BookBundle(
        book = BookInfo(SourceId.READER, "42", "https://example.com/42", "My: Book", author = "Writer"),
        chapters = listOf(
            ChapterContent(ChapterRef("1", 1, "Start"), "<p>Hello &amp; welcome</p><p>Line two</p>"),
            ChapterContent(ChapterRef("2", 2, "Next"), "<div>Second<br>chapter</div><script>x()</script>"),
        ),
        settings = settings,
    )

    @Test
    fun singleFile() = runTest {
        val b = bundle(AppSettings(shared = SharedSettings(numberChapters = true)))
        val name = Writers.baseName(b.book, b.settings.shared)
        assertEquals("My_ Book", name)

        val text = TxtWriter().write(b, name, tmp.root).readText()
        assertTrue(text.startsWith("My: Book\nWriter\n"))
        assertTrue(text.contains("1. Start\n\nHello & welcome\nLine two"))
        assertTrue(text.contains("2. Next\n\nSecond\nchapter"))
        assertFalse(text.contains("x()"))
    }

    @Test
    fun separateFiles() = runTest {
        val folder = TxtWriter().write(bundle(AppSettings(shared = SharedSettings(separateTxt = true))), "book", tmp.root)
        assertEquals(listOf("0001.txt", "0002.txt"), folder.list()!!.sorted())
    }

    @Test
    fun baseNameAppendsRange() {
        val book = BookInfo(SourceId.NPIA, "7", "https://example.com", "Title")
        assertEquals("Title (3-end)", Writers.baseName(book, SharedSettings(appendRange = true, rangeFrom = 3)))
        assertEquals("7 (3-end)", Writers.baseName(book, SharedSettings(appendRange = true, rangeFrom = 3, namingMode = NamingMode.ID)))
    }
}
