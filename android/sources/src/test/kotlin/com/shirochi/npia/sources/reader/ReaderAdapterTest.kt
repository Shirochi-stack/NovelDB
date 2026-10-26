package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UnavailableWebEngine
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Base64

class ReaderAdapterTest {
    // Loading and matching

    @Test
    fun rulesBundleLoadsInQuickJs() {
        val runtime = ReaderRuntime.createBlocking(FixtureHttp(emptyMap()))
        try {
            assertEquals("function", runtime.evaluate("typeof window.__ND_getRule"))
            assertEquals("function", runtime.evaluate("typeof window.__ND_parseBook"))
            assertEquals("true", runtime.evaluate("window.__ND_READY === true"))
            assertEquals("function", runtime.evaluate("typeof GM_xmlhttpRequest"))
        } finally {
            runtime.close()
        }
    }

    @Test
    fun matchesAsksTheRules() {
        val adapter = ReaderAdapter()
        assertTrue(adapter.matches("https://www.sudugu.com/1001/"))
        assertTrue(adapter.matches("https://ncode.syosetu.com/n0000aa/"))
        assertTrue(adapter.matches("https://kakuyomu.jp/works/1"))
        // Rules that match a host pattern rather than a fixed name.
        assertTrue(adapter.matches("https://booktoki123.com/novel/1"))
        assertFalse(adapter.matches("https://example.com/book/1"))
        assertFalse(adapter.matches("ftp://www.sudugu.com/1001/"))
        assertEquals(SourceId.READER, SourceCatalog.adapterFor("https://www.sudugu.com/1001/")?.id)
    }

    // End to end

    @Test
    fun suduguBookAndChapters() = runBlocking {
        val context = makeContext(Fixtures.sudugu)
        val adapter = ReaderAdapter()
        val book = adapter.parseBook("https://www.sudugu.com/1001/", context)

        assertEquals(SourceId.READER, book.source)
        assertEquals("测试之书", book.title)
        assertEquals("张三", book.author)
        assertEquals("这是一段简介。\n\n第二行。", book.synopsis)
        assertEquals(listOf("玄幻", "连载"), book.tags)
        assertEquals("https://www.sudugu.com/cover/1001.jpg", book.coverUrl)
        assertEquals(listOf("第一章 开始", "第二章 继续", "第三章 结束"), book.chapters.map { it.title })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals("https://www.sudugu.com/1001/3.html", book.chapters[2].url)
        assertFalse(book.chapters.any { it.isPaid })

        // Two-page chapter with an image and a repeated title line.
        val first = adapter.parseChapter(book.chapters[0], book, context)
        if (first !is ChapterResult.Content) fail("chapter 1 should parse: $first")
        first as ChapterResult.Content
        assertEquals(
            """
            <div class="reader-content">
            <p>第一段。</p>
            <p>第二段 文字。</p>
            <p class="img"><img src="images/0001_01.png" alt=""/></p>
            <p>第三段。</p>
            </div>
            """.trimIndent(),
            first.content.html,
        )
        assertEquals(listOf(ImageRef("https://www.sudugu.com/img/a.png", "images/0001_01.png")), first.content.images)

        val results = adapter.parseChapters(book.chapters.drop(1), book, context)
        val second = results.getOrNull(0) as? ChapterResult.Content
        val third = results.getOrNull(1) as? ChapterResult.Content
        if (results.size != 2 || second == null || third == null) fail("chapters 2 and 3 should parse: $results")
        assertTrue(second!!.content.html, second.content.html.contains("<p>甲行</p>\n<p>乙行</p>\n<p>丙行</p>"))
        assertTrue(third!!.content.html, third.content.html.contains("<p>完 &amp; 结</p>"))
    }

