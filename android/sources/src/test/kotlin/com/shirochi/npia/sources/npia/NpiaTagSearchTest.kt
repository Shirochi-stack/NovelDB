package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.NpiaSettings
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URL
import java.net.URLDecoder
import java.util.Collections

/** Answers every request through `handler` (status to body) and records them. */
private class FakeHttp(private val handler: (HttpRequest) -> Pair<Int, String>) : HttpFetching {
    val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())

    override suspend fun send(request: HttpRequest): HttpResponse {
        requests += request
        val (status, body) = handler(request)
        return HttpResponse(request.url, status, mapOf("content-type" to "application/json"), body.encodeToByteArray())
    }
}

private fun query(request: HttpRequest): Map<String, String> =
    (URL(request.url).query ?: "").split("&").filter { it.isNotEmpty() }.associate { pair ->
        val parts = pair.split("=", limit = 2)
        URLDecoder.decode(parts[0], "UTF-8") to URLDecoder.decode(parts.getOrElse(1) { "" }, "UTF-8")
    }

private fun form(request: HttpRequest): Map<String, String> =
    (request.body?.decodeToString() ?: "").split("&").filter { it.isNotEmpty() }.associate { pair ->
        val parts = pair.split("=", limit = 2)
        URLDecoder.decode(parts[0], "UTF-8") to URLDecoder.decode(parts.getOrElse(1) { "" }, "UTF-8")
    }

private fun path(request: HttpRequest) = URL(request.url).path

/** The `novel_genre` search reply for a list of novel numbers. */
private fun idsReply(vararg ids: Int) = """{"status":200,"list":[${ids.joinToString(",") { """{"novel_no":$it}""" }}]}"""

class NpiaTagSearchTest {
    private val tags = mapOf(
        "TS" to idsReply(1, 2, 3, 4),
        "약피폐" to idsReply(2, 3),
        "피폐" to idsReply(3, 9),
        "백합" to idsReply(4, 5),
    )

    private fun tagHttp() = FakeHttp { request ->
        val params = query(request)
        if (params["search_type"] == "novel_genre") 200 to (tags[params["search_val"]] ?: idsReply()) else 404 to ""
    }

    private fun search(http: HttpFetching, logs: MutableList<String> = mutableListOf(), stopped: () -> Boolean = { false }) =
        NpiaTagSearch(http, "key", { logs += it }, stopped)

    // Search terms

    @Test
    fun searchTermsCoverEveryTagAndSweepCharacter() {
        assertEquals(165, NpiaSearchTerms.DEFAULT_SEARCH_QUERY_COUNT)
        assertEquals(NpiaSettings.MAX_QUERIES, NpiaSearchTerms.DEFAULT_SEARCH_QUERY_COUNT)
        assertEquals(NpiaSearchTerms.ALL_SEARCH_TERMS.distinct(), NpiaSearchTerms.ALL_SEARCH_TERMS)
        assertEquals(NpiaSearchTerms.SEARCH_TAGS + NpiaSearchTerms.SWEEP_CHARS, NpiaSearchTerms.ALL_SEARCH_TERMS)
        assertEquals("판타지", NpiaSearchTerms.ALL_SEARCH_TERMS.first())
        assertEquals("9", NpiaSearchTerms.ALL_SEARCH_TERMS.last())
        assertEquals(setOf(408, 429, 500, 502, 503, 504), NpiaSearchTerms.RETRYABLE_STATUS_CODES)
    }

    // Tag groups

