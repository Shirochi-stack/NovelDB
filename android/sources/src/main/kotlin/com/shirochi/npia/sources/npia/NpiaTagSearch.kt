package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject

/** What a tag search asks for. */
sealed interface TagQuery {
    /** Tags within a group are OR'd and the groups are AND'd: `[["TS"], ["a", "b"]]` is TS AND (a OR b). */
    data class Groups(val groups: List<List<String>>) : TagQuery

    /** Novels that have every tag. */
    data class AllOf(val tags: List<String>) : TagQuery

    /** Novels that have any of the tags. */
    data class AnyOf(val tags: List<String>) : TagQuery
}

/**
 * The npia novel-id searches of the desktop downloader (`DownloaderCore.fetch_novels_by_tags`,
 * `fetch_all_novels` and `fetch_top100_rankings`), over `HttpFetching`. Every method logs the same
 * lines as the desktop and checks `stopped` between requests; a stop also cancels the request in
 * flight and returns what was collected so far.
 */
class NpiaTagSearch(
    private val http: HttpFetching,
    private val loginKey: String?,
    private val log: (String) -> Unit = {},
    private val stopped: () -> Boolean = { false },
) {
    private val searchHeaders = NpiaAuth.headers(loginKey, referer = "https://${NpiaParser.HOST}/search")

    // Tag search

    /**
     * Novel ids for a tag query, one `novel_genre` search per tag (up to 50000 results each).
     * `delay` is the pause between requests in seconds, `threads` how many run at once.
     */
    suspend fun fetchNovelsByTags(query: TagQuery, delay: Double = 0.5, ageFilter: String = "", threads: Int = 1): List<String> {
        when (query) {
            is TagQuery.Groups -> {
                log("Searching with ${query.groups.size} group(s), $threads thread(s):")
                query.groups.forEachIndexed { index, group ->
                    log("  Group ${index + 1}: ${group.joinToString(" OR ")}")
                    if (index < query.groups.size - 1) log("    AND")
                }
            }
            is TagQuery.AllOf -> log("Searching for novels with tags: ${query.tags.joinToString(", ")} (mode: AND, $threads thread(s))")
            is TagQuery.AnyOf -> log("Searching for novels with tags: ${query.tags.joinToString(", ")} (mode: OR, $threads thread(s))")
        }
        logAgeFilter(ageFilter)

        val resultIds: List<String>
        when (query) {
            is TagQuery.Groups -> {
                // Each group ORs its tags; the groups are then AND'd.
                var resultSet: LinkedHashSet<String>? = null
                for ((index, group) in query.groups.withIndex()) {
                    if (stopped()) break
                    val groupIds = LinkedHashSet<String>()
                    fetchTags(group, delay, ageFilter, threads).forEach { groupIds.addAll(it) }
                    log("  Group ${index + 1} result: ${groupIds.size} novel(s)")
                    val so = resultSet
                    if (so == null) {
                        resultSet = groupIds
                    } else {
                        so.retainAll(groupIds)
                        log("  After AND with group ${index + 1}: ${so.size} novel(s)")
                    }
                }
                resultIds = resultSet.orEmpty().toList()
            }
            is TagQuery.AllOf -> {
                if (query.tags.size > 1) {
                    var intersection: LinkedHashSet<String>? = null
                    for (ids in fetchTags(query.tags, delay, ageFilter, threads)) {
                        val so = intersection
                        if (so == null) intersection = LinkedHashSet(ids) else so.retainAll(ids)
                    }
                    resultIds = intersection.orEmpty().toList()
                    log("  AND intersection: ${resultIds.size} novel(s) match all ${query.tags.size} tag(s)")
                } else {
                    resultIds = union(fetchTags(query.tags, delay, ageFilter, threads))
                }
            }
            is TagQuery.AnyOf -> resultIds = union(fetchTags(query.tags, delay, ageFilter, threads))
        }
        log("Tag search complete: ${resultIds.size} novel(s) found.")
        return resultIds
    }

    private fun union(sets: List<Set<String>>): List<String> = LinkedHashSet<String>().also { all -> sets.forEach { all.addAll(it) } }.toList()

    /** Searches each tag, in order, and returns the id sets in the same order (shorter when stopped). */
    private suspend fun fetchTags(tags: List<String>, delay: Double, ageFilter: String, threads: Int): List<Set<String>> {
        if (threads <= 1) {
            val results = mutableListOf<Set<String>>()
            for (tag in tags) {
                if (stopped()) break
                results.add(fetchTag(tag, ageFilter))
                if (delay > 0) sleepUnlessStopped(delay, stopped)
            }
            return results
        }
        return coroutineScope {
            val permits = Semaphore(threads)
            val pending = mutableListOf<Deferred<Set<String>>>()
            for (tag in tags) {
                if (stopped()) break
                pending.add(async { permits.withPermit { fetchTag(tag, ageFilter) } })
                sleepUnlessStopped(delay, stopped)
            }
            pending.awaitAll()
        }
    }

    /** All novel ids of one tag in a single API call. */
    private suspend fun fetchTag(tag: String, ageFilter: String): Set<String> {
        if (stopped()) return emptySet()
        val params = listOf(
            "cmd" to "novel_search", "search_type" to "novel_genre", "search_val" to tag,
            "page" to "1", "rows" to "50000", "novel_type" to "", "start_count_book" to "", "end_count_book" to "",
            "novel_age" to ageFilter, "start_days" to "", "sort_col" to "last_viewdate", "novel_genre" to "",
            "block_out" to "0", "block_stop" to "0", "is_contest" to "0", "is_complete" to "", "is_challenge" to "0",
            "list_display" to "grid",
        )
        val response = withElapsedTicks(stopped, log) { abortable(stopped) { search(params, 180) } } ?: return emptySet()
        val data = response.jsonObjectOrFail()
        if (!data.statusIs200()) {
            log("  [$tag] API error: ${data.errorMessage()}")
            return emptySet()
        }
        val ids = LinkedHashSet<String>()
        for (novel in data.listOrEmpty()) {
            val id = (novel as? JsonObject)?.get("novel_no").scalarText()
            if (id.isNotEmpty()) ids.add(id)
        }
        log("  [$tag] ${ids.size} novel(s)")
        return ids
    }

    // Scrape all

    /**
     * Every novel id the site will list: a search sweep over the first `maxQueries` terms of
     * `NpiaSearchTerms.ALL_SEARCH_TERMS` (the API caps one query, so the results are unioned),
     * then the public intro catalog and the public catalog of every author seen.
     */
    suspend fun fetchAllNovels(
        delay: Double = 0.5,
        ageFilter: String = "",
        maxQueries: Int = NpiaSearchTerms.DEFAULT_SEARCH_QUERY_COUNT,
        threads: Int = 1,
    ): List<String> {
        val count = maxQueries.coerceIn(1, NpiaSearchTerms.DEFAULT_SEARCH_QUERY_COUNT)
        val terms = NpiaSearchTerms.ALL_SEARCH_TERMS.take(count)
        log("Scraping all novel IDs from Npia... (${terms.size} queries, $threads thread(s))")
        logAgeFilter(ageFilter)

        val ids = LinkedHashSet<String>()
        val authors = LinkedHashSet<String>()
        val lock = Any()

        if (threads > 1) {
            coroutineScope {
                val permits = Semaphore(threads)
                val pending = mutableListOf<Deferred<Unit>>()
                for ((index, term) in terms.withIndex()) {
                    if (stopped()) break
                    pending.add(async { permits.withPermit { searchTerm(index, term, terms.size, ageFilter, ids, authors, lock) } })
                    sleepUnlessStopped(delay, stopped)
                }
                pending.awaitAll()
            }
        } else {
            for ((index, term) in terms.withIndex()) {
                if (stopped()) {
                    log("  Stopped by user.")
                    break
                }
                searchTerm(index, term, terms.size, ageFilter, ids, authors, lock)
                sleepUnlessStopped(delay, stopped)
            }
        }

        val intro = NpiaIntroDiscovery(http, loginKey, stopped, log)
        for (novel in intro.discover(maxRequests = 6, delay = maxOf(2.0, delay))) {
            authorId(novel)?.let { authors.add(it) }
            if (matchesAge(novel, ageFilter)) ids.add(novel["novel_no"].scalarText())
        }

        log("Checking ${authors.size} public author catalogs with 4 workers (1-2s random delay)...")
        var completedAuthors = 0
        NpiaAuthorCatalog(http, loginKey, log).parallel(
            authors = authors.sortedBy { it.toBigInteger() }.map { it to 1 },
            workers = 4, delay = 1.0, delayMax = 2.0, retries = 4, pageSize = 100, stopped = stopped,
        ) { event ->
            if (event.error != null) {
                log("  Author ${event.author} PARTIAL: completed IDs retained; ${event.error.message}")
                return@parallel
            }
            val rows = event.rows ?: return@parallel
            for (novel in rows) {
                if (matchesAge(novel, ageFilter)) ids.add(novel["novel_no"].scalarText())
            }
            if (!event.more) completedAuthors++
            log("  Author ${event.author}, page ${event.page}: $completedAuthors/${authors.size} complete, ${ids.size} IDs total")
        }

        log("  ${ids.size} novel(s) retrieved total")
        return ids.toList()
    }

    /** One search term: every page of results, merged into `ids` and `authors` once the term is done. */
    private suspend fun searchTerm(
        index: Int,
        term: String,
        total: Int,
        ageFilter: String,
        ids: MutableSet<String>,
        authors: MutableSet<String>,
        lock: Any,
    ) {
        if (stopped()) return
        log("  Query ${index + 1}/$total: searching '$term'...")
        try {
            val termIds = LinkedHashSet<String>()
            val termAuthors = LinkedHashSet<String>()
            var results = 0
            withElapsedTicks(stopped, log) {
                for (page in 1..MAX_PAGES) {  // keeps the 600,000-result ceiling
                    val params = listOf(
                        "cmd" to "novel_search", "search_type" to "all", "search_val" to term,
                        "page" to page.toString(), "rows" to ROWS_PER_PAGE.toString(), "novel_type" to "",
                        "start_count_book" to "", "end_count_book" to "",
                        "novel_age" to (if (ageFilter == "19") "" else ageFilter),
                        "start_days" to "", "sort_col" to "last_viewdate", "novel_genre" to "",
                        "block_out" to "0", "block_stop" to "0", "is_contest" to "0", "is_complete" to "",
                        "is_challenge" to "0", "list_display" to "grid",
                    )
                    var response: HttpResponse? = null
                    var lastError: String? = null
                    for (attempt in 1..4) {
                        if (stopped()) break
                        try {
                            response = abortable(stopped) { search(params, 240) }
                            if (response == null) break
                            if (response.status !in NpiaSearchTerms.RETRYABLE_STATUS_CODES) break
                            lastError = "HTTP ${response.status}"
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            lastError = e.message ?: errorName(e)
                            response = null
                        }
                        if (attempt < 4 && !stopped()) {
                            val wait = minOf(1 shl (attempt - 1), 8)
                            log("    Retry $attempt/4 for '$term' p$page after $lastError; sleeping ${wait}s")
                            sleepUnlessStopped(wait.toDouble(), stopped)
                        }
                    }

                    val reply = response
                    if (reply == null) {
                        if (!stopped()) log("    Failed page $page for '$term': $lastError")
                        break
                    }
                    if (reply.status != 200) {
                        log("    HTTP ${reply.status} on page $page, skipping")
                        break
                    }
                    val text = reply.text.trim()
                    if (text.isEmpty()) {
                        log("    Empty response on page $page, skipping")
                        break
                    }
                    val data = try {
                        reply.jsonObjectOrFail()
                    } catch (e: ValueError) {
                        log("    Invalid JSON for '$term' p$page (len=${text.length}): ${text.take(300)}")
                        break
                    }
                    if (!data.statusIs200()) {
                        log("    API error p$page: ${data.errorMessage()}")
                        break
                    }
                    val batch = data.listOrEmpty()
                    results += batch.size
                    for (element in batch) {
                        val novel = element as? JsonObject ?: continue
                        authorId(novel)?.let { termAuthors.add(it) }
                        val id = novel["novel_no"].scalarText()
                        if (id.isNotEmpty() && matchesAge(novel, ageFilter)) termIds.add(id)
                    }
                    // A short page is the last one.
                    if (batch.size < ROWS_PER_PAGE) break
                }
            }
            synchronized(lock) {
                val before = ids.size
                ids.addAll(termIds)
                authors.addAll(termAuthors)
                log("    $results results, ${ids.size - before} new (total: ${ids.size})")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("    Error on '$term': ${e.message ?: errorName(e)}")
        }
    }

    // Top 100

    /**
     * The first 100 unique novel ids of each public ranking page. `ageFilter` "19" reads only the
     * adult rankings, "15" only the teen ones, anything else all three audiences. Keys are labels
     * such as "daily all"; a ranking that could not be read is an empty list.
     */
    suspend fun fetchTop100Rankings(ageFilter: String = ""): Map<String, List<String>> {
        val audiences = when (ageFilter) {
            "19" -> listOf("adult/plus" to "adult")
            "15" -> listOf("teen/plus" to "teen")
            else -> listOf("all/plus" to "all", "adult/plus" to "adult", "teen/plus" to "teen")
        }
        val periods = listOf("today" to "daily", "weekly" to "weekly", "month" to "monthly")
        val results = LinkedHashMap<String, List<String>>()
        for ((audienceUrl, audienceLabel) in audiences) {
            for ((periodUrl, periodLabel) in periods) {
                if (stopped()) {
                    log("  Stopped by user.")
                    return results
                }
                val label = "$periodLabel $audienceLabel"
                val url = "https://${NpiaParser.HOST}/top100/all/$periodUrl/view/$audienceUrl"
                log("  Fetching $label...")
                try {
                    val request = HttpRequest(url, "GET", NpiaAuth.headers(loginKey), null, timeoutSeconds = 30)
                    val response = abortable(stopped) { http.send(request) }
                    if (response == null) {
                        log("  Stopped by user.")
                        return results
                    }
                    if (response.status != 200) {
                        log("  $label: HTTP ${response.status}")
                        results[label] = emptyList()
                    } else {
                        val ids = rankingIds(response.text)
                        results[label] = ids
                        log("  $label: ${ids.size} novels")
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    log("  $label: Error - ${e.message ?: errorName(e)}")
                    results[label] = emptyList()
                }
                sleepUnlessStopped(0.3, stopped)
            }
        }
        return results
    }

    // Requests

    private suspend fun search(params: List<Pair<String, String>>, timeoutSeconds: Long): HttpResponse {
        val query = NpiaAuth.formBody(params).decodeToString()
        return http.send(HttpRequest("$SEARCH_URL?$query", "GET", searchHeaders, null, timeoutSeconds = timeoutSeconds))
    }

    private fun logAgeFilter(ageFilter: String) {
        when (ageFilter) {
            "15" -> log("  Age filter: Non-adult only")
            "19" -> log("  Age filter: Adult only")
        }
    }

    private fun JsonObject.errorMessage(): String = this["errmsg"].scalarText().ifEmpty { "Unknown" }

    private fun JsonObject.listOrEmpty(): List<kotlinx.serialization.json.JsonElement> = (this["list"] as? JsonArray) ?: emptyList()

    companion object {
        val SEARCH_URL = "https://${NpiaParser.HOST}/proc/novel"
        private const val ROWS_PER_PAGE = 15000
        private const val MAX_PAGES = 40

        private val NOVEL_LINK = Regex("/novel/(\\d+)")

        /** The first 100 unique `/novel/<id>` ids of a ranking page, in page order. */
        fun rankingIds(html: String): List<String> =
            NOVEL_LINK.findAll(html).map { it.groupValues[1] }.distinct().take(100).toList()

        /** The page of a novel on the main site, for adding found ids to the link cart. */
        fun novelUrl(id: String): String = NpiaParser.novelUrl(id)
    }
}