    @Test
    fun templateRuleWithSections() = runBlocking {
        val context = makeContext(Fixtures.syosetu)
        val adapter = ReaderAdapter()
        val book = adapter.parseBook("https://ncode.syosetu.com/n0000aa/", context)

        assertEquals("試験の物語", book.title)
        assertEquals("ja", book.language)
        assertEquals("あらすじです。\n二行目。", book.synopsis)
        assertEquals(listOf("プロローグ", "第一話", "第二話"), book.chapters.map { it.title })
        assertEquals(
            listOf(
                "https://ncode.syosetu.com/n0000aa/1/",
                "https://ncode.syosetu.com/n0000aa/2/",
                "https://ncode.syosetu.com/n0000aa/3/",
            ),
            book.chapters.map { it.url },
        )

        val result = adapter.parseChapter(book.chapters[0], book, context)
        if (result !is ChapterResult.Content) fail("chapter should parse: $result")
        assertEquals(
            """
            <div class="reader-content">
            <p>前書き</p>
            <p>本文一行目</p>
            <p>本文三行目</p>
            <p>後書き</p>
            </div>
            """.trimIndent(),
            (result as ChapterResult.Content).content.html,
        )
    }

    @Test
    fun chapterFromAnotherSessionReopensTheBook() = runBlocking {
        val context = makeContext(Fixtures.sudugu)
        val session = ReaderBookSession.open("https://www.sudugu.com/1001/", context)
        val book = session.book
        session.close()
        // A fresh BookInfo (e.g. restored after relaunch) still resolves its chapters.
        val result = ReaderAdapter().parseChapter(book.chapters[2], book.copy(), context)
        if (result !is ChapterResult.Content) fail("$result")
        assertTrue((result as ChapterResult.Content).content.html.contains("完 &amp; 结"))
    }