    @Test
    fun groupsAreAndedAndTheTagsInAGroupAreOred() = runTest {
        val http = tagHttp()
        val logs = mutableListOf<String>()
        val ids = search(http, logs).fetchNovelsByTags(
            TagQuery.Groups(listOf(listOf("TS"), listOf("약피폐", "피폐"))), delay = 0.0, ageFilter = "15",
        )

        assertEquals(listOf("2", "3"), ids)
        assertEquals(
            listOf(
                "Searching with 2 group(s), 1 thread(s):",
                "  Group 1: TS",
                "    AND",
                "  Group 2: 약피폐 OR 피폐",
                "  Age filter: Non-adult only",
                "  [TS] 4 novel(s)",
                "  Group 1 result: 4 novel(s)",
                "  [약피폐] 2 novel(s)",
                "  [피폐] 2 novel(s)",
                "  Group 2 result: 3 novel(s)",
                "  After AND with group 2: 2 novel(s)",
                "Tag search complete: 2 novel(s) found.",
            ),
            logs,
        )

        val first = http.requests.first()
        assertEquals("GET", first.method)
        assertEquals("/proc/novel", path(first))
        val params = query(first)
        assertEquals("novel_search", params["cmd"])
        assertEquals("novel_genre", params["search_type"])
        assertEquals("50000", params["rows"])
        assertEquals("1", params["page"])
        assertEquals("15", params["novel_age"])
        assertEquals("last_viewdate", params["sort_col"])
        assertEquals("XMLHttpRequest", first.headers["X-Requested-With"])
        assertEquals("https://${NpiaParser.HOST}/search", first.headers["Referer"])
        assertEquals("LOGINKEY=key", first.headers["Cookie"])
    }

    @Test
    fun threadedSearchGivesTheSameResult() = runTest {
        val groups = TagQuery.Groups(listOf(listOf("TS"), listOf("약피폐", "피폐")))
        val single = search(tagHttp()).fetchNovelsByTags(groups, delay = 0.0)
        val threaded = search(tagHttp()).fetchNovelsByTags(groups, delay = 0.0, threads = 3)
        assertEquals(single, threaded)
        assertEquals(listOf("2", "3"), threaded)
    }

    @Test
    fun allOfIntersectsAndAnyOfUnites() = runTest {
        val logs = mutableListOf<String>()
        assertEquals(listOf("4"), search(tagHttp(), logs).fetchNovelsByTags(TagQuery.AllOf(listOf("TS", "백합")), delay = 0.0))
        assertTrue(logs.contains("Searching for novels with tags: TS, 백합 (mode: AND, 1 thread(s))"))
        assertTrue(logs.contains("  AND intersection: 1 novel(s) match all 2 tag(s)"))

        val any = search(tagHttp()).fetchNovelsByTags(TagQuery.AnyOf(listOf("약피폐", "백합")), delay = 0.0)
        assertEquals(listOf("2", "3", "4", "5"), any)
        // One tag under AND is just that tag.
        assertEquals(listOf("2", "3"), search(tagHttp()).fetchNovelsByTags(TagQuery.AllOf(listOf("약피폐")), delay = 0.0))
    }

    @Test
    fun anApiErrorGivesAnEmptyTagAndTheSearchContinues() = runTest {
        val logs = mutableListOf<String>()
        val http = FakeHttp { request ->
            if (query(request)["search_val"] == "TS") 200 to """{"status":500,"errmsg":"busy"}""" else 200 to idsReply(7, 8)
        }
        val ids = search(http, logs).fetchNovelsByTags(TagQuery.AnyOf(listOf("TS", "백합")), delay = 0.0)
        assertEquals(listOf("7", "8"), ids)
        assertTrue(logs.contains("  [TS] API error: busy"))
    }

    @Test
    fun stopKeepsWhatWasFound() = runTest {
        val http = tagHttp()
        var stop = false
        val engine = NpiaTagSearch(http, null, { if (it.startsWith("  Group 1 result")) stop = true }, { stop })
        val ids = engine.fetchNovelsByTags(TagQuery.Groups(listOf(listOf("TS"), listOf("약피폐"))), delay = 0.0)
        // Group 1 finished, then the stop was noticed before group 2 was searched.
        assertEquals(listOf("1", "2", "3", "4"), ids)
        assertEquals(1, http.requests.size)
    }

    // Top 100

