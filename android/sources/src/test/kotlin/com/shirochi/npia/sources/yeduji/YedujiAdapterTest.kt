package com.shirochi.npia.sources.yeduji

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.RoutedHttp
import com.shirochi.npia.sources.http.content
import com.shirochi.npia.sources.http.isRefused
import com.shirochi.npia.sources.http.makeContext
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class YedujiAdapterTest {
    private val index = """
        <html><head><title>夜之书 - 某某 著 - 夜读集</title></head><body>
        <h1>夜之书</h1>
        <img class="cover" src="/data/cover/1/77.jpg" />
        <div class="info"><dl><dt>作者</dt><dd><a href="/author/1">某某</a></dd></dl>
        <dl><dt>分类</dt><dd><a href="/c/1">玄幻</a></dd></dl>
        <dl><dt>标签</dt><dd><a href="/t/1">热血</a><a href="/t/2">系统</a></dd></dl>
        <dl><dt>状态</dt><dd>连载中</dd></dl></div>
        <div class="desc"><img src="/more.png"><p class="desc-content">第一段<br/>第二段 &amp; 更多</p></div>
        </body></html>
    """.trimIndent()

    private val list = """
        <ul>
        <li><a data-chapterId="901" href="/book/77/901.html" class="c"><h4>第一章 开始</h4><small class="text-muted">免费</small></a></li>
        <li><a data-chapterId="902" href="/book/77/902.html" class="c"><h4>第二章 &amp; 继续</h4><small class="text-muted">VIP</small></a></li>
        </ul>
    """.trimIndent()

    @Test
    fun matches() {
        val adapter = YedujiAdapter()
        assertTrue(adapter.matches("https://www.yeduji.com/book/77/"))
        assertFalse(adapter.matches("https://yeduji.com/author/77"))
    }

    @Test
    fun parseBook() = runTest {
        val http = RoutedHttp()
        http.on("/book/77/", index)
        http.on("/book/77/list/", list)
        val book = YedujiAdapter().parseBook("https://yeduji.com/book/77/901.html", makeContext(http))
        assertEquals("夜之书", book.title)
        assertEquals("某某", book.author)
        assertEquals(listOf("热血", "系统", "玄幻"), book.tags)
        assertEquals("连载中", book.status)
        assertEquals("第一段\n第二段 & 更多", book.synopsis)
        assertEquals("https://yeduji.com/data/cover/1/77.jpg", book.coverUrl)
        assertEquals(listOf("第一章 开始", "第二章 & 继续"), book.chapters.map { it.title })
        assertEquals(listOf(false, true), book.chapters.map { it.isPaid })
        assertEquals("https://yeduji.com/book/77/902.html", book.chapters[1].url)
    }

    @Test
    fun chapterStripsPromoAndRefusesVip() = runTest {
        val ref = ChapterRef("901", 1, "第一章", "https://yeduji.com/book/77/901.html")
        val html = """
            <h1 class="title">第一章 开始</h1>
            <div class="content"><p>正文一</p><p>夜读集由爱发电支持</p><p><b>正文二</b></p><p> </p></div>
        """.trimIndent()
        val body = content(YedujiAdapter.chapter(html, ref))
        assertEquals("<div class=\"yeduji-content\">\n<p>正文一</p>\n<p>正文二</p>\n</div>", body?.html)

        val preview = "<div class=\"content\"><p>预览</p><p>以下内容为VIP专属</p></div>"
        assertTrue(isRefused(YedujiAdapter.chapter(preview, ref)))

        val paid = ref.copy(isPaid = true)
        val http = RoutedHttp()
        val book = BookInfo(SourceId.YEDUJI, "77", "https://yeduji.com/book/77/", "t")
        assertTrue(isRefused(YedujiAdapter().parseChapter(paid, book, makeContext(http))))
        assertTrue(http.requests.isEmpty())
    }
}