    @Test
    fun missingPageFailsWithParseError() = runBlocking {
        val context = makeContext(emptyMap())
        try {
            ReaderAdapter().parseBook("https://www.sudugu.com/404/", context)
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("HTTP 404 for https://www.sudugu.com/404/"))
        }
    }

    @Test
    fun closingOneRuntimeKeepsOthersWorking() = runBlocking {
        val open = listOf("https://www.sudugu.com/1001/", null, null, emptyMap<String, String>())
        val chapter = listOf("/1001/3.html", "x", false, null)
        val kept = ReaderRuntime.create(FixtureHttp(Fixtures.sudugu))
        kept.call("openBook", open, 30)
        repeat(2) {
            val other = ReaderRuntime.create(FixtureHttp(Fixtures.sudugu))
            other.call("openBook", open, 30)
            other.close()
            assertTrue(kept.call("parseChapter", chapter, 30).contains("完 & 结"))
        }
        kept.close()
        val fresh = ReaderRuntime.create(FixtureHttp(Fixtures.sudugu))
        assertEquals("function", fresh.evaluate("typeof __npia_call"))
        fresh.close()
    }

    // Chapter HTML

    @Test
    fun chapterBuilderDropsRepeatedTitleAndEscapes() {
        val ref = ChapterRef(id = "x", index = 12, title = "Chapter  ONE")
        val content = ReaderChapterBuilder.content(
            ref,
            listOf(
                RuleBlock.Text("chapter one"),
                RuleBlock.Text("a < b & \"c\""),
                RuleBlock.Image("https://img.example/p/1.webp?x=1"),
                RuleBlock.Image("https://img.example/p/1.webp?x=1"),
                RuleBlock.Image("https://img.example/p/2"),
            ),
        )
        assertEquals(
            """
            <div class="reader-content">
            <p>a &lt; b &amp; &quot;c&quot;</p>
            <p class="img"><img src="images/0012_01.webp" alt=""/></p>
            <p class="img"><img src="images/0012_01.webp" alt=""/></p>
            <p class="img"><img src="images/0012_02.jpg" alt=""/></p>
            </div>
            """.trimIndent(),
            content?.html,
        )
        assertEquals(listOf("images/0012_01.webp", "images/0012_02.jpg"), content?.images?.map { it.filename })
        assertNull(ReaderChapterBuilder.content(ref, listOf(RuleBlock.Text("Chapter one"))))
    }

    @Test
    fun paidChapterWithoutTextIsRefused() = runBlocking {
        val ref = ChapterRef(id = "/1001/9.html", index = 9, title = "VIP", url = "https://www.sudugu.com/1001/9.html", isPaid = true)
        val runtime = ReaderRuntime.create(FixtureHttp(emptyMap()))
        val session = ReaderBookSession(
            BookInfo(SourceId.READER, "b", "https://www.sudugu.com/1001/", "b", chapters = listOf(ref)),
            ReaderBookSession.Engine.Script(runtime),
            mapOf(ref.id to ReaderBookSession.ChapterInfo(ref.id, "VIP", isVIP = true, isPaid = false)),
        )
        // No book is open in the runtime, so the rule call fails; a paid chapter is refused, not failed.
        val result = session.chapter(ref)
        session.close()
        if (result !is ChapterResult.Refused) fail("$result")
        assertEquals(9, result.ref.index)
    }

    // JavaScript environment

    @Test
    fun domShimFollowsBrowserParsing() {
        val runtime = ReaderRuntime.createBlocking(FixtureHttp(emptyMap()))
        val script = """
            (function () {
              var d = new DOMParser().parseFromString('<table><tr><td>a</td><td>b</td></tr></table><p>x<p>y &amp; z<ul><li>1<li>2</ul><div id="c" class="k m"><span>s</span> t</div>', 'text/html');
              return JSON.stringify([
                d.querySelectorAll('table > tbody > tr > td').length,
                d.querySelectorAll('body > p').length,
                d.querySelector('p:nth-of-type(2)').textContent,
                d.querySelectorAll('ul > li:last-child')[0].textContent,
                d.querySelector('div.k.m > span + *') === null,
                d.querySelector('#c').innerHTML,
                d.querySelector('div:has(> span)').id,
                d.querySelectorAll('li:not(:first-child)').length,
                new URL('../b?q=1#h', 'https://a.example/x/y/z').href
              ]);
            })()
        """.trimIndent()
        try {
            assertEquals("""[2,2,"y & z","2",true,"<span>s</span> t","c",1,"https://a.example/x/b?q=1#h"]""", runtime.evaluate(script))
        } finally {
            runtime.close()
        }
    }

    @Test
    fun textDecoderUsesJvmCharsets() {
        val runtime = ReaderRuntime.createBlocking(FixtureHttp(emptyMap()))
        val bytes = Base64.getEncoder().encodeToString("第一章 中文".toByteArray(charset("GB18030")))
        try {
            assertEquals("第一章 中文", runtime.evaluate("new TextDecoder('gbk').decode(__npia_env.base64ToBytes('$bytes'))"))
        } finally {
            runtime.close()
        }
        assertEquals("é", ReaderText.decode("é".toByteArray(), "utf-8"))
        assertEquals("한국어", ReaderText.decode("한국어".toByteArray(charset("EUC-KR")), "euc-kr"))
    }

    @Test
    fun cookiesFollowDomainPathAndExpiry() {
        val cookies = ReaderCookies()
        cookies.set("https://www.example.com/a/b", "sid=1; path=/")
        cookies.set("https://www.example.com/a/b", "wide=2; domain=.example.com")
        cookies.set("https://www.example.com/a/b", "deep=3; path=/a")
        cookies.set("https://www.example.com/a/b", "other=4; domain=elsewhere.com")
        assertEquals("sid=1; wide=2; deep=3", cookies.header("https://www.example.com/a/c"))
        assertEquals("wide=2", cookies.header("https://m.example.com/"))
        cookies.set("https://www.example.com/", "sid=gone; max-age=0")
        assertEquals("wide=2; deep=3", cookies.header("https://www.example.com/a/"))
    }

    // Helpers

    private fun makeContext(pages: Map<String, String>) =
        SourceContext(FixtureHttp(pages), UnavailableWebEngine, InMemorySecretStore(), AppSettings())
}

/** Serves fixture pages by absolute URL; anything else is a 404. */
internal class FixtureHttp(private val pages: Map<String, String>) : HttpFetching {
    override suspend fun send(request: HttpRequest): HttpResponse {
        val page = pages[request.url] ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
        return HttpResponse(request.url, 200, mapOf("content-type" to "text/html; charset=utf-8"), page.toByteArray())
    }
}

