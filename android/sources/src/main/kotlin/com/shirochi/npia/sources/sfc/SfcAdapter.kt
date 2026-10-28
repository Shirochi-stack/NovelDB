package com.shirochi.npia.sources.sfc

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlocks
import org.jsoup.Jsoup

/**
 * Chinese light-novel platform. The book page and free chapters are plain HTML; VIP
 * chapters come from the Android app API with the user's app session (`login`).
 * Port of the sfc rule in `rules-lib.js` plus `_sfc_*` / `login_sfc_app` in the desktop
 * `external_scraper.py`, and of the iOS `SfcAdapter`.
 */
class SfcAdapter : SourceAdapter {
    override val id = SourceId.SFC
    override val tier = SourceTier.HTTP
    override val sites: Set<String> = setOf(BOOK_HOST)

    override fun matches(url: String): Boolean = Companion.matches(url)

    companion object {
        const val BOOK_HOST = "book.\u0073\u0066\u0061\u0063\u0067.com"

        /** Byte sizes of the placeholder image served instead of a VIP chapter image. */
        val placeholderImageSizes = setOf(53658, 42356)

        val headers = mapOf(
            "User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT,
            "Accept" to "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language" to "zh-CN,zh;q=0.9,en;q=0.8",
        )

        fun matches(url: String): Boolean = HttpSourceSupport.host(url) == BOOK_HOST && bookId(url) != null

        fun bookId(url: String): String? = HttpSourceSupport.firstMatch("^/Novel/(\\d+)", HttpSourceSupport.path(url))

        fun chapterId(url: String): String? = HttpSourceSupport.allMatches("(\\d+)", HttpSourceSupport.path(url)).lastOrNull()?.firstOrNull()

        // Login

        /**
         * Signs in through the app API and saves the session cookie (`sfc.appCookie`) in
         * `secrets`. Returns false when the reply carried no session.
         */
        suspend fun login(username: String, password: String, http: HttpFetching, secrets: SecretStore): Boolean =
            SfcAppApi.login(username, password, http, secrets)

        /** Saves a cookie header copied from the app; false when `session_APP` is missing. */
        fun saveAppCookie(cookie: String, secrets: SecretStore): Boolean = SfcAppApi.saveCookie(cookie, secrets)

        fun hasAppSession(secrets: SecretStore): Boolean = SfcAppApi.savedCookie(secrets) != null

        fun book(indexHtml: String, infoHtml: String, bookId: String, infoUrl: String, indexUrl: String): BookInfo {
            val index = Jsoup.parse(indexHtml, indexUrl)
            val info = Jsoup.parse(infoHtml, infoUrl)
            val title = index.selectFirst("h1.story-title")?.text() ?: info.selectFirst("h1.title .text")?.text() ?: ""
            if (title.isEmpty()) throw SourceException.Parse("book title not found")
            val author = info.selectFirst(".author-name")?.text() ?: ""
            val synopsis = info.selectFirst(".introduce")?.let { HttpSourceSupport.textLines(it).joinToString("\n") } ?: ""
            val cover = info.selectFirst("#hasTicket div.pic img")?.absUrl("src") ?: ""
            val tags = mutableListOf<String>()
            for (link in info.select("ul.tag-list > li.tag > a")) {
                link.select("span.icn").remove()
                val tag = link.text().replace(Regex("\\(\\d+\\)$"), "").trim()
                if (tag.isNotEmpty() && tag !in tags) tags += tag
            }

            val chapters = mutableListOf<ChapterRef>()
            for (link in index.select(".catalog-list a")) {
                val href = link.absUrl("href")
                val chapterId = if (href.isEmpty()) null else chapterId(href)
                if (chapterId == null) continue
                val name = link.attr("title").trim().ifEmpty { link.text() }
                val isVip = link.children().firstOrNull()?.className() == "icn_vip"
                chapters += ChapterRef(chapterId, chapters.size + 1, name, href, isPaid = isVip)
            }
            if (chapters.isEmpty()) throw SourceException.Parse("no chapters found in the catalog")
            return BookInfo(
                source = SourceId.SFC, id = bookId, url = infoUrl, title = title,
                author = author.ifEmpty { null }, synopsis = synopsis.ifEmpty { null },
                coverUrl = cover.ifEmpty { null }, tags = tags, language = "zh", chapters = chapters,
            )
        }

        fun webChapter(html: String, url: String, ref: ChapterRef): WebChapter {
            val document = Jsoup.parse(html, url)
            val guest = document.selectFirst(".user-bar > .top-link > .normal-link")?.html()?.contains("SF游客") ?: false
            // A guest only ever sees a preview of a VIP chapter.
            if (document.selectFirst(".pay-section") != null || (ref.isPaid && guest)) return WebChapter.Locked
            document.selectFirst(".article-content > #vipImage")?.absUrl("src")?.takeIf { it.isNotEmpty() }?.let {
                return WebChapter.VipImage(it)
            }
            val content = document.selectFirst(".article-content") ?: return if (ref.isPaid) WebChapter.Locked else WebChapter.Empty
            val title = document.selectFirst("h1.article-title")?.text().orEmpty()
            val named = if (title.isNotEmpty()) ref.copy(title = title) else ref
            val blocks = ReaderBlocks.fromHtml(content.outerHtml(), url)
            if (blocks.isEmpty()) return WebChapter.Empty
            return WebChapter.Content(ReaderBlocks.content(blocks, named, "sfc-content"))
        }
    }

