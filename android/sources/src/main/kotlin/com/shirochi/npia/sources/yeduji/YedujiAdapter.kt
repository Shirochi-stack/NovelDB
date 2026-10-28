package com.shirochi.npia.sources.yeduji

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlocks

/**
 * Chinese novel site with free and VIP chapters. Port of `_yeduji_*` in the desktop
 * `external_scraper.py`, and of the iOS `YedujiAdapter`. VIP chapters only show a preview,
 * so they are refused.
 */
class YedujiAdapter : SourceAdapter {
    override val id = SourceId.YEDUJI
    override val tier = SourceTier.HTTP
    override val sites: Set<String> = setOf(HOST)

    override fun matches(url: String): Boolean = Companion.matches(url)

    companion object {
        const val HOST = "yeduji.com"

        val headers = mapOf(
            "User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT,
            "Accept" to "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language" to "zh-CN,zh;q=0.9,en;q=0.8",
        )
        private val dotAll = RegexOption.DOT_MATCHES_ALL
        const val VIP_NOTICE = "以下内容为VIP专属"
        const val PROMO = "夜读集由爱发电"

        fun matches(url: String): Boolean {
            val host = HttpSourceSupport.host(url)
            return (host == HOST || host == "www.$HOST") &&
                Regex("^/book/\\d+").containsMatchIn(HttpSourceSupport.path(url))
        }

        fun bookId(url: String): String? = HttpSourceSupport.firstMatch("/book/(\\d+)", HttpSourceSupport.path(url))

        fun stripTags(value: String): String = HttpSourceSupport.unescapeHtml(value.replace(Regex("<[^>]+>"), "")).trim()

        fun book(indexHtml: String, listHtml: String, bookId: String, indexUrl: String, listUrl: String): BookInfo {
            var title = HttpSourceSupport.firstMatch("<h1[^>]*>(.*?)</h1>", indexHtml, dotAll)
                ?.let { HttpSourceSupport.unescapeHtml(it.trim()) }.orEmpty()
            val info = HttpSourceSupport.firstMatch("<div\\s+class=[\"']info[\"'][^>]*>(.*?)</div>", indexHtml, dotAll).orEmpty()

            fun field(label: String): String? =
                HttpSourceSupport.firstMatch("<dt>\\s*" + Regex.escape(label) + "\\s*</dt>\\s*<dd>(.*?)</dd>", info, dotAll)

            fun links(label: String): List<String> {
                val value = field(label) ?: return emptyList()
                return HttpSourceSupport.allMatches("<a[^>]*>(.*?)</a>", value, dotAll)
                    .map { HttpSourceSupport.unescapeHtml(it[0].trim()) }
                    .filter { it.isNotEmpty() }
            }

            val pageTitle = HttpSourceSupport.firstMatch("<title>(.*?)</title>", indexHtml, dotAll).orEmpty()
            var author = field("作者")?.let(::stripTags).orEmpty()
            if (author.isEmpty()) {
                author = HttpSourceSupport.firstMatch("-\\s*(.+?)\\s*著\\s*-", pageTitle)?.trim().orEmpty()
            }
            if (title.isEmpty()) title = pageTitle.split(" - ").first().trim()
            val tags = links("标签")
            val category = links("分类")
            val status = field("状态")?.let(::stripTags).orEmpty()

            val cover = HttpSourceSupport.firstMatch(
                "<img[^>]+src=[\"']([^\"']*?/data/cover/[^\"']*)[\"'\\s]", indexHtml, RegexOption.IGNORE_CASE,
            )?.let { HttpSourceSupport.resolve(it, indexUrl) }

            var synopsis = ""
            HttpSourceSupport.firstMatch("<div\\s+class=[\"']desc[\"'][^>]*>(.*?)</div>", indexHtml, dotAll)?.let { found ->
                var inner = found.replace(Regex("<img[^>]*>"), "")
                HttpSourceSupport.firstMatch(
                    "<p[^>]*class=[\"'][^\"']*desc-content[^\"']*[\"'][^>]*>(.*?)</p>", inner, dotAll,
                )?.let { inner = it }
                synopsis = stripTags(inner.replace(Regex("<br\\s*/?>", RegexOption.IGNORE_CASE), "\n"))
            }

            val chapters = mutableListOf<ChapterRef>()
            val pattern = "<a\\s+data-chapterId=[\"']([^\"']*)[\"'\\s][^>]*href=[\"']([^\"']*?/book/\\d+/\\d+\\.html)[\"'\\s][^>]*>(.*?)</a>"
            for (groups in HttpSourceSupport.allMatches(pattern, listHtml, dotAll)) {
                val (chapterId, href, inner) = groups
                val name = HttpSourceSupport.firstMatch("<h4[^>]*>(.*?)</h4>", inner, dotAll)?.let(::stripTags) ?: "Chapter $chapterId"
                val isVip = Regex("<small[^>]*>\\s*VIP\\s*</small>", RegexOption.IGNORE_CASE).containsMatchIn(inner)
                val chapterUrl = HttpSourceSupport.resolve(href, listUrl) ?: continue
                chapters += ChapterRef(chapterId, chapters.size + 1, name, chapterUrl, isPaid = isVip)
            }
            if (chapters.isEmpty()) throw SourceException.Parse("no chapters found on the list page")

            return BookInfo(
                source = SourceId.YEDUJI, id = bookId, url = indexUrl, title = title.ifEmpty { "Book $bookId" },
                author = author.ifEmpty { null }, synopsis = synopsis.ifEmpty { null },
                coverUrl = cover, tags = tags + category.filter { it !in tags },
                status = status.ifEmpty { null }, language = "zh", chapters = chapters,
            )
        }

        fun chapter(html: String, ref: ChapterRef): ChapterResult {
            val content = HttpSourceSupport.firstMatch("<div\\s+class=[\"']content[\"'][^>]*>(.*?)</div>", html, dotAll)
                ?: return ChapterResult.Failed(ref, "no content block")
            if (content.contains(VIP_NOTICE)) return ChapterResult.Refused(ref, "VIP chapter (preview only)")
            val parts = mutableListOf<String>()
            for (groups in HttpSourceSupport.allMatches("<p[^>]*>(.*?)</p>", content, dotAll)) {
                val plain = stripTags(groups[0].trim())
                if (plain.isEmpty() || plain.contains(PROMO)) continue
                if (plain.contains("请先") && plain.contains("登录") && plain.contains("后查看完整内容")) continue
                parts += "<p>${ReaderBlocks.escape(plain, quote = false)}</p>"
            }
            if (parts.isEmpty()) return ChapterResult.Failed(ref, "no text content")
            return ChapterResult.Content(ChapterContent(ref, "<div class=\"yeduji-content\">\n" + parts.joinToString("\n") + "\n</div>"))
        }

        private suspend fun fetch(url: String, http: HttpFetching): String = HttpSourceSupport.get(url, headers, http).text
    }

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val bookId = bookId(url) ?: throw SourceException.UnsupportedUrl(url)
        val origin = "${HttpSourceSupport.scheme(url) ?: "https"}://${HttpSourceSupport.host(url).ifEmpty { "yeduji.com" }}"
        val indexUrl = "$origin/book/$bookId/"
        val listUrl = "$origin/book/$bookId/list/"
        val indexHtml = fetch(indexUrl, context.http)
        val listHtml = fetch(listUrl, context.http)
        return book(indexHtml, listHtml, bookId, indexUrl, listUrl)
    }

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        // The page only carries a preview for VIP chapters; never save it as the chapter.
        if (ref.isPaid) return ChapterResult.Refused(ref, "VIP chapter (preview only)")
        val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
        return chapter(fetch(url, context.http), ref)
    }
}
