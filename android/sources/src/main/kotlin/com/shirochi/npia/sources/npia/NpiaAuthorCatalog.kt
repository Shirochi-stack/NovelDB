package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.selects.select
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.random.Random

/** One validated page of an author's public catalog. */
internal data class AuthorPage(val page: Int, val rows: List<JsonObject>, val more: Boolean)

/** A page or an error for one author, in the order the pages were requested (`error` set means the page failed). */
internal data class AuthorEvent(
    val author: String,
    val page: Int,
    val rows: List<JsonObject>?,
    val more: Boolean,
    val error: Exception?,
    val attempts: Int,
)

/**
 * The public author catalogs, ported from `npia_author_catalog.py`: `/proc/user` pages of an
 * author's novels, validated so a wrong, repeated or cut-off page is an error instead of silently
 * missing ids.
 */
internal class NpiaAuthorCatalog(
    private val http: HttpFetching,
    private val loginKey: String?,
    private val log: (String) -> Unit = {},
) {
    /**
     * Yields validated pages one at a time; callers keep each page before asking for the next.
     * `signatures` remembers the novel ids of every page already seen for this author.
     */
    fun pages(
        author: String,
        startPage: Int = 1,
        maxPages: Int? = null,
        delay: Double = 1.0,
        delayMax: Double? = null,
        stopped: () -> Boolean = { false },
        pageSize: Int = 20,
        retries: Int = 4,
        acquireRequest: () -> Boolean = { true },
        signatures: MutableSet<List<String>> = mutableSetOf(),
        /** Cancels the request in flight; `stopped` also covers the request budget, which must not cancel the request that used it up. */
        abortRequest: () -> Boolean = stopped,
    ): Flow<AuthorPage> = flow {
        if (!author.isAsciiDigits() || author.all { it == '0' }) throw ValueError("invalid author ID")
        if (pageSize !in 1..100) throw ValueError("author page size must be between 1 and 100")
        val high = delayMax ?: delay
        if (delay < 0 || high < delay || retries < 1) throw ValueError("invalid author delay range or retry count")
        var page = startPage
        var requestsMade = 0
        while (maxPages == null || requestsMade < maxPages) {
            var lastError: ValueError? = null
            var fetched: AuthorPage? = null
            var aborted = false
            for (attempt in 1..retries) {
                sleepUnlessStopped(if (high > delay) Random.nextDouble(delay, high) else delay, stopped)
                if (stopped() || !acquireRequest()) {
                    aborted = true
                    break
                }
                var response: HttpResponse? = null
                try {
                    val body = NpiaAuth.formBody(
                        listOf(
                            "mode" to "get_member_writer_novel", "mem_no" to author,
                            "paging[rowCount]" to pageSize.toString(), "paging[curPage]" to page.toString(),
                            "paging[order]" to "date", "paging[sort][date]" to "1",
                        ),
                    )
                    val request = HttpRequest(AUTHOR_URL, "POST", NpiaAuth.headers(loginKey, form = true), body)
                    val reply = abortable(abortRequest) { http.send(request) }
                    if (reply == null) {
                        aborted = true
                        break
                    }
                    response = reply
                    fetched = validate(reply, author, page, pageSize, signatures)
                    break
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    // Transport errors can contain credential-bearing URLs, so only their type is logged.
                    val reason = if (response != null) (e.message ?: errorName(e)) else errorName(e)
                    val details = if (response != null) responseDetails(response) else "no response details"
                    val error = ValueError("$reason; $details")
                    lastError = error
                    log("Author $author page $page attempt $attempt/$retries failed: ${error.message}")
                    if (attempt == retries) throw error
                }
            }
            if (aborted) {
                lastError?.let { throw it }
                return@flow
            }
            val result = fetched ?: return@flow
            requestsMade++
            emit(result)
            if (!result.more) return@flow
            page++
        }
    }

    private fun validate(
        response: HttpResponse,
        author: String,
        page: Int,
        pageSize: Int,
        signatures: MutableSet<List<String>>,
    ): AuthorPage {
        if (response.status != 200 || response.wasRedirected(AUTHOR_URL)) throw ValueError("author catalog HTTP ${response.status}")
        val data = jsonOf(response.text) as? JsonObject
        if (data == null || data["status"].scalarText() != "200") throw ValueError("author catalog API error")
        val result = data["result"] as? JsonObject ?: JsonObject(emptyMap())
        val rows = result["novel"] as? JsonArray
        val totalText = (result["allCount"] as? JsonPrimitive)?.takeIf { !it.isString }?.content
        val total = totalText?.toIntOrNull()
        if (rows == null || total == null || total < 0) throw ValueError("invalid author catalog response")
        val records = rows.map { row ->
            val record = row as? JsonObject
            if (record == null || !record["novel_no"].scalarText().isAsciiDigits()) throw ValueError("invalid author catalog record")
            val owner = authorId(record)
            if (owner != null && owner != author) throw ValueError("author catalog identity mismatch")
            record
        }
        val signature = records.map { it["novel_no"].scalarText() }
        if (records.isNotEmpty() && signature in signatures) throw ValueError("author catalog repeated a page")
        if (records.isEmpty() && (page - 1) * pageSize < total) throw ValueError("author catalog returned an incomplete page")
        val more = page * pageSize < total
        val expected = minOf(pageSize, maxOf(0, total - (page - 1) * pageSize))
        if (records.size < expected) throw ValueError("author catalog returned an incomplete page")
        signatures.add(signature)
        return AuthorPage(page, records, more)
    }

    private fun jsonOf(text: String): JsonElement? =
        runCatching { kotlinx.serialization.json.Json.parseToJsonElement(text) }.getOrNull()

    /**
     * Fetches the catalogs of many authors with `workers` pages in flight (at most one per author)
     * and calls `onEvent` for each page or error on the caller's coroutine, in request order, so
     * only the caller touches its results. `authors` maps each author to the page to start at.
     */
    suspend fun parallel(
        authors: List<Pair<String, Int>>,
        workers: Int = 4,
        delay: Double = 1.0,
        delayMax: Double = 2.0,
        retries: Int = 4,
        pageSize: Int = 100,
        maxRequests: Int? = null,
        stopped: () -> Boolean = { false },
        onEvent: suspend (AuthorEvent) -> Unit,
    ) {
        if (workers < 1) throw ValueError("author workers must be positive")
        val queue = ArrayDeque(authors)
        val signatures = HashMap<String, MutableSet<List<String>>>()
        val lock = Any()
        var requestsMade = 0
        val closing = AtomicBoolean(false)

        fun exhausted() = closing.get() || stopped() || (maxRequests != null && requestsMade >= maxRequests)

        suspend fun fetch(owner: String, page: Int): AuthorEvent {
            var attempts = 0
            val acquire = {
                synchronized(lock) {
                    if (exhausted()) {
                        false
                    } else {
                        attempts++
                        requestsMade++
                        true
                    }
                }
            }
            return try {
                val first = pages(
                    owner, startPage = page, maxPages = 1, delay = delay, delayMax = delayMax, retries = retries,
                    pageSize = pageSize, stopped = { exhausted() }, acquireRequest = acquire,
                    abortRequest = { closing.get() || stopped() },
                    signatures = synchronized(lock) { signatures.getOrPut(owner) { mutableSetOf() } },
                ).firstOrNull()
                if (first == null) AuthorEvent(owner, page, null, false, null, attempts)
                else AuthorEvent(owner, first.page, first.rows, first.more, null, attempts)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                AuthorEvent(owner, page, null, false, e, attempts)
            }
        }

        coroutineScope {
            val running = LinkedHashMap<Deferred<AuthorEvent>, Int>()
            var sequence = 0
            try {
                while (running.isNotEmpty() || (queue.isNotEmpty() && !exhausted())) {
                    while (queue.isNotEmpty() && running.size < workers && !exhausted()) {
                        val (owner, page) = queue.removeFirst()
                        synchronized(lock) { signatures.getOrPut(owner) { mutableSetOf() } }
                        running[async { fetch(owner, page) }] = sequence++
                    }
                    if (running.isEmpty()) break
                    select<Unit> { running.keys.forEach { deferred -> deferred.onAwait { } } }
                    for (deferred in running.keys.filter { it.isCompleted }) {
                        running.remove(deferred)
                        val event = deferred.await()
                        if (event.rows != null && event.more) {
                            queue.addFirst(event.author to event.page + 1)
                        } else {
                            synchronized(lock) { signatures.remove(event.author) }
                        }
                        onEvent(event)
                    }
                }
            } finally {
                closing.set(true)
                running.keys.forEach { it.cancel() }
            }
        }
    }

    companion object {
        val AUTHOR_URL = "https://${NpiaParser.HOST}/proc/user"
    }
}
