package com.shirochi.npia.sources.maru

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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MaruAdapterTest {
    private val site = "https://marumaru123.com/novel/42"

    @Test
    fun matches() {
        val adapter = MaruAdapter()
        assertTrue(adapter.matches(site))
        assertTrue(adapter.matches("https://www.marumaru9.net/novel/42/77"))
        assertFalse(adapter.matches("https://marumaru123.com/webtoon/42"))
        assertFalse(adapter.matches("https://example.com/novel/42"))
        assertEquals("77", MaruAdapter.parts("https://marumaru1.com/novel/42/77")?.episode)
    }

    @Test
    fun parseBookSortsEpisodesAndStripsWorkTitle() = runTest {
        val http = RoutedHttp()
        http.onJson(
            "works/42",
            mapOf(
                "ok" to true,
                "item" to mapOf("title" to "작품", "author" to "작가", "cover_url" to "https://img.example.com/c.jpg", "genres" to "판타지, 무협/로맨스", "status" to "연재"),
                "synopsis" to "줄거리\n둘째 줄",
                "episodes" to listOf(
                    mapOf("id" to 12, "number" to 2, "title" to "작품 2화"),
                    mapOf("id" to 11, "number" to 1, "title" to "작품  프롤로그"),
                    mapOf("id" to 13, "number" to 3, "title" to ""),
                ),
            ),
        )
        val book = MaruAdapter().parseBook(site, makeContext(http))
        assertEquals("작품", book.title)
        assertEquals("작가", book.author)
        assertEquals(listOf("판타지", "무협", "로맨스"), book.tags)
        assertEquals(listOf("프롤로그", "2화", "3화"), book.chapters.map { it.title })
        assertEquals(listOf("11", "12", "13"), book.chapters.map { it.id })
        assertEquals("https://marumaru123.com/novel/42/11", book.chapters[0].url)
        assertNull(http.requests.first().headers["Referer"])
    }

    @Test
    fun chapterKinds() = runTest {
        val ref = ChapterRef("11", 1, "프롤로그", "https://marumaru123.com/novel/42/11")
        val book = BookInfo(SourceId.MARU, "42", site, "작품")
        val http = RoutedHttp()
        http.onJson(
            "episodes/11",
            mapOf(
                "ok" to true,
                "item" to mapOf(
                    "body" to listOf(
                        mapOf("kind" to "text", "text" to "첫 줄\n\n둘째 줄"),
                        mapOf("kind" to "image", "url" to "https://img.example.com/1.webp"),
                    ),
                ),
            ),
        )
        val body = content(MaruAdapter().parseChapter(ref, book, makeContext(http)))!!
        assertTrue(body.html.contains("<p>첫 줄</p>\n<p>둘째 줄</p>"))
        assertEquals(listOf("images/0001_01.webp"), body.images.map { it.filename })

        http.onJson("episodes/11", mapOf("ok" to true, "item" to mapOf("body" to listOf(mapOf("kind" to "paid")))))
        assertTrue(isRefused(MaruAdapter().parseChapter(ref, book, makeContext(http))))

        http.onJson("episodes/11", mapOf("ok" to true, "item" to mapOf("ready" to false, "body" to emptyList<Any>())))
        assertTrue(isRefused(MaruAdapter().parseChapter(ref, book, makeContext(http))))

        http.onJson("episodes/11", mapOf("ok" to true, "item" to mapOf("embed_url" to "/files/11.txt", "body" to emptyList<Any>())))
        http.on("files/11.txt", "${Char(0xfeff)}임베드 본문\n끝")
        val embedded = content(MaruAdapter().parseChapter(ref, book, makeContext(http)))!!
        assertTrue(embedded.html.contains("<p>임베드 본문</p>"))
    }
}
