package com.shirochi.npia.sources.http

import com.shirochi.npia.core.ChapterRef
import org.junit.Assert.assertEquals
import org.junit.Test

class ReaderBlocksTest {
    @Test
    fun htmlBlocksSplitParagraphsBreaksNewlinesAndImages() {
        val html = """
            <div><p>First&nbsp;line<br>second line</p>
            <script>var x = 1;</script>
            <p>pre-line one
            pre-line two</p>
            <img data-src="/img/a.png" src="blank.gif"><span>tail <b>bold</b></span>
            <img src="file:///C:/local.png"><button>Like</button></div>
        """.trimIndent()
        val blocks = ReaderBlocks.fromHtml(html, "https://example.com/read/1")
        assertEquals(
            listOf(
                ReaderBlock.Text("First line"), ReaderBlock.Text("second line"),
                ReaderBlock.Text("pre-line one"), ReaderBlock.Text("pre-line two"),
                ReaderBlock.Image("https://example.com/img/a.png"),
                ReaderBlock.Text("tail bold"),
            ),
            blocks,
        )
    }

    @Test
    fun plainBlocksDropBlankLines() {
        assertEquals(
            listOf(ReaderBlock.Text("a"), ReaderBlock.Text("b")),
            ReaderBlocks.fromPlainText("  a \r\n\r\n${Char(0xa0)}b\n \n"),
        )
    }

    @Test
    fun contentDropsRepeatedTitleAndNamesImages() {
        val ref = ChapterRef("1", 3, "제 1 화")
        val image = "https://cdn.example.com/x/pic.PNG?type=w800"
        val content = ReaderBlocks.content(
            listOf(ReaderBlock.Text("제1화"), ReaderBlock.Text("A < B"), ReaderBlock.Image(image), ReaderBlock.Image(image)),
            ref,
            "c",
        )
        assertEquals(listOf("images/0003_01.png"), content.images.map { it.filename })
        assertEquals(
            """
            <div class="c">
            <p>A &lt; B</p>
            <p class="img"><img src="images/0003_01.png" alt=""/></p>
            <p class="img"><img src="images/0003_01.png" alt=""/></p>
            </div>
            """.trimIndent(),
            content.html,
        )
    }

    @Test
    fun unescapeHtml() {
        assertEquals("a &lt; 한글 \"q\"", HttpSourceSupport.unescapeHtml("a &amp;lt; &#54620;&#xAE00; &quot;q&quot;"))
    }

    @Test
    fun queryAndResolve() {
        assertEquals("a b", HttpSourceSupport.query("https://x.example/p?k=a%20b&z=1", "k"))
        assertEquals(null, HttpSourceSupport.query("https://x.example/p", "k"))
        assertEquals("https://img.example.com/c.jpg", HttpSourceSupport.resolve("//img.example.com/c.jpg", "https://x.example/a/"))
        assertEquals(null, ReaderBlocks.resolve("file:///C:/x.png", null))
    }
}
