package com.shirochi.npia.sources.jara

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.RoutedHttp
import com.shirochi.npia.sources.http.content
import com.shirochi.npia.sources.http.isRefused
import com.shirochi.npia.sources.http.makeContext
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class JaraAdapterTest {
    private val host = JaraAdapter.HOST

    @Test
    fun matches() {
        val adapter = JaraAdapter()
        assertTrue(adapter.matches("https://www.$host/book/1234567"))
        assertTrue(adapter.matches("https://m.$host/viewer?bookCode=1234567&cid=9"))
        assertFalse(adapter.matches("https://www.$host/viewer?bookCode=0123"))
        assertFalse(adapter.matches("https://example.com/book/1234567"))
        assertEquals("77", JaraAdapter.bookCode("https://$host/book/77/"))
    }

    @Test
    fun chapterNames() {
        assertEquals("3화", JaraAdapter.chapterName(3, ""))
        assertEquals("3화 - 만남", JaraAdapter.chapterName(3, "만남"))
        assertEquals("이야기 183화", JaraAdapter.chapterName(3, "이야기 183화"))
        assertEquals("Chapter", JaraAdapter.chapterName(0, ""))
    }

    @Test
    fun signedInfoToken() {
        val future = System.currentTimeMillis() + 60_000
        assertEquals("abc", JaraAdapter.tokenFromSignedInfo("{\"data\":\"{\\\"token\\\":\\\"abc\\\"}\",\"expire\":$future}"))
        assertEquals("xyz", JaraAdapter.tokenFromSignedInfo("{\"token\":\"xyz\"}"))
        assertNull(JaraAdapter.tokenFromSignedInfo("{\"data\":{\"token\":\"old\"},\"expire\":1000}"))
        assertNull(JaraAdapter.tokenFromSignedInfo("not json"))
    }

    @Test
    fun bundleParams() {
        val bundle = "x={baseURL:\"https://api.$host\",apiKey:\"mw_new\",version:\"3.3.0\",device:\"mw\",devicetoken:\"mw\"}"
        assertEquals(
            mapOf("api_key" to "mw_new", "ver" to "3.3.0", "device" to "mw", "devicetoken" to "mw"),
            JaraAdapter.paramsFromBundle(bundle),
        )
    }

    @Test
    fun decryptMatchesDesktopCipher() {
        // Produced with the desktop's `cryptography` AES-CBC/PKCS7 path.
        val text = JaraCrypto.decrypt("jmo66ZhGK4FFGOsdRmM40gS8PHtVrBAkKrtB4lYKLAk=", "0123456789abcdef", "fedcba9876543210")
        assertEquals("첫 문단\n둘째 문단", text)
        try {
            JaraCrypto.decrypt("jmo66ZhGK4FFGOsdRmM40gS8PHtVrBAkKrtB4lYKLAk=", "0123456789abcdeX", "fedcba9876543210")
            fail("expected a decrypt failure")
        } catch (e: JaraCrypto.Failure) {
            // expected
        }
    }

    private fun bookPayload() = mapOf(
        "status" to 1,
        "book" to mapOf(
            "book_code" to "77", "subject" to "제목 &amp; 부제", "writer_name" to "작가", "intro" to "소개\r\n둘째",
            "book_img" to "https://cdn.example.com/77.jpg", "keyword" to listOf("#판타지", "회귀"), "category_ko_name" to "판타지",
            "is_premium" to "TRUE", "chk_finish" to "N",
            "chapter" to listOf(
                mapOf("cid" to "902", "sortno" to 2, "sub_subject" to "만남", "is_free" to "N", "is_buy" to "N"),
                mapOf("cid" to "901", "sortno" to 1, "sub_subject" to "", "is_free" to "Y"),
            ),
        ),
    )

    @Test
    fun parseBook() = runTest {
        val http = RoutedHttp()
        http.onJson("/v1/book/detail.joa", bookPayload())
        val book = JaraAdapter().parseBook("https://www.$host/book/77", makeContext(http))
        assertEquals("제목 & 부제", book.title)
        assertEquals(listOf("판타지", "회귀"), book.tags)
        assertEquals("소개\n둘째", book.synopsis)
        assertEquals("연재", book.status)
        assertEquals(listOf("1화", "2화 - 만남"), book.chapters.map { it.title })
        assertEquals(listOf(false, true), book.chapters.map { it.isPaid })
        assertEquals(listOf("901", "902"), book.chapters.map { it.id })
        val request = http.requested("detail.joa").first()
        assertEquals("mw_8ba234e7801ba288554ca07ae44c7", HttpSourceSupport.query(request.url, "api_key"))
        assertEquals("77", HttpSourceSupport.query(request.url, "book_code"))
        assertNull(HttpSourceSupport.query(request.url, "token"))
    }

    @Test
    fun parseBookUsesSavedTokenAndRequiresLogin() = runTest {
        val http = RoutedHttp()
        http.onJson("/v1/book/detail.joa", mapOf("status" to 0, "error_code" to 9200, "message" to "로그인이 필요합니다"))
        val secrets = InMemorySecretStore(mapOf(JaraAdapter.tokenKey to "tok123"))
        try {
            JaraAdapter().parseBook("https://www.$host/book/77", makeContext(http, secrets))
            fail("expected login error")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.JARA, e.source)
        }
        val calls = http.requested("detail.joa")
        assertEquals(2, calls.size)
        assertEquals("tok123", HttpSourceSupport.query(calls[0].url, "token"))
        assertNull(HttpSourceSupport.query(calls[1].url, "token"))
    }

    @Test
    fun parseChapterDecryptsAndAppendsAuthorNote() = runTest {
        val key = "0123456789abcdef"
        val iv = "fedcba9876543210"
        val http = RoutedHttp()
        http.onJson("/v1/book/chapter_valid.joa", mapOf("status" to 1, "data" to listOf(key, iv)))
        http.onJson(
            "/v1/book/chapter.joa",
            mapOf(
                "status" to 1,
                "chapter" to mapOf("content" to JaraCrypto.encrypt("2화 - 만남\n본문 첫 줄\n\n본문 둘째 줄", key, iv), "episode" to "작가의 말"),
            ),
        )
        val ref = ChapterRef("902", 2, "2화 - 만남", "https://www.$host/viewer?cid=902&bookCode=77&sortno=2")
        val book = BookInfo(SourceId.JARA, "77", "https://www.$host/book/77", "t")
        val body = content(JaraAdapter().parseChapter(ref, book, makeContext(http)))
        assertNotNull(body)
        assertEquals(
            """
            <div class="jara-content">
            <p>본문 첫 줄</p>
            <p>본문 둘째 줄</p>
            <p>* * *</p>
            <p>작가의 말</p>
            </div>
            """.trimIndent(),
            body!!.html,
        )
        assertEquals("902", HttpSourceSupport.query(http.requested("chapter.joa?").first().url, "cid"))
    }

    @Test
    fun parseChapterLoginErrorIsRefused() = runTest {
        val http = RoutedHttp()
        http.onJson("/v1/book/chapter_valid.joa", mapOf("status" to 1, "data" to listOf("0123456789abcdef", "fedcba9876543210")))
        http.onJson("/v1/book/chapter.joa", mapOf("status" to 0, "error_code" to 9200, "message" to "로그인"))
        val ref = ChapterRef("902", 2, "2화", isPaid = true)
        val book = BookInfo(SourceId.JARA, "77", "https://www.$host/book/77", "t")
        assertTrue(isRefused(JaraAdapter().parseChapter(ref, book, makeContext(http))))
    }

    @Test
    fun plainTextHtmlChapter() {
        val ref = ChapterRef("1", 1, "x")
        val body = content(JaraAdapter.chapter("<p>하나</p><p>둘<br>셋</p>", "", ref))
        assertTrue(body!!.html.contains("<p>하나</p>\n<p>둘</p>\n<p>셋</p>"))
    }
}