    @Test
    fun rankingIdsKeepTheFirstHundredUniqueIdsInOrder() {
        val html = (1..150).joinToString("") { """<a href="/novel/${1000 + it}">x</a><a href="/novel/${1000 + it}/">y</a>""" }
        val ids = NpiaTagSearch.rankingIds(html)
        assertEquals(100, ids.size)
        assertEquals((1001..1100).map { it.toString() }, ids)
        assertEquals(listOf("5", "3", "9"), NpiaTagSearch.rankingIds("/novel/5 /novel/3 /novel/5 /viewer/77 /novel/9 /novel/3"))
        assertTrue(NpiaTagSearch.rankingIds("no ids here").isEmpty())
    }

    @Test
    fun top100ReadsEachPeriodAndAudience() = runTest {
        val logs = mutableListOf<String>()
        val http = FakeHttp { request ->
            val url = request.url
            when {
                url.endsWith("/top100/all/weekly/view/adult/plus") -> 503 to ""
                url.contains("/top100/all/") -> 200 to """<a href="/novel/${if (url.contains("/adult/")) 2 else 1}">x</a><a href="/novel/7">y</a>"""
                else -> 404 to ""
            }
        }
        val rankings = search(http, logs).fetchTop100Rankings("")
        assertEquals(
            listOf("daily all", "weekly all", "monthly all", "daily adult", "weekly adult", "monthly adult", "daily teen", "weekly teen", "monthly teen"),
            rankings.keys.toList(),
        )
        assertEquals(listOf("1", "7"), rankings["daily all"])
        assertEquals(listOf("2", "7"), rankings["monthly adult"])
        assertEquals(emptyList<String>(), rankings["weekly adult"])
        assertTrue(logs.contains("  Fetching daily all..."))
        assertTrue(logs.contains("  daily all: 2 novels"))
        assertTrue(logs.contains("  weekly adult: HTTP 503"))
        assertEquals("https://${NpiaParser.HOST}/top100/all/today/view/all/plus", http.requests.first().url)

        http.requests.clear()
        assertEquals(listOf("daily adult", "weekly adult", "monthly adult"), search(http).fetchTop100Rankings("19").keys.toList())
        assertEquals(listOf("daily teen", "weekly teen", "monthly teen"), search(http).fetchTop100Rankings("15").keys.toList())
    }

    @Test
    fun top100StopsBetweenPages() = runTest {
        var stop = false
        val logs = mutableListOf<String>()
        val http = FakeHttp { 200 to """<a href="/novel/1">x</a>""" }
        val engine = NpiaTagSearch(http, null, { logs += it; if (it.endsWith("novels")) stop = true }, { stop })
        val rankings = engine.fetchTop100Rankings("")
        assertEquals(listOf("daily all"), rankings.keys.toList())
        assertEquals("  Stopped by user.", logs.last())
    }

    // Scrape all