    sealed class WebChapter {
        data class Content(val content: ChapterContent) : WebChapter()
        data class VipImage(val url: String) : WebChapter()
        data object Locked : WebChapter()
        data object Empty : WebChapter()
    }

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val bookId = bookId(url) ?: throw SourceException.UnsupportedUrl(url)
        val infoUrl = "https://$BOOK_HOST/Novel/$bookId/"
        val indexUrl = "https://$BOOK_HOST/Novel/$bookId/MainIndex/"
        val index = HttpSourceSupport.get(indexUrl, headers, context.http).text
        val info = HttpSourceSupport.get(infoUrl, headers, context.http).text
        return book(index, info, bookId, infoUrl, indexUrl)
    }

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
        val chapterId = chapterId(url) ?: ref.id
        var apiStatus: Int? = null
        // With an app session the app API serves every chapter, VIP ones included.
        val cookie = SfcAppApi.savedCookie(context.secrets)
        if (cookie != null) {
            when (val outcome = SfcAppApi.chapter(ref, chapterId, cookie, context.http)) {
                is SfcAppApi.ChapterOutcome.Content -> return ChapterResult.Content(outcome.content)
                is SfcAppApi.ChapterOutcome.NoAccess -> apiStatus = outcome.status
                SfcAppApi.ChapterOutcome.Unavailable -> Unit
            }
        }
        val page = HttpSourceSupport.get(url, headers, context.http).text
        return when (val outcome = webChapter(page, url, ref)) {
            is WebChapter.Content -> ChapterResult.Content(outcome.content)
            is WebChapter.VipImage -> vipImageChapter(outcome.url, url, ref, context.http)
            WebChapter.Locked -> if (apiStatus != null) {
                ChapterResult.Refused(ref, "the app session cannot read this VIP chapter")
            } else {
                ChapterResult.Refused(ref, "VIP chapter: sign in with the sfc app account that bought it")
            }
            WebChapter.Empty -> ChapterResult.Failed(ref, "no chapter text")
        }
    }

    /** Bought VIP chapters on the website are a single rendered image. */
    private suspend fun vipImageChapter(imageUrl: String, page: String, ref: ChapterRef, http: HttpFetching): ChapterResult {
        val response = HttpSourceSupport.get(
            imageUrl,
            mapOf(
                "User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT,
                "Accept" to "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer" to page,
            ),
            http,
        )
        if (response.body.size in placeholderImageSizes) {
            return ChapterResult.Refused(ref, "VIP chapter image was not served (not bought)")
        }
        val filename = ReaderBlocks.imageFilename("vip.gif", ref.index, 1)
        val html = "<div class=\"sfc-content\"><p class=\"img\"><img src=\"$filename\" alt=\"\"/></p></div>"
        return ChapterResult.Content(ChapterContent(ref, html, listOf(ImageRef(imageUrl, filename, response.body))))
    }
}