/** Small hand-made pages shaped like the sites the rules expect. */
private object Fixtures {
    val sudugu = mapOf(
        "https://www.sudugu.com/1001/" to """
            <!DOCTYPE html><html><head><meta charset="utf-8"><title>测试之书</title></head><body>
            <div class="item"><img src="/cover/1001.jpg"></div>
            <div class="itemtxt"><h1><a href="/1001/">测试之书</a></h1><p><a href="/author/1">作者：张三</a> <span>玄幻</span><span>连载</span></p></div>
            <div class="des"><p>这是一段简介。</p><p>第二行。</p></div>
            <div id="list"><ul><li><a href="/1001/1.html">第一章 开始</a></li><li><a href="/1001/2.html">第二章 继续</a></li></ul></div>
            <div id="pages"><a href="/1001/">上一页</a><a href="/1001/p-2.html">下一页</a></div>
            </body></html>
        """.trimIndent(),
        "https://www.sudugu.com/1001/p-2.html" to """
            <html><body><div id="list"><ul><li><a href="/1001/3.html">第三章 结束</a></li></ul></div>
            <div id="pages"><a href="/1001/">上一页</a></div></body></html>
        """.trimIndent(),
        "https://www.sudugu.com/1001/1.html" to """
            <html><body><div class="con"><p>第一章 开始</p><p>第一段。</p><p>第二段&nbsp;文字。</p><p><img src="/img/a.png"></p></div>
            <div class="prenext"><span><a href="/1001/">目录</a></span><span><a href="/1001/1_2.html">下一页</a></span></div></body></html>
        """.trimIndent(),
        "https://www.sudugu.com/1001/1_2.html" to """
            <html><body><div class="con"><p>第三段。</p></div>
            <div class="prenext"><span><a href="/1001/2.html">下一章</a></span></div></body></html>
        """.trimIndent(),
        "https://www.sudugu.com/1001/2.html" to """
            <html><body><div class="con">甲行<br>乙行<br><br>丙行</div></body></html>
        """.trimIndent(),
        "https://www.sudugu.com/1001/3.html" to """
            <html><body><div class="con"><p>完 &amp; 结</p><script>ad()</script></div></body></html>
        """.trimIndent(),
    )

    val syosetu = mapOf(
        "https://ncode.syosetu.com/n0000aa/" to """
            <!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><title>試験の物語</title></head><body>
            <div class="l-container"><main><article>
            <h1 class="p-novel__title">試験の物語</h1>
            <div class="p-novel__author">作者：<a href="/u/1/">山田</a></div>
            <div id="novel_ex" class="p-novel__summary">あらすじです。<br>二行目。</div>
            <div class="p-eplist">
            <div class="p-eplist__chapter-title">第一部</div>
            <div class="p-eplist__sublist"><a href="/n0000aa/1/" class="p-eplist__subtitle">プロローグ</a></div>
            <div class="p-eplist__sublist"><a href="/n0000aa/2/" class="p-eplist__subtitle">第一話</a></div>
            <div class="p-eplist__chapter-title">第二部</div>
            <div class="p-eplist__sublist"><a href="/n0000aa/3/" class="p-eplist__subtitle">第二話</a></div>
            </div></article></main></div></body></html>
        """.trimIndent(),
        "https://ncode.syosetu.com/n0000aa/1/" to """
            <html><body><div class="p-novel__body">
            <div class="p-novel__text p-novel__text--preface"><p>前書き</p></div>
            <div class="p-novel__text"><p id="L1">本文一行目</p><p id="L2"><br></p><p id="L3">本文三行目</p></div>
            <div class="p-novel__text p-novel__text--afterword"><p>後書き</p></div>
            </div></body></html>
        """.trimIndent(),
        "https://ncode.syosetu.com/n0000aa/2/" to "<html><body><div class=\"p-novel__text\"><p>二話です。</p></div></body></html>",
        "https://ncode.syosetu.com/n0000aa/3/" to "<html><body><div class=\"p-novel__text\"><p>三話です。</p></div></body></html>",
    )
}