    @Test
    fun scrapeAllUnitesQueriesAndAuthorCatalogs() = runTest {
        val logs = mutableListOf<String>()
        val http = FakeHttp { request ->
            val params = query(request)
            when {
                path(request) == "/proc/novel" -> when (params["search_val"]) {
                    "판타지" -> 200 to """{"status":200,"list":[{"novel_no":1,"novel_age":15,"writer_mem_no":"7"},{"novel_no":2,"novel_age":19,"writer_mem_no":"8"}]}"""
                    "현대" -> 200 to """{"status":200,"list":[{"novel_no":2,"novel_age":19,"mem_no":"8"},{"novel_no":3,"novel_age":15}]}"""
                    else -> 404 to ""
                }
                path(request) == "/proc/user" -> when (form(request)["mem_no"]) {
                    "7" -> 200 to """{"status":200,"result":{"allCount":2,"novel":[{"novel_no":"1","writer_mem_no":"7"},{"novel_no":"10","writer_mem_no":"7","novel_age":15}]}}"""
                    "8" -> 200 to """{"status":200,"result":{"allCount":1,"novel":[{"novel_no":"2","novel_age":19}]}}"""
                    else -> 404 to ""
                }
                else -> 404 to ""
            }
        }

        val ids = search(http, logs).fetchAllNovels(delay = 0.0, maxQueries = 2)

        assertEquals(listOf("1", "2", "3", "10"), ids)
        assertEquals("Scraping all novel IDs from Npia... (2 queries, 1 thread(s))", logs.first())
        assertTrue(logs.contains("  Query 1/2: searching '판타지'..."))
        assertTrue(logs.contains("    2 results, 2 new (total: 2)"))
        assertTrue(logs.contains("    2 results, 1 new (total: 3)"))
        assertTrue(logs.any { it.startsWith("Public intro default PARTIAL; discoveries retained: ValueError: public intro HTTP 404") })
        assertTrue(logs.contains("Public intro: 0 R19 novels discovered in 2 requests"))
        assertTrue(logs.contains("Checking 2 public author catalogs with 4 workers (1-2s random delay)..."))
        // The authors answer in a random order (each waits 1 to 2 seconds first), so only the counts are fixed.
        val authorLines = logs.filter { it.startsWith("  Author ") }
        assertEquals(2, authorLines.size)
        assertTrue(authorLines.toString(), authorLines.any { it.startsWith("  Author 7, page 1: ") })
        assertTrue(authorLines.toString(), authorLines.any { it.startsWith("  Author 8, page 1: ") })
        assertTrue(authorLines.toString(), authorLines.first().contains(": 1/2 complete, 4 IDs total"))
        assertTrue(authorLines.toString(), authorLines.last().contains(": 2/2 complete, 4 IDs total"))
        assertEquals("  4 novel(s) retrieved total", logs.last())

        // Two queries only: the first two terms, with the age parameter left empty.
        val searches = http.requests.filter { path(it) == "/proc/novel" }
        assertEquals(listOf("판타지", "현대"), searches.map { query(it)["search_val"] })
        assertEquals("all", query(searches[0])["search_type"])
        assertEquals("15000", query(searches[0])["rows"])
    }

    @Test
    fun scrapeAllOfAdultsSendsNoAgeParameterAndFiltersLocally() = runTest {
        val http = FakeHttp { request ->
            val params = query(request)
            when {
                path(request) == "/proc/novel" ->
                    if (params["search_val"] == "판타지") {
                        200 to """{"status":200,"list":[{"novel_no":1,"novel_age":15,"writer_mem_no":"7"},{"novel_no":2,"novel_age":19,"writer_mem_no":"8"},{"novel_no":4}]}"""
                    } else {
                        404 to ""
                    }
                path(request) == "/proc/user" -> when (form(request)["mem_no"]) {
                    "7" -> 200 to """{"status":200,"result":{"allCount":1,"novel":[{"novel_no":"1","novel_age":15}]}}"""
                    "8" -> 200 to """{"status":200,"result":{"allCount":1,"novel":[{"novel_no":"2","novel_age":19}]}}"""
                    else -> 404 to ""
                }
                else -> 404 to ""
            }
        }
        val logs = mutableListOf<String>()
        val ids = search(http, logs).fetchAllNovels(delay = 0.0, ageFilter = "19", maxQueries = 1)
        assertEquals(listOf("2"), ids)
        assertTrue(logs.contains("  Age filter: Adult only"))
        assertEquals("", query(http.requests.first { path(it) == "/proc/novel" })["novel_age"])
    }

    @Test
    fun aRetryableStatusIsRetriedWithBackoffThenGivenUp() = runTest {
        val logs = mutableListOf<String>()
        var calls = 0
        val http = FakeHttp { request ->
            if (path(request) == "/proc/novel") {
                calls++
                if (calls < 3) 503 to "" else 200 to """{"status":200,"list":[{"novel_no":5}]}"""
            } else {
                404 to ""
            }
        }
        val ids = search(http, logs).fetchAllNovels(delay = 0.0, maxQueries = 1)
        assertEquals(listOf("5"), ids)
        assertTrue(logs.contains("    Retry 1/4 for '판타지' p1 after HTTP 503; sleeping 1s"))
        assertTrue(logs.contains("    Retry 2/4 for '판타지' p1 after HTTP 503; sleeping 2s"))
        assertEquals(3, calls)

        val failing = FakeHttp { 503 to "" }
        val failLogs = mutableListOf<String>()
        search(failing, failLogs).fetchAllNovels(delay = 0.0, maxQueries = 1)
        assertEquals(4, failing.requests.count { path(it) == "/proc/novel" })
        assertTrue(failLogs.contains("    HTTP 503 on page 1, skipping"))
    }

    // Author catalog

    private fun catalogPage(total: Int, vararg rows: String) =
        """{"status":200,"result":{"allCount":$total,"novel":[${rows.joinToString(",")}]}}"""

    private fun row(id: Int, owner: String? = "7") = if (owner == null) """{"novel_no":"$id"}""" else """{"novel_no":"$id","writer_mem_no":"$owner"}"""

    private suspend fun failure(http: HttpFetching, author: String = "7", pageSize: Int = 2): String {
        try {
            NpiaAuthorCatalog(http, null).pages(author, delay = 0.0, pageSize = pageSize, retries = 1).toList()
        } catch (e: ValueError) {
            return e.message.orEmpty()
        }
        fail("expected a ValueError")
        return ""
    }

    @Test
    fun authorPagesAreRequestedUntilTheCatalogIsComplete() = runTest {
        val http = FakeHttp { request ->
            when (form(request)["paging[curPage]"]) {
                "1" -> 200 to catalogPage(3, row(1), row(2))
                else -> 200 to catalogPage(3, row(3))
            }
        }
        val pages = NpiaAuthorCatalog(http, "key").pages("7", delay = 0.0, pageSize = 2).toList()
        assertEquals(listOf(1, 2), pages.map { it.page })
        assertEquals(listOf(true, false), pages.map { it.more })
        assertEquals(listOf("1", "2", "3"), pages.flatMap { it.rows }.map { it["novel_no"].scalarText() })

        val first = http.requests.first()
        assertEquals("POST", first.method)
        assertEquals("/proc/user", path(first))
        val body = form(first)
        assertEquals("get_member_writer_novel", body["mode"])
        assertEquals("7", body["mem_no"])
        assertEquals("2", body["paging[rowCount]"])
        assertEquals("date", body["paging[order]"])
        assertEquals("1", body["paging[sort][date]"])
        assertEquals("LOGINKEY=key", first.headers["Cookie"])
    }

    @Test
    fun authorPageValidationRejectsWrongRepeatedAndIncompletePages() = runTest {
        // A record that belongs to another author.
        assertTrue(failure(FakeHttp { 200 to catalogPage(2, row(1), row(2, owner = "8")) }).startsWith("author catalog identity mismatch; HTTP 200"))
        // The same page again.
        val repeated = FakeHttp { 200 to catalogPage(4, row(1), row(2)) }
        assertTrue(failure(repeated).startsWith("author catalog repeated a page; HTTP 200"))
        // Fewer rows than the total promises, or none at all before the total is reached.
        assertTrue(failure(FakeHttp { 200 to catalogPage(3, row(1)) }).startsWith("author catalog returned an incomplete page"))
        assertTrue(failure(FakeHttp { 200 to catalogPage(5) }).startsWith("author catalog returned an incomplete page"))
        // Bad envelopes and records.
        assertTrue(failure(FakeHttp { 500 to "oops" }).startsWith("author catalog HTTP 500"))
        assertTrue(failure(FakeHttp { 200 to """{"status":500,"result":{}}""" }).startsWith("author catalog API error"))
        assertTrue(failure(FakeHttp { 200 to """{"status":200,"result":{"novel":"x","allCount":1}}""" }).startsWith("invalid author catalog response"))
        assertTrue(failure(FakeHttp { 200 to """{"status":200,"result":{"novel":[],"allCount":-1}}""" }).startsWith("invalid author catalog response"))
        assertTrue(failure(FakeHttp { 200 to catalogPage(1, """{"novel_no":"x1"}""") }).startsWith("invalid author catalog record"))
        // The author ID itself is validated before any request.
        val never = FakeHttp { error("no request expected") }
        assertEquals("invalid author ID", failure(never, author = "0"))
        assertEquals("invalid author ID", failure(never, author = "abc"))
        assertEquals("author page size must be between 1 and 100", failure(never, pageSize = 101))
        assertTrue(never.requests.isEmpty())
    }

    @Test
    fun theFailureLogRedactsSecretsAndKeepsTheBodyShort() = runTest {
        val logs = mutableListOf<String>()
        val http = FakeHttp { 200 to """{"status":500,"loginkey":"abc123secret","detail":"${"x".repeat(400)}"}""" }
        try {
            NpiaAuthorCatalog(http, null, { logs += it }).pages("7", delay = 0.0, retries = 1).toList()
            fail("expected a ValueError")
        } catch (e: ValueError) {
            assertFalse(e.message.orEmpty(), e.message.orEmpty().contains("abc123secret"))
            assertTrue(e.message.orEmpty(), e.message.orEmpty().contains("[redacted]"))
        }
        assertEquals(1, logs.size)
        assertTrue(logs[0], logs[0].startsWith("Author 7 page 1 attempt 1/1 failed: author catalog API error; HTTP 200;"))
        assertTrue(logs[0].length < 600)
    }

    @Test
    fun parallelAuthorsKeepRequestOrderAndPageOn() = runTest {
        val http = FakeHttp { request ->
            val body = form(request)
            when (body["mem_no"] to body["paging[curPage]"]) {
                "7" to "1" -> 200 to catalogPage(150, *(1..100).map { row(it) }.toTypedArray())
                "7" to "2" -> 200 to catalogPage(150, *(101..150).map { row(it) }.toTypedArray())
                "8" to "1" -> 200 to catalogPage(1, row(1001, owner = "8"))
                else -> 404 to ""
            }
        }
        val events = mutableListOf<AuthorEvent>()
        NpiaAuthorCatalog(http, null).parallel(listOf("7" to 1, "8" to 1), delay = 0.0, delayMax = 0.0) { events += it }
        assertEquals(listOf("7" to 1, "8" to 1, "7" to 2), events.map { it.author to it.page })
        assertEquals(listOf(true, false, false), events.map { it.more })
        assertTrue(events.all { it.error == null })
        assertEquals(100, events[0].rows!!.size)

        // A failing author is reported and does not stop the others.
        val broken = FakeHttp { request -> if (form(request)["mem_no"] == "7") 500 to "" else 200 to catalogPage(1, row(5, owner = "8")) }
        val seen = mutableListOf<AuthorEvent>()
        NpiaAuthorCatalog(broken, null).parallel(listOf("7" to 1, "8" to 1), retries = 1, delay = 0.0, delayMax = 0.0) { seen += it }
        assertEquals(2, seen.size)
        assertNull(seen.first { it.author == "8" }.error)
        assertTrue(seen.first { it.author == "7" }.error!!.message.orEmpty().startsWith("author catalog HTTP 500"))

        // The request budget stops the sweep.
        val budget = FakeHttp { 200 to catalogPage(1, row(5, owner = "8")) }
        val limited = mutableListOf<AuthorEvent>()
        NpiaAuthorCatalog(budget, null).parallel(listOf("8" to 1, "8" to 1, "8" to 1), workers = 1, maxRequests = 2, delay = 0.0, delayMax = 0.0) { limited += it }
        assertEquals(2, budget.requests.size)
    }

    // Intro discovery

    @Test
    fun introDiscoveryReadsAdultCardsAndTheirAuthors() = runTest {
        val logs = mutableListOf<String>()
        val card = """<div class="intro-item" data-novel_no="55" data-novel_age="19"><div class="intro-novel-info"><p>Writer <b>Kim</b></p></div><span class="novel-title"> The Title </span></div>""" +
            """<div class="intro-item" data-novel_no="56" data-novel_age="15"></div>"""
        var apiCalls = 0
        val http = FakeHttp { request ->
            when (path(request)) {
                "/intro" -> 200 to "<html><body>$card</body></html>"
                "/intro/megabox" -> 200 to "<html><body>$card</body></html>"
                "/novel/55" -> 200 to """<meta property="og:url" content="https://${NpiaParser.HOST}/novel/55"><div class="epnew-novel-info"><div class="in-writer"><a class="writer-name" href="/user/321/">Kim</a></div></div>"""
                "/proc/intro" ->
                    if (apiCalls++ == 0) {
                        200 to """{"result":"success","data":{"list":[{"novel_no":"57","novel_age":"19","mem_no":"9"},{"novel_no":"58","novel_age":"15"}]}}"""
                    } else {
                        200 to """{"result":"success","data":{"list":[]}}"""
                    }
                else -> 404 to ""
            }
        }
        val found = NpiaIntroDiscovery(http, null, log = { logs += it }).discover(maxRequests = 6, delay = 0.0)

        assertEquals(listOf("55", "57"), found.map { it["novel_no"].scalarText() })
        assertEquals("321", found[0]["mem_no"].scalarText())
        assertEquals("19", found[0]["novel_age"].scalarText())
        assertEquals("WriterKim", found[0]["writer_nick"].scalarText())
        assertEquals("The Title", found[0]["novel_name"].scalarText())
        assertEquals("9", authorId(found[1]))
        // /intro, its adult card's novel page, /intro/megabox (that card's author is known by now),
        // then the API: a page of new entries and an empty page that ends the walk.
        assertEquals(listOf("/intro", "/novel/55", "/intro/megabox", "/proc/intro", "/proc/intro"), http.requests.map { path(it) })
        val api = form(http.requests[3])
        assertEquals("ajax_intro_list", api["cmd"])
        assertEquals("2", api["page"])
        assertEquals("megabox", api["cate"])
        assertEquals("Public intro: 2 R19 novels discovered in 5 requests", logs.last())
    }

    @Test
    fun introDiscoveryKeepsWhatItFoundWhenAPageFails() = runTest {
        val logs = mutableListOf<String>()
        val http = FakeHttp { 500 to "" }
        val found = NpiaIntroDiscovery(http, null, log = { logs += it }).discover(delay = 0.0)
        assertTrue(found.isEmpty())
        assertTrue(logs[0], logs[0].startsWith("Public intro default PARTIAL; discoveries retained: ValueError: public intro HTTP 500"))
        assertTrue(logs[1], logs[1].startsWith("Public intro PARTIAL; discoveries retained: ValueError: public intro HTTP 500"))
        assertEquals("Public intro: 0 R19 novels discovered in 2 requests", logs[2])
    }

    // Helpers

    @Test
    fun authorIdAndAgeRulesMatchTheDesktopHelpers() {
        fun obj(text: String) = kotlinx.serialization.json.Json.parseToJsonElement(text) as kotlinx.serialization.json.JsonObject
        assertEquals("12", authorId(obj("""{"writer_mem_no":"12"}""")))
        assertEquals("5", authorId(obj("""{"writer_mem_no":"0","mem_no":5}""")))
        assertEquals("9", authorId(obj("""{"writer_mem_no":"x","mem_no":null,"author_id":"9"}""")))
        assertNull(authorId(obj("""{"mem_no":"0"}""")))

        assertTrue(matchesAge(obj("""{"novel_age":19}"""), "19"))
        assertFalse(matchesAge(obj("""{"novel_age":15}"""), "19"))
        assertTrue(matchesAge(obj("""{"novel_age":"15"}"""), "15"))
        assertFalse(matchesAge(obj("""{"novel_age":19}"""), "15"))
        assertTrue(matchesAge(obj("""{"novel_age":19}"""), ""))
        // Missing counts as 0; unreadable passes only without a filter.
        assertTrue(matchesAge(obj("""{}"""), "15"))
        assertFalse(matchesAge(obj("""{}"""), "19"))
        assertTrue(matchesAge(obj("""{"novel_age":null}"""), ""))
        assertFalse(matchesAge(obj("""{"novel_age":"adult"}"""), "15"))
    }
}
